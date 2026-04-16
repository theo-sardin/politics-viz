"""String normalization used across parsers and matching.

`company_slug` groups free-text company names that refer to the same entity.
`person_key` does the same for names, used to match declarants to party data.
"""

from __future__ import annotations

import re
import unicodedata

_COMPANY_SUFFIXES = re.compile(
    # Generic legal forms and financial wrappers. A declaration of
    # just "GFA" or "PEA" with no actual name slugs to empty and is
    # dropped by _identifiable_slug in the parser.
    r"\b(sasu|sarl|sci|sas|sa|eurl|scp|scm|scs|snc|sc|sca"
    r"|gfa|gfv|gfr|gfp|scea|gaec|earl|selarl|sccv|sicav|sccp"
    r"|fcpi|fcpr|fcp|gie|pea)\b",
    re.IGNORECASE,
)
_NON_WORD = re.compile(r"[^a-z0-9]+")
_SPACES = re.compile(r"\s+")


def strip_accents(s: str) -> str:
    """Fold accents using NFKD → ASCII drop. Leaves unknown chars alone."""
    return "".join(
        c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)
    )


def company_slug(name: str | None) -> str:
    """Slug a company name for grouping.

    Empty input → empty slug. Suffixes like 'SA', 'SARL' stripped to
    collapse 'Crédit Agricole SA' and 'Crédit Agricole' into one bucket.
    """
    if not name:
        return ""
    s = strip_accents(name).lower()
    s = _COMPANY_SUFFIXES.sub(" ", s)
    s = _NON_WORD.sub("-", s)
    return s.strip("-")


# Display-name overrides for known companies whose raw HATVP names are
# ugly ALL-CAPS or missing punctuation. Keyed by company_slug.
_DISPLAY_NAMES: dict[str, str] = {
    "l-oreal": "L'Oréal",
    "air-liquide": "Air Liquide",
    "total-energies": "TotalEnergies",
    "totalenergies": "TotalEnergies",
    "lvmh": "LVMH",
    "lvmh-moet-vuitton": "LVMH Moët Hennessy",
    "hermes-international": "Hermès International",
    "hermes-intl": "Hermès International",
    "credit-agricole": "Crédit Agricole",
    "credit-mutuel": "Crédit Mutuel",
    "societe-generale": "Société Générale",
    "bnp-paribas": "BNP Paribas",
    "schneider-electric": "Schneider Electric",
    "sanofi": "Sanofi",
    "axa": "AXA",
    "kering": "Kering",
    "bouygues": "Bouygues",
    "engie": "Engie",
    "vinci": "Vinci",
    "edf": "EDF",
    "orange": "Orange",
    "airbus": "Airbus SE",
    "vallourec": "Vallourec",
    "sopra-steria-group": "Sopra Steria",
}


def display_name(slug: str, raw_name: str | None = None) -> str:
    """Best human-readable name for a company_slug.

    Checks override table first; falls back to title-casing the raw name
    (if provided) or the slug itself.
    """
    if slug in _DISPLAY_NAMES:
        return _DISPLAY_NAMES[slug]
    if raw_name:
        # Title-case but keep short words lowercase and known acronyms upper.
        return _titlecase(raw_name)
    return slug.replace("-", " ").title()


_ACRONYMS = {"etf", "edf", "bnp", "axa", "lvmh", "sas", "usa", "usd", "pea"}
_LOWERCASE = {"de", "du", "des", "le", "la", "les", "et", "en", "au", "aux", "d"}


def _titlecase(s: str) -> str:
    words = s.split()
    out = []
    for i, w in enumerate(words):
        low = w.lower()
        if low in _ACRONYMS or (w.isupper() and len(w) <= 4 and low not in _LOWERCASE):
            out.append(w.upper())
        elif i > 0 and low in _LOWERCASE:
            out.append(low)
        else:
            out.append(w.capitalize())
    return " ".join(out)


def person_key(nom: str | None, prenom: str | None) -> str:
    """Stable key for matching persons across sources.

    Format: 'lastname|firstname', both accent-folded + lowercased + trimmed.
    """
    n = _SPACES.sub(" ", strip_accents(nom or "").lower().strip())
    p = _SPACES.sub(" ", strip_accents(prenom or "").lower().strip())
    return f"{n}|{p}"
