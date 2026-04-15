"""String normalization used across parsers and matching.

`company_slug` groups free-text company names that refer to the same entity.
`person_key` does the same for names, used to match declarants to party data.
"""

from __future__ import annotations

import re
import unicodedata

_COMPANY_SUFFIXES = re.compile(
    r"\b(sasu|sarl|sci|sas|sa|eurl|scp|scm|scs|snc|sc|sca)\b",
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


def person_key(nom: str | None, prenom: str | None) -> str:
    """Stable key for matching persons across sources.

    Format: 'lastname|firstname', both accent-folded + lowercased + trimmed.
    """
    n = _SPACES.sub(" ", strip_accents(nom or "").lower().strip())
    p = _SPACES.sub(" ", strip_accents(prenom or "").lower().strip())
    return f"{n}|{p}"
