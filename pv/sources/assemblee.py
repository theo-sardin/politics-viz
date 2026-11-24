"""Assemblée nationale open data: actors, bodies, recusals, roll calls, dossiers.

All four archives are zips of one JSON document per entity. They are read
straight from the zip; nothing is extracted to disk.
"""

from __future__ import annotations

import json
import re
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from pv.db import ABSTENTION, CONTRE, NON_VOTANT, POUR
from pv.text import fold, strip_html

# ---------------------------------------------------------------------------
# Actors, bodies, recusals (AMO archive)
# ---------------------------------------------------------------------------


@dataclass
class Mandat:
    organe_uid: str
    kind: str  # typeOrgane: ASSEMBLEE, GP, COMPER, GOUVERNEMENT…
    legislature: int | None
    start: str | None
    end: str | None
    role: str | None
    departement: str | None = None
    circo: str | None = None
    seat: int | None = None


@dataclass
class Acteur:
    uid: str
    civilite: str | None
    prenom: str
    nom: str
    birth_date: str | None
    profession: str | None
    hatvp_slug: str | None
    mandats: list[Mandat] = field(default_factory=list)


@dataclass
class Organe:
    uid: str
    kind: str
    label: str
    short: str | None
    legislature: int | None
    ended: bool


@dataclass
class Deport:
    uid: str
    acteur_uid: str
    legislature: int | None
    date: str | None
    scope: str | None
    target_kind: str | None
    target: str | None
    reason: str | None


def _nn(v):
    """The archives encode null both as JSON null and as an xsi:nil object."""
    if v is None or isinstance(v, dict):
        return None
    v = str(v).strip()
    return v or None


def _as_list(v) -> list:
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def _int(v) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _members(zf: zipfile.ZipFile, folder: str) -> Iterator[dict]:
    marker = f"/{folder}/"
    for name in zf.namelist():
        if marker in name and name.endswith(".json"):
            yield json.loads(zf.read(name))


def read_acteurs(path: str | Path) -> Iterator[Acteur]:
    with zipfile.ZipFile(path) as zf:
        for doc in _members(zf, "acteur"):
            a = doc["acteur"]
            uid = a["uid"]["#text"] if isinstance(a["uid"], dict) else a["uid"]
            ident = a["etatCivil"]["ident"]
            prof = a.get("profession") or {}
            uri = _nn(a.get("uri_hatvp"))
            acteur = Acteur(
                uid=uid,
                civilite=_nn(ident.get("civ")),
                prenom=_nn(ident.get("prenom")) or "",
                nom=_nn(ident.get("nom")) or "",
                birth_date=_nn((a["etatCivil"].get("infoNaissance") or {}).get("dateNais")),
                profession=_clean_profession(_nn(prof.get("libelleCourant"))),
                hatvp_slug=uri.rstrip("/").rsplit("/", 1)[-1] if uri else None,
            )
            for m in _as_list((a.get("mandats") or {}).get("mandat")):
                organe = (m.get("organes") or {}).get("organeRef")
                if isinstance(organe, list):
                    organe = organe[0] if organe else None
                if not organe:
                    continue
                lieu = (m.get("election") or {}).get("lieu") or {}
                dept = _nn(lieu.get("departement"))
                num = _nn(lieu.get("numDepartement"))
                acteur.mandats.append(
                    Mandat(
                        organe_uid=organe,
                        kind=m.get("typeOrgane") or "",
                        legislature=_int(m.get("legislature")),
                        start=_nn(m.get("dateDebut")),
                        end=_nn(m.get("dateFin")),
                        role=_nn((m.get("infosQualite") or {}).get("libQualite")),
                        departement=f"{dept} ({num})" if dept and num else dept,
                        circo=_nn(lieu.get("numCirco")),
                        seat=_int((m.get("mandature") or {}).get("placeHemicycle")),
                    )
                )
            yield acteur


def _clean_profession(s: str | None) -> str | None:
    if not s:
        return None
    s = re.sub(r"^\(\d+\)\s*-\s*", "", s).strip()
    return s or None


def read_organes(path: str | Path) -> Iterator[Organe]:
    with zipfile.ZipFile(path) as zf:
        for doc in _members(zf, "organe"):
            o = doc["organe"]
            kind = o.get("codeType") or ""
            if kind == "CIRCONSCRIPTION":
                continue
            yield Organe(
                uid=o["uid"],
                kind=kind,
                label=_nn(o.get("libelle")) or "",
                short=_nn(o.get("libelleAbrev")),
                legislature=_int(o.get("legislature")),
                ended=_nn((o.get("viMoDe") or {}).get("dateFin")) is not None,
            )


def read_deports(path: str | Path) -> Iterator[Deport]:
    with zipfile.ZipFile(path) as zf:
        for doc in _members(zf, "deport"):
            d = doc["deport"]
            cible = d.get("cible") or {}
            yield Deport(
                uid=d["uid"],
                acteur_uid=d["refActeur"],
                legislature=_int(d.get("legislature")),
                date=(_nn(d.get("dateCreation")) or "")[:10] or None,
                scope=_nn((d.get("portee") or {}).get("libelle")),
                target_kind=_nn((cible.get("type") or {}).get("libelle")),
                target=strip_html(_nn(cible.get("referenceTextuelle"))),
                reason=strip_html(_nn(d.get("explication"))),
            )


# ---------------------------------------------------------------------------
# Roll-call votes
# ---------------------------------------------------------------------------

_POSITIONS = (("pours", POUR), ("contres", CONTRE), ("abstentions", ABSTENTION), ("nonVotants", NON_VOTANT))
_MAJORITY = {"pour": POUR, "contre": CONTRE, "abstention": ABSTENTION}

# Group discipline only means something for a real group voting as a block.
_DISSENT_MIN_VOTERS = 5
_DISSENT_MIN_SHARE = 0.75


@dataclass
class Scrutin:
    uid: str
    legislature: int
    numero: int
    date: str
    title: str
    vote_type: str | None
    outcome: str | None
    n_pour: int
    n_contre: int
    n_abstention: int
    dossier_uid: str | None
    votes: list[tuple[str, int, int]]  # (acteur uid, position, dissent)


def read_scrutins(path: str | Path) -> Iterator[Scrutin]:
    with zipfile.ZipFile(path) as zf:
        for name in zf.namelist():
            if not name.endswith(".json"):
                continue
            s = json.loads(zf.read(name))["scrutin"]
            synth = (s.get("syntheseVote") or {}).get("decompte") or {}
            dossier = (s.get("objet") or {}).get("dossierLegislatif") or {}
            votes: list[tuple[str, int, int]] = []
            groupes = ((s.get("ventilationVotes") or {}).get("organe") or {}).get("groupes") or {}
            for g in _as_list(groupes.get("groupe")):
                v = g.get("vote") or {}
                voix = v.get("decompteVoix") or {}
                counts = {
                    POUR: _int(voix.get("pour")) or 0,
                    CONTRE: _int(voix.get("contre")) or 0,
                    ABSTENTION: _int(voix.get("abstentions")) or 0,
                }
                majority = _MAJORITY.get(v.get("positionMajoritaire") or "")
                expressed = sum(counts.values())
                disciplined = (
                    majority is not None
                    and expressed >= _DISSENT_MIN_VOTERS
                    and counts[majority] / expressed >= _DISSENT_MIN_SHARE
                )
                nominatif = v.get("decompteNominatif") or {}
                for key, pos in _POSITIONS:
                    block = nominatif.get(key)
                    if not isinstance(block, dict):
                        continue
                    for votant in _as_list(block.get("votant")):
                        ref = votant.get("acteurRef")
                        if not ref:
                            continue
                        dissent = int(disciplined and pos != NON_VOTANT and pos != majority)
                        votes.append((ref, pos, dissent))
            yield Scrutin(
                uid=s["uid"],
                legislature=int(s["legislature"]),
                numero=int(s["numero"]),
                date=s["dateScrutin"],
                title=re.sub(r"\s+", " ", s.get("titre") or "").strip(),
                vote_type=_nn((s.get("typeVote") or {}).get("codeTypeVote")),
                outcome=_nn((s.get("sort") or {}).get("code")),
                n_pour=_int(synth.get("pour")) or 0,
                n_contre=_int(synth.get("contre")) or 0,
                n_abstention=_int(synth.get("abstentions")) or 0,
                dossier_uid=_nn(dossier.get("dossierRef")) if isinstance(dossier, dict) else None,
                votes=votes,
            )


_BILL_RE = re.compile(
    r"\b((?:projet|proposition) de (?:loi|r[ée]solution)\b.*)$",
    re.IGNORECASE,
)
_STAGE_PAREN = re.compile(
    r"\s*\(\s*([^()]*(?:lecture|commission mixte|d[ée]lib[ée]ration|texte de la)[^()]*)\)", re.IGNORECASE
)
_SENATE_NOISE = re.compile(
    r",?\s*(?:adopt[ée]e?|modifi[ée]e?|rejet[ée]e?)\s+par\s+(?:le S[ée]nat|l'Assembl[ée]e nationale)"
    r"(?:\s+(?:en|apr[èe]s)\s+[^,]*)?,?\s*",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ParsedTitle:
    kind: str  # amendement | article | ensemble | motion | autre
    bill_title: str | None
    stage: str | None

    @property
    def bill_key(self) -> str | None:
        return fold(self.bill_title) if self.bill_title else None


def parse_title(title: str) -> ParsedTitle:
    """Split a roll-call title into what was voted, on which bill, at which stage."""
    t = title.replace("’", "'").strip()
    head = fold(t[:60])
    if head.startswith(("l amendement", "le sous amendement", "les amendements", "l s amendement")):
        kind = "amendement"
    elif head.startswith(("l article", "les articles", "les credits", "la premiere partie", "la seconde partie")):
        kind = "article"
    elif head.startswith("l ensemble"):
        kind = "ensemble"
    elif head.startswith(("la motion", "la demande de renvoi")):
        kind = "motion"
    else:
        kind = "autre"

    m = _BILL_RE.search(t)
    if not m:
        return ParsedTitle(kind, None, None)
    bill = m.group(1).rstrip(". ")
    # Everything from the reading marker on is not part of the bill's name:
    # « … pour 2024 (première lecture) - Mission Défense ».
    stage = None
    sm = _STAGE_PAREN.search(bill)
    if sm:
        inner = sm.group(1).strip()
        if "lecture" in inner.lower() or "commission mixte" in inner.lower():
            stage = inner
        bill = bill[: sm.start()].rstrip(". ")
    bill = _SENATE_NOISE.sub(" ", bill)
    bill = re.sub(r"\s+", " ", bill).strip(" ,.")
    if len(bill) < 20:
        return ParsedTitle(kind, None, stage)
    return ParsedTitle(kind, bill[0].upper() + bill[1:], stage)


# ---------------------------------------------------------------------------
# Legislative dossiers
# ---------------------------------------------------------------------------


@dataclass
class Dossier:
    uid: str
    legislature: int | None
    title: str
    url_path: str | None
    procedure: str | None
    committee_uid: str | None
    rapporteurs: list[str]
    auteurs: list[str]


def read_dossiers(path: str | Path) -> Iterator[Dossier]:
    with zipfile.ZipFile(path) as zf:
        for doc in _members(zf, "dossierParlementaire"):
            d = doc["dossierParlementaire"]
            titre = d.get("titreDossier") or {}
            title = _nn(titre.get("titre"))
            if not title:
                continue
            rapporteurs: list[str] = []
            committee: list[str] = []
            _walk_actes(d.get("actesLegislatifs"), rapporteurs, committee)
            init = (d.get("initiateur") or {}).get("acteurs") or {}
            auteurs = [a["acteurRef"] for a in _as_list(init.get("acteur")) if a.get("acteurRef")]
            yield Dossier(
                uid=d["uid"],
                legislature=_int(d.get("legislature")),
                title=title,
                url_path=_nn(titre.get("titreChemin")),
                procedure=_nn((d.get("procedureParlementaire") or {}).get("libelle")),
                committee_uid=committee[0] if committee else None,
                rapporteurs=list(dict.fromkeys(rapporteurs)),
                auteurs=list(dict.fromkeys(auteurs)),
            )


def _walk_actes(node, rapporteurs: list[str], committee: list[str]) -> None:
    for acte in _as_list((node or {}).get("acteLegislatif")):
        code = acte.get("codeActe") or ""
        if code.startswith("AN"):
            if code.endswith("COM-FOND") and acte.get("organeRef"):
                committee.append(acte["organeRef"])
            for r in _as_list((acte.get("rapporteurs") or {}).get("rapporteur")):
                if r.get("acteurRef"):
                    rapporteurs.append(r["acteurRef"])
        _walk_actes(acte.get("actesLegislatifs"), rapporteurs, committee)
