"""Streaming parser for HATVP 'déclarations de patrimoine/intérêts' XML.

Uses ``lxml.etree.iterparse`` on ``<declaration>`` elements so the full
118 MB file can be processed without loading it into memory. Each
declaration is clear()-ed after emission.

Exposes ``parse(path)`` which yields :class:`DeclarationRecord` instances.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterator

from lxml import etree

from parsers.normalize import company_slug, strip_accents

_EUR_GARBAGE = re.compile(r"[^\d\-]")


@dataclass
class Holding:
    kind: str  # 'participation_financiere' | 'valeur_bourse' | 'participation_dirigeant'
    company_name: str | None
    company_slug: str
    evaluation_eur: int | None
    capital_pct: float | None
    nb_parts: int | None
    commentaire: str | None


@dataclass
class DeclarationRecord:
    uuid: str
    date_depot: str  # ISO 8601
    mandate_cat: str | None
    mandate_type: str | None  # Député / Sénateur / Membre d'EPCI / ...
    organe_label: str | None
    nom: str
    prenom: str
    date_naissance: str | None  # ISO 8601 date or None
    holdings: list[Holding] = field(default_factory=list)
    spouse_employers: list[str] = field(default_factory=list)


def parse(path: str) -> Iterator[DeclarationRecord]:
    """Yield one :class:`DeclarationRecord` per ``<declaration>`` in *path*."""
    ctx = etree.iterparse(path, events=("end",), tag="declaration")
    for _, elem in ctx:
        record = _parse_declaration(elem)
        if record is not None:
            yield record
        # Free memory as we go.
        elem.clear()
        while elem.getprevious() is not None:
            del elem.getparent()[0]
    del ctx


def _parse_declaration(elem) -> DeclarationRecord | None:
    uuid = _text(elem, "uuid")
    if not uuid:
        return None
    general = elem.find("general")
    declarant = general.find("declarant") if general is not None else None
    nom = _text(declarant, "nom") if declarant is not None else None
    prenom = _text(declarant, "prenom") if declarant is not None else None
    if not nom or not prenom:
        return None

    qualite = general.find("qualiteMandat") if general is not None else None
    return DeclarationRecord(
        uuid=uuid,
        date_depot=_iso_datetime(_text(elem, "dateDepot")) or "",
        mandate_cat=_text(qualite, "codCategorieMandat") if qualite is not None else None,
        mandate_type=_clean(_text(qualite, "typeMandat")) if qualite is not None else None,
        organe_label=_text(general.find("organe"), "labelOrgane")
        if general is not None and general.find("organe") is not None
        else None,
        nom=_clean(nom),
        prenom=_clean(prenom),
        date_naissance=_iso_date(_text(declarant, "dateNaissance"))
        if declarant is not None
        else None,
        holdings=list(_iter_holdings(elem)),
        spouse_employers=list(_iter_spouse_employers(elem)),
    )


def _iter_holdings(elem) -> Iterator[Holding]:
    yield from _iter_participation_financiere(elem)
    yield from _iter_valeurs_bourse(elem)
    yield from _iter_participation_dirigeant(elem)


_SPOUSE_JUNK = {"neant", "0", "sans emploi", "sans", "aucun", "aucune", "na", "n/a", "non", "rien"}


def _iter_spouse_employers(elem) -> Iterator[str]:
    """Yield employer names from ``activProfConjointDto``."""
    for item in _items(elem, "activProfConjointDto"):
        employer = _clean(_text(item, "employeurConjoint"))
        if not employer:
            continue
        folded = strip_accents(employer).lower().strip()
        if folded in _SPOUSE_JUNK:
            continue
        yield employer


def _iter_participation_financiere(elem) -> Iterator[Holding]:
    for item in _items(elem, "participationFinanciereDto"):
        name = _clean(_text(item, "nomSociete"))
        slug = _identifiable_slug(name)
        if slug is None:
            continue
        yield Holding(
            kind="participation_financiere",
            company_name=name,
            company_slug=slug,
            evaluation_eur=_parse_eur(_text(item, "evaluation")),
            capital_pct=_parse_float(_text(item, "capitalDetenu")),
            nb_parts=_parse_int(_text(item, "nombreParts")),
            commentaire=_clean(_text(item, "commentaire")),
        )


def _iter_valeurs_bourse(elem) -> Iterator[Holding]:
    for item in _items(elem, "valeursEnBourseDto"):
        etab = _clean(_text(item, "etablissement"))
        slug = _identifiable_slug(etab)
        if slug is None:
            continue
        yield Holding(
            kind="valeur_bourse",
            company_name=etab,
            company_slug=slug,
            evaluation_eur=_parse_eur(_text(item, "valeur")),
            capital_pct=None,
            nb_parts=None,
            commentaire=_clean(_text(item, "naturePlacement")),
        )


def _iter_participation_dirigeant(elem) -> Iterator[Holding]:
    for item in _items(elem, "participationDirigeantDto"):
        name = _clean(_text(item, "nomSociete"))
        slug = _identifiable_slug(name)
        if slug is None:
            continue
        yield Holding(
            kind="participation_dirigeant",
            company_name=name,
            company_slug=slug,
            evaluation_eur=None,
            capital_pct=None,
            nb_parts=None,
            commentaire=_clean(_text(item, "activite")),
        )


def _identifiable_slug(name: str | None) -> str | None:
    """Return a usable slug, or None if the entry is a placeholder / generic.

    Drops:
      - empty / '0' / 'neant' placeholders
      - fully-redacted entries ('[Données non publiées]')
      - names that reduce to an empty slug after suffix stripping (e.g.
        a raw 'SCI' with no actual name)
    """
    if not name:
        return None
    stripped = name.strip().lower()
    if stripped in ("0", "neant"):
        return None
    slug = company_slug(name)
    return slug or None


def _items(decl, wrapper_tag: str):
    """Yield inner <items> children of a <foo><items><items>...</items></items></foo> wrapper."""
    wrapper = decl.find(wrapper_tag)
    if wrapper is None:
        return
    items_parent = wrapper.find("items")
    if items_parent is None:
        return
    for item in items_parent.findall("items"):
        yield item


def _text(elem, tag: str) -> str | None:
    if elem is None:
        return None
    child = elem.find(tag)
    if child is None or child.text is None:
        return None
    return child.text


def _clean(s: str | None) -> str | None:
    """Collapse whitespace and drop the HATVP '[Données non publiées]' marker."""
    if s is None:
        return None
    s = s.replace("[Données non publiées]", " ").strip()
    s = re.sub(r"\s+", " ", s)
    return s or None


def _parse_eur(s: str | None) -> int | None:
    if not s:
        return None
    cleaned = _EUR_GARBAGE.sub("", s.replace("\u00a0", ""))
    if not cleaned or cleaned in ("-", ""):
        return None
    try:
        return int(cleaned)
    except ValueError:
        return None


def _parse_int(s: str | None) -> int | None:
    return _parse_eur(s)


def _parse_float(s: str | None) -> float | None:
    if not s:
        return None
    s = s.replace("\u00a0", "").replace(" ", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _iso_datetime(s: str | None) -> str | None:
    if not s:
        return None
    try:
        return datetime.strptime(s.strip(), "%d/%m/%Y %H:%M:%S").isoformat()
    except ValueError:
        return _iso_date(s)


def _iso_date(s: str | None) -> str | None:
    if not s:
        return None
    s = s.strip()
    for fmt in ("%d/%m/%Y", "%m/%Y", "%Y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None
