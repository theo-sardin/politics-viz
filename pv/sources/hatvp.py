"""HATVP open data: the merged declarations XML and the declarations index.

The XML is streamed with ``iterparse`` so the ~85 MB file never sits in
memory. Every section of a declaration is parsed — financial stakes, but
also paid activities, board seats, the spouse's employer and parliamentary
staff — because the links worth surfacing are rarely in the stakes alone.
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree

from pv.text import clean, fold, name_key, parse_date, parse_float, parse_int

# Placeholder answers typed into free-text fields.
_EMPTY = re.compile(
    r"^(0|x+|nc|n a|na|ras|neant|aucun|aucune|aucuns|sans|sans objet|non|rien|nul|non concerne"
    r"|sans emploi|sans profession|sans activite|employeur neant|aucun autre employeur"
    r"|aucune a ma connaissance|pas d activite|inactif|inactive|au foyer|mere au foyer|pere au foyer)$"
)

MANDATE_KINDS = {
    "depute": "depute",
    "senateur": "senateur",
    "gouvernement": "gouvernement",
    "europe": "europe",
    "cr": "region",
    "region": "region",
    "departement": "departement",
    "commune": "commune",
    "epci": "epci",
    "ctsp": "ctsp",
}


def meaningful(s: str | None) -> str | None:
    """Cleaned text, or None when the field is empty or a placeholder."""
    s = clean(s)
    if not s:
        return None
    if _EMPTY.match(fold(s)):
        return None
    return s


@dataclass
class Holding:
    kind: str  # participation | non_cotee | sci | compte_titres
    raw_name: str | None
    value_eur: int | None
    shares: int | None = None
    capital_pct: float | None = None
    income: str | None = None
    comment: str | None = None


@dataclass
class Interest:
    kind: str  # activite | conseil | dirigeant | conjoint | collaborateur | benevole | mandat
    org: str | None
    description: str | None = None
    person_name: str | None = None
    start: str | None = None
    end: str | None = None
    kept: bool | None = None
    pay: list[tuple[int, int]] = field(default_factory=list)
    pay_basis: str | None = None


@dataclass
class Asset:
    kind: str
    label: str | None
    value_eur: int | None
    detail: str | None = None


@dataclass
class Declaration:
    uuid: str
    doc_type: str
    is_modification: bool
    filed_at: str
    civilite: str | None
    nom: str
    prenom: str
    birth_date: str | None
    mandate_kind: str
    mandate_label: str | None
    mandate_start: str | None
    no_holdings: bool
    note: str | None
    holdings: list[Holding] = field(default_factory=list)
    interests: list[Interest] = field(default_factory=list)
    assets: list[Asset] = field(default_factory=list)

    @property
    def doc_code(self) -> str:
        """Code used by the HATVP index: di, dim, dia, diam, dsp, dspm…"""
        code = self.doc_type.lower()
        if self.is_modification and not code.endswith("m"):
            code += "m"
        return code


def parse(path: str | Path) -> Iterator[Declaration]:
    """Yield one :class:`Declaration` per ``<declaration>`` element."""
    for _, elem in etree.iterparse(str(path), events=("end",), tag="declaration"):
        rec = _parse_declaration(elem)
        if rec is not None:
            yield rec
        elem.clear()
        while elem.getprevious() is not None:
            del elem.getparent()[0]


def _parse_declaration(el) -> Declaration | None:
    uuid = _t(el, "uuid")
    general = el.find("general")
    if not uuid or general is None:
        return None
    declarant = general.find("declarant")
    nom = clean(_t(declarant, "nom"))
    prenom = clean(_t(declarant, "prenom"))
    filed = parse_date(_t(el, "dateDepot"))
    if not nom or not prenom or not filed:
        return None

    code = (_t(general, "qualiteMandat/codTypeMandatFichier") or "").strip().lower()
    pf = el.find("participationFinanciereDto")
    notes = [clean(_t(i, "contenu")) for i in _items(el, "observationInteretDto")]
    rec = Declaration(
        uuid=uuid.strip(),
        doc_type=(_t(general, "typeDeclaration/id") or "DI").strip().upper(),
        is_modification=(_t(general, "declarationModificative") or "").strip() == "true",
        filed_at=filed,
        civilite=clean(_t(declarant, "civilite")),
        nom=nom,
        prenom=prenom,
        birth_date=_plausible_birth(parse_date(_t(declarant, "dateNaissance"))),
        mandate_kind=MANDATE_KINDS.get(code, "autre"),
        mandate_label=_mandate_label(general),
        mandate_start=parse_date(_t(general, "dateDebutMandat")),
        no_holdings=pf is not None and (_t(pf, "neant") or "").strip() == "true",
        note=" ".join(n for n in notes if n and not _EMPTY.match(fold(n))) or None,
    )
    rec.holdings = list(_holdings(el))
    rec.interests = list(_interests(el))
    rec.assets = list(_assets(el))
    return rec


def _plausible_birth(iso: str | None) -> str | None:
    """Drop typos such as a birth year of 1795."""
    if not iso or len(iso) < 4:
        return None
    return iso if 1900 <= int(iso[:4]) <= 2010 else None


def _mandate_label(general) -> str | None:
    raw = clean(_t(general, "qualiteDeclarantForPDF"))
    if raw:
        return " · ".join(p.strip() for p in raw.split("/") if p.strip())
    return clean(_t(general, "qualiteMandat/typeMandat"))


# -- sections ---------------------------------------------------------------


def _holdings(el) -> Iterator[Holding]:
    for it in _items(el, "participationFinanciereDto"):
        yield Holding(
            kind="participation",
            raw_name=_raw(it, "nomSociete"),
            value_eur=parse_int(_t(it, "evaluation")),
            shares=parse_int(_t(it, "nombreParts")) or None,
            capital_pct=parse_float(_t(it, "capitalDetenu")) or None,
            income=meaningful(_t(it, "remuneration")),
            comment=clean(_t(it, "commentaire")),
        )
    # Wealth declarations (published for government members only).
    for it in _items(el, "valeursNonEnBourseDto"):
        yield Holding(
            kind="non_cotee",
            raw_name=_raw(it, "denomination"),
            value_eur=parse_int(_t(it, "valeurActuelle")),
            capital_pct=parse_float(_t(it, "participation")) or None,
            comment=_join(_t(it, "droitReel"), _t(it, "commentaire")),
        )
    for it in _items(el, "sciDto"):
        yield Holding(
            kind="sci",
            raw_name=("SCI " + (_t(it, "denomination") or "")).strip(),
            value_eur=parse_int(_t(it, "valeur")),
            capital_pct=parse_float(_t(it, "capitalDetenu")) or None,
            comment=_join(_t(it, "droitReel"), _t(it, "commentaire")),
        )
    for it in _items(el, "valeursEnBourseDto"):
        yield Holding(
            kind="compte_titres",
            raw_name=_join(_t(it, "naturePlacement"), _t(it, "etablissement"), sep=" — "),
            value_eur=parse_int(_t(it, "valeur")),
            comment=clean(_t(it, "commentaire")),
        )


def _interests(el) -> Iterator[Interest]:
    for it in _items(el, "activProfCinqDerniereDto"):
        org = meaningful(_t(it, "employeur"))
        desc = meaningful(_t(it, "description"))
        if org or desc:
            yield _paid("activite", it, org, desc)
    for it in _items(el, "activConsultantDto"):
        org = meaningful(_t(it, "nomEmployeur"))
        desc = meaningful(_t(it, "description"))
        if org or desc:
            yield _paid("conseil", it, org, desc)
    for it in _items(el, "participationDirigeantDto"):
        org = meaningful(_t(it, "nomSociete"))
        if org:
            yield _paid("dirigeant", it, org, meaningful(_t(it, "activite")))
    for it in _items(el, "mandatElectifDto"):
        desc = meaningful(_t(it, "descriptionMandat"))
        if desc:
            yield _paid("mandat", it, None, desc)
    for it in _items(el, "activProfConjointDto"):
        org = meaningful(_t(it, "employeurConjoint"))
        desc = meaningful(_t(it, "activiteProf"))
        if org or desc:
            yield Interest("conjoint", org, desc)
    for it in _items(el, "activCollaborateursDto"):
        name = clean(_t(it, "nom"))
        if name:
            yield Interest(
                "collaborateur",
                meaningful(_t(it, "employeur")),
                meaningful(_t(it, "descriptionActivite")),
                person_name=name,
            )
    for it in _items(el, "fonctionBenevoleDto"):
        org = meaningful(_t(it, "nomStructure"))
        if org:
            kept = _t(it, "conservee")
            yield Interest(
                "benevole",
                org,
                meaningful(_t(it, "descriptionActivite")),
                kept=None if kept is None else kept.strip() == "true",
            )


def _paid(kind: str, it, org: str | None, desc: str | None) -> Interest:
    kept = _t(it, "conservee")
    pay: list[tuple[int, int]] = []
    for m in it.iterfind("remuneration/montant/montant"):
        year, amount = parse_int(_t(m, "annee")), parse_int(_t(m, "montant"))
        if year and amount is not None and 1990 <= year <= 2100:
            pay.append((year, amount))
    return Interest(
        kind=kind,
        org=org,
        description=desc,
        start=parse_date(_t(it, "dateDebut")),
        end=parse_date(_t(it, "dateFin")),
        kept=None if kept is None else kept.strip() == "true",
        pay=sorted(pay),
        pay_basis=clean(_t(it, "remuneration/brutNet")),
    )


def _assets(el) -> Iterator[Asset]:
    for it in _items(el, "immeubleDto"):
        dept = re.search(r"Département : (\w+)", _t(it, "codePostal") or "")
        yield Asset(
            "immobilier",
            _join(_t(it, "nature"), f"dép. {dept.group(1)}" if dept else None, sep=", "),
            parse_int(_t(it, "valeurVenale")),
            _join(
                _t(it, "droitReel"),
                f"quote-part {_t(it, 'quotePart')} %" if _t(it, "quotePart") else None,
                f"acquis en {_t(it, 'dateAcquisition')}" if _t(it, "dateAcquisition") else None,
                _t(it, "regimeJuridique"),
                sep=" · ",
            ),
        )
    for it in _items(el, "comptesBancaireDto"):
        yield Asset(
            "compte",
            _join(_t(it, "typeCompte"), _t(it, "etablissement"), sep=" — "),
            parse_int(_t(it, "valeur")),
            clean(_t(it, "titulaire")),
        )
    for it in _items(el, "assuranceVieDto"):
        yield Asset(
            "assurance_vie",
            clean(_t(it, "etablissement")),
            parse_int(_t(it, "valeurRachat")),
            clean(_t(it, "souscripteur")),
        )
    for it in _items(el, "vehiculeDto"):
        yield Asset("vehicule", _join(_t(it, "marque"), _t(it, "anneeAchat")), parse_int(_t(it, "valeur")))
    for tag, kind in (("bienDiverDto", "bien_divers"), ("autreBienDto", "autre_bien")):
        for it in _items(el, tag):
            yield Asset(kind, clean(_t(it, "description")), parse_int(_t(it, "valeur")), clean(_t(it, "denomination")))
    for it in _items(el, "bienEtrangerDto"):
        yield Asset("etranger", clean(_t(it, "nature")), parse_int(_t(it, "valeur")))
    for it in _items(el, "fondDto"):
        yield Asset("fonds", _join(_t(it, "nature"), _t(it, "description"), sep=" — "), parse_int(_t(it, "valeur")))
    for it in _items(el, "passifDto"):
        amount = parse_int(_t(it, "restantDu"))
        yield Asset(
            "dette",
            _join(_t(it, "nature"), _t(it, "nomCreancier"), sep=" — "),
            -amount if amount else amount,
            clean(_t(it, "objetDette")),
        )


# -- low-level helpers ------------------------------------------------------


def _items(decl, wrapper: str):
    w = decl.find(wrapper)
    if w is None:
        return
    outer = w.find("items")
    if outer is None:
        return
    yield from outer.findall("items")


def _t(el, path: str) -> str | None:
    if el is None:
        return None
    child = el.find(path)
    if child is None or child.text is None:
        return None
    return child.text


def _raw(el, path: str) -> str | None:
    """Raw text with whitespace collapsed but the redaction marker intact."""
    s = _t(el, path)
    if s is None:
        return None
    s = re.sub(r"\s+", " ", s).strip()
    return s or None


def _join(*parts: str | None, sep: str = " ") -> str | None:
    kept = [c for c in (clean(p) for p in parts) if c]
    return sep.join(kept) or None


# ---------------------------------------------------------------------------
# Declarations index (liste.csv)
# ---------------------------------------------------------------------------


@dataclass
class IndexRow:
    civilite: str
    prenom: str
    nom: str
    mandate_kind: str
    role: str
    doc_code: str  # di, dim, dia, diam, dsp, dspm…
    departement: str
    published: str | None
    filed: str | None
    source_file: str | None  # base name, without extension
    hatvp_slug: str
    status: str
    origin_id: str  # Assemblée 'PA' number or Sénat matricule
    photo_url: str | None

    @property
    def key(self) -> str:
        return name_key(self.nom, self.prenom)


def parse_index(path: str | Path) -> list[IndexRow]:
    text = Path(path).read_text(encoding="utf-8-sig", errors="replace")
    out: list[IndexRow] = []
    for r in csv.DictReader(io.StringIO(text), delimiter=";"):
        slug = (r.get("url_dossier") or "").rstrip("/").rsplit("/", 1)[-1]
        nom, prenom = (r.get("nom") or "").strip(), (r.get("prenom") or "").strip()
        if not slug or not nom:
            continue
        fname = (r.get("open_data") or r.get("nom_fichier") or "").strip()
        out.append(
            IndexRow(
                civilite=(r.get("civilite") or "").strip(),
                prenom=prenom,
                nom=nom,
                mandate_kind=MANDATE_KINDS.get((r.get("type_mandat") or "").strip().lower(), "autre"),
                role=(r.get("qualite") or "").strip(),
                doc_code=(r.get("type_document") or "").strip().lower(),
                departement=(r.get("departement") or "").strip(),
                published=(r.get("date_publication") or "").strip() or None,
                filed=(r.get("date_depot") or "").strip() or None,
                source_file=fname.rsplit(".", 1)[0] if fname and fname != "dispense" else None,
                hatvp_slug=slug,
                status=(r.get("statut_publication") or "").strip(),
                origin_id=(r.get("id_origine") or "").strip(),
                photo_url=(r.get("url_photo") or "").strip() or None,
            )
        )
    return out
