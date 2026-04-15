"""Sync CLI — download sources and upsert into SQLite.

Subcommands are added incrementally (see plan). First slice: ``hatvp``.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import db
from parsers.hatvp import DeclarationRecord, parse
from parsers.normalize import person_key


def cmd_hatvp(conn: sqlite3.Connection, xml_path: str) -> dict[str, int]:
    """Ingest a HATVP XML dump. Idempotent on raw_uuid."""
    stats = {"declarations_new": 0, "declarations_skipped": 0, "holdings_inserted": 0}
    cur = conn.cursor()
    cur.execute("BEGIN")
    try:
        for rec in parse(xml_path):
            inserted = _upsert_record(cur, rec)
            if inserted:
                stats["declarations_new"] += 1
                stats["holdings_inserted"] += len(rec.holdings)
            else:
                stats["declarations_skipped"] += 1
        cur.execute("COMMIT")
    except Exception:
        cur.execute("ROLLBACK")
        raise
    return stats


def _upsert_record(cur: sqlite3.Cursor, rec: DeclarationRecord) -> bool:
    """Insert declaration + holdings if raw_uuid is new. Returns True on insert."""
    row = cur.execute(
        "SELECT 1 FROM declaration WHERE raw_uuid = ?", (rec.uuid,)
    ).fetchone()
    if row is not None:
        return False

    declarant_id = _upsert_declarant(cur, rec)
    cur.execute(
        """INSERT INTO declaration
           (declarant_id, raw_uuid, date_depot, mandate_cat, organe_label)
           VALUES (?, ?, ?, ?, ?)""",
        (declarant_id, rec.uuid, rec.date_depot, rec.mandate_cat, rec.organe_label),
    )
    declaration_id = cur.lastrowid
    for h in rec.holdings:
        cur.execute(
            """INSERT INTO holding
               (declaration_id, kind, company_name, company_slug,
                evaluation_eur, capital_pct, nb_parts, raw_commentaire)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                declaration_id,
                h.kind,
                h.company_name,
                h.company_slug,
                h.evaluation_eur,
                h.capital_pct,
                h.nb_parts,
                h.commentaire,
            ),
        )
    return True


def _upsert_declarant(cur: sqlite3.Cursor, rec: DeclarationRecord) -> int:
    """Find or create a declarant by (nom, prenom, date_naissance)."""
    row = cur.execute(
        """SELECT id FROM declarant
           WHERE nom = ? AND prenom = ?
             AND (date_naissance IS ? OR date_naissance = ?)""",
        (rec.nom, rec.prenom, rec.date_naissance, rec.date_naissance),
    ).fetchone()
    if row is not None:
        return row["id"]
    cur.execute(
        "INSERT INTO declarant(nom, prenom, date_naissance) VALUES (?, ?, ?)",
        (rec.nom, rec.prenom, rec.date_naissance),
    )
    # Touch person_key so imports are consistent (used later for matching).
    _ = person_key(rec.nom, rec.prenom)
    return cur.lastrowid


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] not in {"hatvp"}:
        print(
            "usage: sync.py hatvp <path/to/data.xml> [--db PATH]",
            file=sys.stderr,
        )
        return 2

    db_path = "data.sqlite"
    args = argv[2:]
    if "--db" in args:
        i = args.index("--db")
        db_path = args.pop(i + 1)
        args.pop(i)

    cmd = argv[1]
    conn = db.connect(db_path)
    db.init_schema(conn)

    if cmd == "hatvp":
        if not args:
            print("usage: sync.py hatvp <path/to/data.xml>", file=sys.stderr)
            return 2
        xml_path = args[0]
        if not Path(xml_path).exists():
            print(f"not found: {xml_path}", file=sys.stderr)
            return 1
        stats = cmd_hatvp(conn, xml_path)
        for k, v in stats.items():
            print(f"{k}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
