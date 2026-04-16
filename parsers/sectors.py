"""Keyword-based sector tagger for vote titles and company names.

Tags each vote title with 0+ economic sectors and a direction (pro/anti/
neutral). Also maps company names to sectors.

This is a v1 heuristic. An LLM-based classifier can be layered on top
for votes that land in 'neutral' or unclassified.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field

from parsers.normalize import strip_accents


@dataclass
class SectorDef:
    slug: str
    label: str
    title_keywords: list[str]
    company_keywords: list[str] = field(default_factory=list)
    manual_companies: list[str] = field(default_factory=list)


# Direction keywords shared across all sectors.
PRO_KEYWORDS = [
    "accelerat", "soutien", "developp", "favoris", "alleg", "simplif",
    "exoner", "subvention", "aide", "credit d'impot", "credit d impot",
    "promot", "incitat", "alloc", "facilit",
]
ANTI_KEYWORDS = [
    "regulat", "taxe", "controle", "encadr", "sanction", "interdic",
    "limit", "redevance", "contribut", "plafon", "restriction",
    "moratoire", "gel", "suppress",
]

SECTORS: list[SectorDef] = [
    SectorDef("energy", "Énergie", [
        "energie", "nucleaire", "renouvelable", "petrole", "gaz", "electricite",
        "carburant", "hydrocarbure", "photovoltaique", "eolien", "hydrogene",
    ], ["energie", "petrol", "gaz", "electri", "carburant", "solaire"],
       ["total-energies", "totalenergies", "edf", "engie"]),

    SectorDef("banking", "Banque & Finance", [
        "banque", "credit", "financier", "epargne", "bancaire", "monetaire",
        "bourse", "marche financ", "speculation",
    ], ["banque", "credit", "financ"],
       ["credit-agricole", "bnp-paribas", "societe-generale", "credit-mutuel"]),

    SectorDef("insurance", "Assurance", [
        "assurance", "mutuelle", "prevoyance", "reassurance",
    ], ["assurance", "mutuelle"], ["axa"]),

    SectorDef("pharma", "Santé & Pharma", [
        "sante", "medicament", "pharmaceut", "hopital", "securite sociale",
        "vaccin", "epidemi", "pandem", "bioethique",
    ], ["pharma", "sante", "medic"], ["sanofi"]),

    SectorDef("defense", "Défense", [
        "defense", "armee", "militaire", "armement", "gendarmerie",
        "renseignement", "securite nationale",
    ], ["defense", "militaire", "armement"], ["thales", "dassault"]),

    SectorDef("tech", "Numérique", [
        "numerique", "donnees", "intelligence artificielle", "telecom",
        "cyber", "numerique", "plateforme", "algorithme",
    ], ["numerique", "tech", "logiciel", "informatique"],
       ["sopra-steria-group", "capgemini"]),

    SectorDef("telecom", "Télécom", [
        "telecom", "fibre", "mobile", "frequence", "spectre", "5g", "reseau",
    ], ["telecom", "fibre"], ["orange"]),

    SectorDef("agri", "Agriculture", [
        "agricol", "pac", "alimentaire", "pesticide", "phytosanit",
        "exploitation agricole", "elevage",
    ], ["agricol", "alimentaire"]),

    SectorDef("real_estate", "Immobilier", [
        "immobilier", "logement", "foncier", "urbanis", "copropriete",
        "loyer", "hlm", "hebergement",
    ], ["immobilier", "foncier"]),

    SectorDef("transport", "Transport", [
        "transport", "ferroviaire", "aerien", "sncf", "autoroute",
        "mobilite", "aviation", "maritime", "portuaire",
    ], ["transport"], ["airbus"]),

    SectorDef("luxury", "Luxe & Cosmétique", [
        "luxe", "mode", "cosmetique",
    ], ["luxe", "cosmetique"],
       ["lvmh", "hermes-international", "kering", "l-oreal"]),

    SectorDef("construction", "BTP & Construction", [
        "btp", "construction", "batiment", "infrastructure", "travaux publics",
        "route", "pont", "ouvrage",
    ], ["construct", "batiment", "infrastructure"],
       ["bouygues", "vinci"]),

    SectorDef("media", "Médias", [
        "audiovisuel", "presse", "media", "communication",
        "audiovisuelle", "diffusion",
    ], ["media", "presse", "audiovisuel"]),

    SectorDef("auto", "Automobile", [
        "automobile", "vehicule", "mobilite electrique", "voiture",
    ], ["automobil", "vehicul"]),

    SectorDef("food", "Agroalimentaire", [
        "agroalimentaire", "restauration", "grande distribution",
        "supermarche",
    ], ["agroaliment", "alimentaire"]),
]


def tag_vote(titre: str) -> list[tuple[str, str]]:
    """Tag a vote title → list of (sector_slug, direction).

    Direction: 'pro' if title also matches pro-keywords, 'anti' if anti-,
    'neutral' otherwise.
    """
    folded = strip_accents(titre).lower()
    results = []
    for sd in SECTORS:
        if any(kw in folded for kw in sd.title_keywords):
            direction = _classify_direction(folded)
            results.append((sd.slug, direction))
    return results


def tag_company(slug: str, name: str | None) -> list[str]:
    """Return sector slugs for a company by slug match or name keywords."""
    sectors = []
    for sd in SECTORS:
        if slug in sd.manual_companies:
            sectors.append(sd.slug)
            continue
        if name:
            folded = strip_accents(name).lower()
            if any(kw in folded for kw in sd.company_keywords):
                sectors.append(sd.slug)
    return sectors


def _classify_direction(folded_titre: str) -> str:
    has_pro = any(kw in folded_titre for kw in PRO_KEYWORDS)
    has_anti = any(kw in folded_titre for kw in ANTI_KEYWORDS)
    if has_pro and not has_anti:
        return "pro"
    if has_anti and not has_pro:
        return "anti"
    return "neutral"


def cmd_sectors(conn: sqlite3.Connection) -> dict[str, int]:
    """Tag all votes and companies with sectors. Idempotent (wipe + reinsert)."""
    cur = conn.cursor()
    stats = {"votes_tagged": 0, "votes_untagged": 0, "companies_tagged": 0}

    # Tag votes.
    cur.execute("DELETE FROM vote_sector_tag")
    for row in cur.execute("SELECT id, titre FROM vote").fetchall():
        tags = tag_vote(row["titre"])
        if tags:
            stats["votes_tagged"] += 1
            for sector, direction in tags:
                cur.execute(
                    "INSERT INTO vote_sector_tag(vote_id, sector, direction) VALUES(?,?,?)",
                    (row["id"], sector, direction),
                )
        else:
            stats["votes_untagged"] += 1

    # Tag companies.
    cur.execute("DELETE FROM company_sector")
    seen: set[tuple[str, str]] = set()
    for row in cur.execute(
        "SELECT DISTINCT company_slug, company_name FROM holding WHERE company_slug <> ''"
    ).fetchall():
        for sector in tag_company(row["company_slug"], row["company_name"]):
            key = (row["company_slug"], sector)
            if key not in seen:
                seen.add(key)
                cur.execute(
                    "INSERT INTO company_sector(company_slug, sector, source) VALUES(?,?,?)",
                    (row["company_slug"], sector, "keyword"),
                )
                stats["companies_tagged"] += 1

    conn.commit()
    return stats
