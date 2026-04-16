"""Party enrichment: fetch public sources and match declarants to a party.

Sources
-------
- Assemblée Nationale:  data.assemblee-nationale.fr open data CSV (legislature 17).
  This is the authoritative roster of current deputies.
- Sénat:                ``data.senat.fr`` ODSEN_GENERAL.csv (filter État=ACTIF).
- Parlement Européen:   ``europarl.europa.eu/meps/en/full-list/xml``
  filter country=France.

Matching
--------
For each fetched person (``PersonParty``) we find the declarant row whose
``person_key(nom, prenom)`` matches. Where ``date_naissance`` is present
on both sides we require it to match too. A fuzzy last-name fallback
(difflib ratio ≥ 0.9) catches minor spelling variants.

For AN deputies with no matching declarant, a new declarant row is created
so the hemicycle shows the full 577-seat roster even before they file HATVP
declarations.
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

AN_OPENDATA_URL = (
    "https://data.assemblee-nationale.fr/static/openData/repository/17"
    "/amo/deputes_actifs_csv_opendata/liste_deputes_libre_office.csv"
)
SENAT_URL = "https://data.senat.fr/data/senateurs/ODSEN_GENERAL.csv"
EUROPARL_URL = "https://www.europarl.europa.eu/meps/en/full-list/xml"


# slug → (label, color). Includes both legislature-17 AN groups and
# legacy slugs used by Sénat / MEP data.
PALETTE: dict[str, tuple[str, str]] = {
    # Legislature 17 AN groups
    "rn": ("Rassemblement National", "#0d378a"),
    "lfi": ("La France Insoumise", "#cc2229"),
    "epr": ("Ensemble pour la République", "#ffc832"),
    "dr": ("Droite Républicaine", "#0066cc"),
    "ps": ("Parti Socialiste", "#ff8080"),
    "ecos": ("Écologiste et Social", "#77b82a"),
    "horizons": ("Horizons", "#00a4a6"),
    "dem": ("Les Démocrates", "#ff8000"),
    "udr": ("Union des Droites", "#1c3a5f"),
    "pcf": ("GDR", "#dd0000"),
    "liot": ("LIOT", "#9b59b6"),
    # Legacy slugs (Sénat / MEP / legislature 16)
    "renaissance": ("Renaissance", "#ffc832"),
    "lr": ("Les Républicains", "#0066cc"),
    "modem": ("MoDem", "#ff8000"),
    "eelv": ("Les Écologistes", "#77b82a"),
    "udi": ("UDI", "#1e90ff"),
    "rec": ("Reconquête", "#1c1c4f"),
    "none": ("Non inscrit", "#999999"),
}

# Direct mapping from AN open-data "Groupe politique (abrégé)" to slug.
_AN_GROUP_TO_SLUG: dict[str, str] = {
    "RN": "rn",
    "EPR": "epr",
    "LFI-NFP": "lfi",
    "SOC": "ps",
    "DR": "dr",
    "Dem": "dem",
    "HOR": "horizons",
    "EcoS": "ecos",
    "UDR": "udr",
    "GDR": "pcf",
    "NI": "none",
    "LIOT": "liot",
}

# Heuristics mapping free-text labels → our slug. Keys are lowercased,
# accent-folded substrings; first match wins. Used for Sénat / MEP sources.
_LABEL_RULES: list[tuple[str, str]] = [
    ("rassemblement national", "rn"),
    ("france insoumise", "lfi"),
    ("ensemble pour la rep", "epr"),
    ("renaissance", "epr"),
    ("rdpi", "epr"),
    ("republicains", "dr"),
    ("union centriste", "udi"),
    ("socialiste", "ps"),
    ("ser", "ps"),
    ("ecologiste", "ecos"),
    ("ecolo", "ecos"),
    ("gest", "ecos"),
    ("verts", "ecos"),
    ("horizons", "horizons"),
    ("democrate", "dem"),
    ("modem", "dem"),
    ("communiste", "pcf"),
    ("crce", "pcf"),
    ("reconquete", "rec"),
    ("libertes", "liot"),
    ("independants", "liot"),
    ("rdse", "liot"),
    # Short abbreviations last (they're substring-greedy)
    ("lfi", "lfi"),
    ("ren", "epr"),
    ("soc", "ps"),
    ("eco", "ecos"),
    ("eelv", "ecos"),
    ("hor", "horizons"),
    ("udi", "udi"),
    ("uc", "udi"),
    ("pcf", "pcf"),
    ("gdr", "pcf"),
    ("rn", "rn"),
    ("lr", "dr"),
    ("liot", "liot"),
]


@dataclass
class PersonParty:
    nom: str
    prenom: str
    date_naissance: str | None
    party_slug: str  # pre-resolved slug (for AN) or raw label (for others)
    source: str      # 'an_opendata' | 'senat' | 'europarl'
    slug_resolved: bool = False  # True if party_slug is already a final slug


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


def fetch_an_opendata(client: httpx.Client | None = None) -> list[PersonParty]:
    """Fetch current AN deputies from the Assemblée nationale open data CSV."""
    c = client or httpx.Client(timeout=30.0, follow_redirects=True)
    try:
        text = c.get(AN_OPENDATA_URL).text
    finally:
        if client is None:
            c.close()
    reader = csv.DictReader(io.StringIO(text))
    out: list[PersonParty] = []
    for row in reader:
        nom = (row.get("Nom") or "").strip()
        prenom = (row.get("Prénom") or "").strip()
        group = (row.get("Groupe politique (abrégé)") or "").strip()
        if not nom or not prenom:
            continue
        slug = _AN_GROUP_TO_SLUG.get(group, "none")
        out.append(
            PersonParty(
                nom=nom, prenom=prenom, date_naissance=None,
                party_slug=slug, source="an_opendata", slug_resolved=True,
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
                party_slug=label, source="senat",
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
                nom=nom, prenom=prenom, date_naissance=None,
                party_slug=label, source="europarl",
            )
        )
    return out


def _split_mep_name(full: str) -> tuple[str, str]:
    """MEP full names have the last name in UPPER CASE."""
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
    conn: sqlite3.Connection,
    people: Iterable[PersonParty],
    create_if_unmatched: bool = False,
) -> dict[str, int]:
    """Populate declarant.party_id / declarant.party_source from *people*.

    When *create_if_unmatched* is True, a new declarant row is created for
    people that don't match any existing row — this ensures the full roster
    appears in the hemicycle even without HATVP declarations.

    Returns counts of match outcomes.
    """
    slugs_to_id = upsert_palette(conn)
    stats = {"total": 0, "matched": 0, "matched_exact": 0, "matched_fuzzy": 0, "created": 0}

    declarants = conn.execute(
        "SELECT id, nom, prenom, date_naissance FROM declarant"
    ).fetchall()
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
        slug = p.party_slug if p.slug_resolved else _label_to_slug(p.party_slug)
        party_id = slugs_to_id.get(slug, slugs_to_id["none"])

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
            if match_row is None and len(candidates) == 1:
                match_row = candidates[0]
        else:
            pk = strip_accents(p.prenom).lower().strip()
            nk = strip_accents(p.nom).lower().strip()
            for c in by_prenom.get(pk, []):
                c_nk = strip_accents(c["nom"]).lower().strip()
                if SequenceMatcher(None, nk, c_nk).ratio() >= 0.9:
                    match_row = c
                    fuzzy = True
                    break

        if match_row is not None:
            cur.execute(
                "UPDATE declarant SET party_id = ?, party_source = ? WHERE id = ?",
                (party_id, p.source, match_row["id"]),
            )
            stats["matched"] += 1
            stats["matched_fuzzy" if fuzzy else "matched_exact"] += 1
        elif create_if_unmatched:
            cur.execute(
                "INSERT INTO declarant(nom, prenom, date_naissance, party_id, party_source) VALUES (?, ?, ?, ?, ?)",
                (p.nom, p.prenom, p.date_naissance, party_id, p.source),
            )
            stats["created"] += 1

    return stats


# ---------------------------------------------------------------------------
# CLI-callable command
# ---------------------------------------------------------------------------


def cmd_parties(conn: sqlite3.Connection) -> dict[str, int]:
    """Fetch all three sources and apply the matcher. Returns aggregate stats."""
    with httpx.Client(timeout=30.0, follow_redirects=True) as client:
        an = fetch_an_opendata(client)
        senat = fetch_senat(client)
        meps = fetch_europarl_fr(client)
    log.info("fetched: AN=%d Sénat=%d MEP=%d", len(an), len(senat), len(meps))

    # Reset before re-applying
    conn.execute("UPDATE declarant SET party_id = NULL, party_source = NULL")

    # AN deputies: create declarant rows for those not in HATVP data
    an_stats = match(conn, an, create_if_unmatched=True)
    # Sénat + MEPs: only enrich existing declarants
    other_stats = match(conn, [*senat, *meps])

    return {
        "fetched_an": len(an),
        "fetched_senat": len(senat),
        "fetched_europarl": len(meps),
        "an_matched": an_stats["matched"],
        "an_created": an_stats["created"],
        "other_matched": other_stats["matched"],
    }
