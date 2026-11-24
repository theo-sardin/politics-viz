"""HATVP lobbying registry (« répertoire des représentants d'intérêts »).

The export is one ~140 MB JSON object. It is decoded one organisation at a
time so memory stays flat whatever the file size.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

_PARL = "député, sénateur"
_LAW = "lois"
_AMOUNT = re.compile(r"(\d[\d\s  ]*)\s*euros?")


@dataclass
class LobbyYear:
    year: int
    spend_min: int | None
    spend_max: int | None
    staff: float | None
    n_actions: int


@dataclass
class LobbyAction:
    year: int
    subject: str
    domains: list[str]
    targets_parl: bool
    on_law: bool


@dataclass
class LobbyOrg:
    national_id: str
    name: str
    aliases: list[str]
    category: str | None
    website: str | None
    sector_labels: list[str]
    active: bool
    years: list[LobbyYear] = field(default_factory=list)
    actions: list[LobbyAction] = field(default_factory=list)


def parse_spend(band: str | None) -> tuple[int | None, int | None]:
    """'> = 1 250 000 euros et < 1 500 000 euros' → (1250000, 1500000)."""
    if not band:
        return None, None
    amounts = [int(re.sub(r"\D", "", m)) for m in _AMOUNT.findall(band)]
    if not amounts:
        return None, None
    if len(amounts) >= 2:
        return amounts[0], amounts[1]
    return (0, amounts[0]) if band.strip().startswith("<") else (amounts[0], None)


def iter_publications(path: str | Path) -> Iterator[dict]:
    """Yield each element of the top-level ``publications`` array."""
    decoder = json.JSONDecoder()
    with open(path, encoding="utf-8") as f:
        buf = f.read(1 << 20)
        while '"publications"' not in buf or "[" not in buf[buf.index('"publications"') :]:
            more = f.read(1 << 20)
            if not more:
                return
            buf += more
        buf = buf[buf.index("[", buf.index('"publications"')) + 1 :]
        while True:
            buf = buf.lstrip(" \t\r\n,")
            if buf.startswith("]"):
                return
            try:
                obj, end = decoder.raw_decode(buf)
            except json.JSONDecodeError:
                more = f.read(1 << 22)
                if not more:
                    return
                buf += more
                continue
            yield obj
            buf = buf[end:]


def read_orgs(path: str | Path) -> Iterator[LobbyOrg]:
    for p in iter_publications(path):
        national_id = str(p.get("identifiantNational") or "").strip()
        name = (p.get("denomination") or "").strip()
        if not national_id or not name:
            continue
        aliases = [a.strip() for a in (p.get("nomUsageHatvp"), p.get("nomUsage"), p.get("sigleHatvp")) if a and a.strip()]
        org = LobbyOrg(
            national_id=national_id,
            name=name,
            aliases=aliases,
            category=((p.get("categorieOrganisation") or {}).get("label") or "").strip() or None,
            website=(p.get("lienSiteWeb") or "").strip() or None,
            sector_labels=[s.get("label") for s in (p.get("activites") or {}).get("listSecteursActivites") or [] if s.get("label")],
            active=not p.get("dateCessation"),
        )
        for ex in p.get("exercices") or []:
            pc = ex.get("publicationCourante") or {}
            start = pc.get("dateDebut") or ""
            if len(start) < 4 or not start[-4:].isdigit():
                continue
            year = int(start[-4:])
            actions = pc.get("activites") or []
            lo, hi = parse_spend(pc.get("montantDepense"))
            if lo is not None or hi is not None or actions:
                org.years.append(LobbyYear(year, lo, hi, pc.get("nombreSalaries"), len(actions)))
            for a in actions:
                pa = a.get("publicationCourante") or {}
                subject = re.sub(r"\s+", " ", pa.get("objet") or "").strip()
                if not subject:
                    continue
                targets, decisions = [], []
                for ar in pa.get("actionsRepresentationInteret") or []:
                    targets += ar.get("reponsablesPublics") or []
                    decisions += ar.get("decisionsConcernees") or []
                org.actions.append(
                    LobbyAction(
                        year=year,
                        subject=subject,
                        domains=list(pa.get("domainesIntervention") or []),
                        targets_parl=any(t.lower().startswith(_PARL) for t in targets),
                        on_law=any(d.lower().startswith(_LAW) for d in decisions),
                    )
                )
        yield org
