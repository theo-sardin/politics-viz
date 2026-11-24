"""Declarations kept from the pre-rewrite database.

HATVP unpublishes a declaration once the mandate it covers is over, so the
old database is the only remaining copy of several thousand of them. They
carry less detail (stakes and board seats only) and are flagged
``archived`` once imported.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterator

from pv.sources.hatvp import Declaration, Holding, Interest
from pv.text import fold

_KINDS = [
    ("depute", "depute"),
    ("senateur", "senateur"),
    ("regional", "region"),
    ("departemental", "departement"),
    ("epci", "epci"),
    ("maire", "commune"),
    ("statut particulier", "ctsp"),
]


def _mandate_kind(mandate_type: str | None) -> str:
    t = fold(mandate_type)
    for needle, kind in _KINDS:
        if needle in t:
            return kind
    return "autre"


def read_declarations(path: str | Path, skip_uuids: set[str]) -> Iterator[Declaration]:
    """Yield legacy declarations whose uuid is not in *skip_uuids*."""
    path = Path(path)
    if not path.exists():
        return
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"declarant", "declaration", "holding"} <= tables:
            return
        rows = conn.execute(
            """SELECT d.id, d.raw_uuid, d.date_depot, d.mandate_cat, d.mandate_type, d.organe_label,
                      de.nom, de.prenom, de.date_naissance
               FROM declaration d JOIN declarant de ON de.id = d.declarant_id
               WHERE d.date_depot <> ''
               ORDER BY d.id"""
        ).fetchall()
        for r in rows:
            if r["raw_uuid"] in skip_uuids:
                continue
            kind = _mandate_kind(r["mandate_type"])
            label = " · ".join(p for p in (r["mandate_type"], r["organe_label"]) if p and p.strip())
            birth = r["date_naissance"]
            rec = Declaration(
                uuid=r["raw_uuid"],
                doc_type="DIA" if r["mandate_cat"] == "PAR" else "DI",
                is_modification=False,
                filed_at=r["date_depot"],
                civilite=None,
                nom=r["nom"],
                prenom=r["prenom"],
                birth_date=birth if birth and 1900 <= int(birth[:4]) <= 2010 else None,
                mandate_kind=kind,
                mandate_label=label or None,
                mandate_start=None,
                no_holdings=False,
                note=None,
            )
            for h in conn.execute(
                """SELECT kind, company_name, evaluation_eur, capital_pct, nb_parts, raw_commentaire
                   FROM holding WHERE declaration_id = ?""",
                (r["id"],),
            ):
                if h["kind"] == "participation_financiere":
                    rec.holdings.append(
                        Holding(
                            kind="participation",
                            raw_name=h["company_name"],
                            value_eur=h["evaluation_eur"],
                            shares=h["nb_parts"] or None,
                            capital_pct=h["capital_pct"] or None,
                            comment=h["raw_commentaire"],
                        )
                    )
                elif h["kind"] == "participation_dirigeant" and h["company_name"]:
                    rec.interests.append(Interest("dirigeant", h["company_name"], h["raw_commentaire"]))
            yield rec
    finally:
        conn.close()
