"""Party enrichment: fetch public sources and match declarants to a party.

Sources
-------
- Assemblée Nationale:  NosDéputés ``/deputes/json`` (legislature 16 snapshot;
  the open data stream for legislature 17 is not currently published).
- Sénat:                ``data.senat.fr`` ODSEN_GENERAL.csv (filter État=ACTIF).
- Parlement Européen:   ``europarl.europa.eu/meps/en/full-list/xml``
  filter country=France.

Matching
--------
For each fetched person (``PersonParty``) we find the declarant row whose
``person_key(nom, prenom)`` matches. Where ``date_naissance`` is present
on both sides we require it to match too. A fuzzy last-name fallback
(difflib ratio ≥ 0.9) catches minor spelling variants.
"""

from __future__ import annotations

import csv
import io
import logging
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from typing import Iterable

import httpx
from lxml import etree

from parsers.normalize import person_key, strip_accents

log = logging.getLogger(__name__)

NOSDEPUTES_URL = "https://www.nosdeputes.fr/deputes/json"
SENAT_URL = "https://data.senat.fr/data/senateurs/ODSEN_GENERAL.csv"
EUROPARL_URL = "https://www.europarl.europa.eu/meps/en/full-list/xml"


# slug → (label, color). This is the "canonical" palette. Raw party labels
# coming from sources are normalised to a slug via _label_to_slug().
PALETTE: dict[str, tuple[str, str]] = {
    "rn": ("Rassemblement National", "#0d378a"),
    "lfi": ("La France Insoumise", "#cc2229"),
    "renaissance": ("Renaissance", "#ffc832"),
    "lr": ("Les Républicains", "#0066cc"),
    "ps": ("Parti Socialiste", "#ff8080"),
    "eelv": ("Les Écologistes", "#77b82a"),
    "horizons": ("Horizons", "#00a4a6"),
    "modem": ("MoDem", "#ff8000"),
    "udi": ("UDI", "#1e90ff"),
    "pcf": ("PCF", "#dd0000"),
    "rec": ("Reconquête", "#1c1c4f"),
    "liot": ("LIOT", "#9b59b6"),
    "none": ("Non affilié", "#999999"),
}

# Heuristics mapping free-text labels → our slug. Keys are lowercased,
# accent-folded substrings; first match wins.
_LABEL_RULES: list[tuple[str, str]] = [
    ("rassemblement national", "rn"),
    ("rn", "rn"),
    ("france insoumise", "lfi"),
    ("lfi", "lfi"),
    ("nupes", "lfi"),
    ("renaissance", "renaissance"),
    ("ren", "renaissance"),      # AN group sigle
    ("rdpi", "renaissance"),     # Sénat group dominated by macronists
    ("ensemble", "renaissance"),
    ("republicains", "lr"),
    ("lr", "lr"),
    ("socialiste", "ps"),
    ("ser", "ps"),
    ("soc", "ps"),               # AN group sigle
    ("ps", "ps"),
    ("ecologiste", "eelv"),
    ("ecolo", "eelv"),
    ("eco", "eelv"),             # AN group sigle
    ("eelv", "eelv"),
    ("gest", "eelv"),
    ("verts", "eelv"),
    ("horizons", "horizons"),
    ("hor", "horizons"),         # AN group sigle
    ("modem", "modem"),
    ("democrate", "modem"),
    ("udi", "udi"),
    ("union centriste", "udi"),
    ("uc", "udi"),
    ("communiste", "pcf"),
    ("pcf", "pcf"),
    ("gdr", "pcf"),              # Gauche Démocrate et Républicaine
    ("crce", "pcf"),
    ("reconquete", "rec"),
    ("liot", "liot"),
    ("libertes", "liot"),
    ("independants", "liot"),
    ("rdse", "liot"),
]


@dataclass
class PersonParty:
    nom: str
    prenom: str
    date_naissance: str | None
    party_label: str
    source: str  # 'nosdeputes' | 'senat' | 'europarl'


# ---------------------------------------------------------------------------
# Label → slug
# ---------------------------------------------------------------------------


def _label_to_slug(label: str) -> str:
    folded = strip_accents(label).lower()
    for needle, slug in _LABEL_RULES:
        if needle in folded:
            return slug
    return "none"


# ---------------------------------------------------------------------------
# Fetchers
# ---------------------------------------------------------------------------


def fetch_an(client: httpx.Client | None = None) -> list[PersonParty]:
    c = client or httpx.Client(timeout=30.0, follow_redirects=True)
    try:
        data = c.get(NOSDEPUTES_URL).json()
    finally:
        if client is None:
            c.close()
    out: list[PersonParty] = []
    for wrap in data.get("deputes", []):
        d = wrap.get("depute", {})
        label = d.get("groupe_sigle") or d.get("parti_ratt_financier")
        if not label:
            continue
        out.append(
            PersonParty(
                nom=d.get("nom_de_famille", "").strip(),
                prenom=d.get("prenom", "").strip(),
                date_naissance=d.get("date_naissance") or None,
                party_label=label,
                source="nosdeputes",
            )
        )
    return out


def fetch_senat(client: httpx.Client | None = None) -> list[PersonParty]:
    c = client or httpx.Client(timeout=30.0, follow_redirects=True)
    try:
        body = c.get(SENAT_URL).content
    finally:
        if client is None:
            c.close()
    text = "\n".join(
        l for l in body.decode("latin-1").splitlines() if not l.startswith("%")
    )
    reader = csv.DictReader(io.StringIO(text))
    out: list[PersonParty] = []
    for row in reader:
        if row.get("État") != "ACTIF":
            continue
        dn = _parse_senat_date(row.get("Date naissance"))
        label = (row.get("Groupe politique") or "").strip()
        if not label:
            continue
        out.append(
            PersonParty(
                nom=row.get("Nom usuel", "").strip(),
                prenom=row.get("Prénom usuel", "").strip(),
                date_naissance=dn,
                party_label=label,
                source="senat",
            )
        )
    return out


def fetch_europarl_fr(client: httpx.Client | None = None) -> list[PersonParty]:
    c = client or httpx.Client(timeout=30.0, follow_redirects=True)
    try:
        body = c.get(EUROPARL_URL).content
    finally:
        if client is None:
            c.close()
    root = etree.fromstring(body)
    out: list[PersonParty] = []
    for mep in root.findall("mep"):
        country = (mep.findtext("country") or "").strip()
        if country != "France":
            continue
        full = (mep.findtext("fullName") or "").strip()
        prenom, nom = _split_mep_name(full)
        label = (mep.findtext("nationalPoliticalGroup") or "").strip()
        if not label or not nom:
            continue
        out.append(
            PersonParty(
                nom=nom,
                prenom=prenom,
                date_naissance=None,  # not exposed in this feed
                party_label=label,
                source="europarl",
            )
        )
    return out


def _split_mep_name(full: str) -> tuple[str, str]:
    """MEP full names have the last name in UPPER CASE.

    'Grégory ALLIONE' → ('Grégory', 'ALLIONE')
    """
    parts = full.split()
    last = [p for p in parts if p.isupper() or (p.upper() == p and any(c.isalpha() for c in p))]
    first = [p for p in parts if p not in last]
    return (" ".join(first), " ".join(last))


def _parse_senat_date(s: str | None) -> str | None:
    if not s:
        return None
    try:
        return datetime.strptime(s.split()[0], "%Y-%m-%d").date().isoformat()
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Party table + matcher
# ---------------------------------------------------------------------------


def upsert_palette(conn: sqlite3.Connection) -> dict[str, int]:
    """Ensure all palette rows exist; return slug → party.id."""
    cur = conn.cursor()
    for slug, (label, color) in PALETTE.items():
        cur.execute(
            """INSERT INTO party(slug, label, color) VALUES (?, ?, ?)
               ON CONFLICT(slug) DO UPDATE SET label=excluded.label, color=excluded.color""",
            (slug, label, color),
        )
    rows = cur.execute("SELECT id, slug FROM party").fetchall()
    return {r["slug"]: r["id"] for r in rows}


def match(
    conn: sqlite3.Connection, people: Iterable[PersonParty]
) -> dict[str, int]:
    """Populate declarant.party_id / declarant.party_source from *people*.

    Strategy per person:
      1. exact: person_key + date_naissance match
      2. no-birth fallback: person_key match, single candidate
      3. fuzzy fallback: same prenom_key, last-name similarity ≥ 0.9

    Returns counts of match outcomes.
    """
    slugs_to_id = upsert_palette(conn)
    stats = {"total": 0, "matched": 0, "matched_exact": 0, "matched_fuzzy": 0}

    declarants = conn.execute(
        "SELECT id, nom, prenom, date_naissance FROM declarant"
    ).fetchall()
    # Build in-memory index: person_key → list of declarant rows
    by_key: dict[str, list[sqlite3.Row]] = {}
    by_prenom: dict[str, list[sqlite3.Row]] = {}
    for row in declarants:
        k = person_key(row["nom"], row["prenom"])
        by_key.setdefault(k, []).append(row)
        pk = strip_accents(row["prenom"] or "").lower().strip()
        by_prenom.setdefault(pk, []).append(row)

    cur = conn.cursor()
    for p in people:
        stats["total"] += 1
        key = person_key(p.nom, p.prenom)
        candidates = by_key.get(key, [])

        match_row: sqlite3.Row | None = None
        fuzzy = False

        if candidates:
            if p.date_naissance:
                for c in candidates:
                    if c["date_naissance"] == p.date_naissance:
                        match_row = c
                        break
            elif len(candidates) == 1:
                match_row = candidates[0]
            # Ambiguous (≥2 candidates, no birthdate): deliberately skip.
        else:
            # No exact key hit → try fuzzy last-name within same prenom.
            pk = strip_accents(p.prenom).lower().strip()
            nk = strip_accents(p.nom).lower().strip()
            for c in by_prenom.get(pk, []):
                c_nk = strip_accents(c["nom"]).lower().strip()
                if SequenceMatcher(None, nk, c_nk).ratio() >= 0.9:
                    match_row = c
                    fuzzy = True
                    break

        if match_row is None:
            continue

        slug = _label_to_slug(p.party_label)
        cur.execute(
            "UPDATE declarant SET party_id = ?, party_source = ? WHERE id = ?",
            (slugs_to_id[slug], p.source, match_row["id"]),
        )
        stats["matched"] += 1
        if fuzzy:
            stats["matched_fuzzy"] += 1
        else:
            stats["matched_exact"] += 1

    return stats


# ---------------------------------------------------------------------------
# CLI-callable command
# ---------------------------------------------------------------------------


def cmd_parties(conn: sqlite3.Connection) -> dict[str, int]:
    """Fetch all three sources and apply the matcher. Returns aggregate stats."""
    with httpx.Client(timeout=30.0, follow_redirects=True) as client:
        an = fetch_an(client)
        senat = fetch_senat(client)
        meps = fetch_europarl_fr(client)
    log.info("fetched: AN=%d Sénat=%d MEP=%d", len(an), len(senat), len(meps))

    # reset before re-applying
    conn.execute("UPDATE declarant SET party_id = NULL, party_source = NULL")
    all_people = [*an, *senat, *meps]
    stats = match(conn, all_people)
    stats.update(
        {"fetched_an": len(an), "fetched_senat": len(senat), "fetched_europarl": len(meps)}
    )
    return stats
