"""Text normalisation shared by parsers, matching and search.

HATVP declarations are typed by hand: accents, case, punctuation and the
redaction marker all vary. Everything that compares two strings goes
through :func:`fold` first.
"""

from __future__ import annotations

import html
import re
import unicodedata
from datetime import datetime

REDACTED = "[Données non publiées]"

_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_WS = re.compile(r"\s+")
_TAG = re.compile(r"<[^>]+>")
_DIGITS = re.compile(r"-?\d+")

# Typographic characters NFKD leaves untouched.
_TRANSLATE = str.maketrans({"œ": "oe", "Œ": "OE", "æ": "ae", "Æ": "AE", "ß": "ss", "’": "'", "‘": "'"})


def strip_accents(s: str) -> str:
    s = s.translate(_TRANSLATE)
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def fold(s: str | None) -> str:
    """Accent-fold, lowercase and reduce to space-separated alphanumerics."""
    if not s:
        return ""
    return _NON_ALNUM.sub(" ", strip_accents(s).lower()).strip()


def slugify(s: str | None) -> str:
    return fold(s).replace(" ", "-")


def clean(s: str | None) -> str | None:
    """Collapse whitespace and drop the HATVP redaction marker."""
    if s is None:
        return None
    s = _WS.sub(" ", s.replace(REDACTED, " ")).strip()
    return s or None


def is_redacted(s: str | None) -> bool:
    return bool(s) and REDACTED in s


def strip_html(s: str | None) -> str | None:
    if not s:
        return None
    out = _WS.sub(" ", html.unescape(_TAG.sub(" ", s))).strip()
    return out or None


def name_key(nom: str | None, prenom: str | None) -> str:
    """Stable key for matching a person across sources: 'nom|prenom' folded."""
    return f"{fold(nom)}|{fold(prenom)}"


def parse_int(s: str | None) -> int | None:
    """Parse '10 312', '1 234,50 €', '12.5' → int. None when no digits."""
    if s is None:
        return None
    t = str(s).replace(" ", "").replace(" ", "").replace(" ", "")
    # Drop a decimal part, whichever separator was used.
    t = re.split(r"[.,](?=\d{1,2}(?:\D|$))", t, maxsplit=1)[0]
    t = t.replace(".", "").replace(",", "")
    m = _DIGITS.search(t)
    if not m:
        return None
    try:
        return int(m.group(0))
    except ValueError:
        return None


def parse_float(s: str | None) -> float | None:
    if s is None:
        return None
    t = str(s).replace(" ", "").replace(" ", "").replace(",", ".").replace("%", "")
    try:
        return float(t)
    except ValueError:
        return None


def parse_date(s: str | None) -> str | None:
    """'05/04/1980' → '1980-04-05'; '04/1980' → '1980-04'; '1980' → '1980'."""
    if not s:
        return None
    s = s.strip()
    for fmt, out in (
        ("%d/%m/%Y %H:%M:%S", "%Y-%m-%dT%H:%M:%S"),
        ("%d/%m/%Y", "%Y-%m-%d"),
        ("%m/%Y", "%Y-%m"),
        ("%Y", "%Y"),
        ("%Y-%m-%d", "%Y-%m-%d"),
    ):
        try:
            return datetime.strptime(s, fmt).strftime(out)
        except ValueError:
            continue
    return None


def titlecase_name(s: str | None) -> str:
    """'JEAN-RENÉ DE LA TOUR' → 'Jean-René de La Tour'. Leaves mixed case alone."""
    if not s:
        return ""
    s = _WS.sub(" ", s).strip()
    if s != s.upper() and s != s.lower():
        return s
    particles = {"de", "du", "des", "d", "le", "la", "les", "von", "van", "el", "al", "ben"}

    def cap(word: str, first: bool) -> str:
        if not first and word.lower() in particles:
            return word.lower()
        return "-".join(p[:1].upper() + p[1:].lower() if p else p for p in word.split("-"))

    parts = re.split(r"(\s+|')", s)
    out, first = [], True
    for p in parts:
        if not p or p.isspace() or p == "'":
            out.append(p)
            continue
        out.append(cap(p, first))
        first = False
    return "".join(out)
