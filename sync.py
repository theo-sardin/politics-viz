"""Sync CLI — download sources and upsert into SQLite.

Subcommands:
  hatvp <path>   — ingest HATVP XML
  parties        — fetch AN/Sénat/MEP sources and match to declarants
  changes        — recompute holding_change deltas
  all <path>     — hatvp → parties → changes
"""

from __future__ import annotations

import logging
import sqlite3
import sys
from pathlib import Path

import db
from parsers.hatvp import DeclarationRecord, parse
from parsers.normalize import person_key
from parsers.parties import cmd_parties

log = logging.getLogger(__name__)


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
           (declarant_id, raw_uuid, date_depot, mandate_cat, mandate_type, organe_label)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            declarant_id,
            rec.uuid,
            rec.date_depot,
            rec.mandate_cat,
            rec.mandate_type,
            rec.organe_label,
        ),
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


def cmd_changes(conn: sqlite3.Connection) -> dict[str, int]:
    """Recompute ``holding_change`` from scratch.

    Walks each declarant's declarations in chronological order, diffing
    the set of (company_slug → total_eur) between consecutive snapshots.
    Emits one row per transition: 'new', 'grown', 'reduced', or 'sold'.
    """
    cur = conn.cursor()
    cur.execute("BEGIN")
    try:
        cur.execute("DELETE FROM holding_change")
        stats = {"new": 0, "grown": 0, "reduced": 0, "sold": 0}
        for declarant_id in _iter_declarant_ids(cur):
            _emit_changes_for_declarant(cur, declarant_id, stats)
        cur.execute("COMMIT")
    except Exception:
        cur.execute("ROLLBACK")
        raise
    return stats


def _iter_declarant_ids(cur):
    return [
        r["declarant_id"]
        for r in cur.execute(
            """SELECT DISTINCT d.declarant_id
               FROM declaration d
               JOIN holding h ON h.declaration_id = d.id
               WHERE h.kind = 'participation_financiere' AND h.company_slug <> ''"""
        )
    ]


def _emit_changes_for_declarant(cur, declarant_id, stats):
    declarations = cur.execute(
        "SELECT id, date_depot FROM declaration WHERE declarant_id = ? ORDER BY date_depot",
        (declarant_id,),
    ).fetchall()

    prev_map: dict[str, int] = {}
    prev_decl_id: int | None = None
    for decl in declarations:
        curr_map = _snapshot(cur, decl["id"])
        _emit_transitions(cur, declarant_id, prev_map, curr_map, prev_decl_id, decl["id"], stats)
        prev_map = curr_map
        prev_decl_id = decl["id"]


def _snapshot(cur, declaration_id: int) -> dict[str, int]:
    """Return {company_slug: total_eur} for one declaration (summed across rows)."""
    rows = cur.execute(
        """SELECT company_slug, SUM(evaluation_eur) AS total_eur
           FROM holding
           WHERE declaration_id = ?
             AND kind = 'participation_financiere'
             AND company_slug <> ''
           GROUP BY company_slug""",
        (declaration_id,),
    ).fetchall()
    return {r["company_slug"]: (r["total_eur"] or 0) for r in rows}


def _emit_transitions(cur, declarant_id, prev_map, curr_map, prev_decl_id, curr_decl_id, stats):
    for slug, cur_eur in curr_map.items():
        prev_eur = prev_map.get(slug, 0)
        if prev_eur == 0 and cur_eur > 0:
            _insert_change(cur, declarant_id, slug, prev_decl_id, curr_decl_id, cur_eur, "new")
            stats["new"] += 1
        elif cur_eur > prev_eur > 0:
            _insert_change(cur, declarant_id, slug, prev_decl_id, curr_decl_id, cur_eur - prev_eur, "grown")
            stats["grown"] += 1
        elif 0 < cur_eur < prev_eur:
            _insert_change(cur, declarant_id, slug, prev_decl_id, curr_decl_id, cur_eur - prev_eur, "reduced")
            stats["reduced"] += 1
        elif cur_eur == 0 and prev_eur > 0:
            _insert_change(cur, declarant_id, slug, prev_decl_id, curr_decl_id, -prev_eur, "sold")
            stats["sold"] += 1
    # Companies present before but absent now → 'sold'.
    for slug, prev_eur in prev_map.items():
        if slug not in curr_map and prev_eur > 0:
            _insert_change(cur, declarant_id, slug, prev_decl_id, curr_decl_id, -prev_eur, "sold")
            stats["sold"] += 1


def _insert_change(cur, declarant_id, company_slug, prev_decl_id, curr_decl_id, delta_eur, change_type):
    cur.execute(
        """INSERT INTO holding_change
           (declarant_id, company_slug, prev_decl_id, curr_decl_id, delta_eur, change_type)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (declarant_id, company_slug, prev_decl_id, curr_decl_id, delta_eur, change_type),
    )


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    if len(argv) < 2 or argv[1] not in {"hatvp", "parties", "changes", "all"}:
        print(
            "usage: sync.py {hatvp <xml> | parties | changes | all <xml>} [--db PATH]",
            file=sys.stderr,
        )
        return 2

    # Strip optional --db flag
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
        return _run(cmd_hatvp, conn, args[0])
    if cmd == "parties":
        return _run(cmd_parties, conn)
    if cmd == "changes":
        return _run(cmd_changes, conn)
    if cmd == "all":
        if not args:
            print("usage: sync.py all <path/to/data.xml>", file=sys.stderr)
            return 2
        code = _run(cmd_hatvp, conn, args[0])
        if code:
            return code
        code = _run(cmd_parties, conn)
        if code:
            return code
        return _run(cmd_changes, conn)
    return 2


def _run(fn, *args) -> int:
    stats = fn(*args)
    for k, v in stats.items():
        print(f"{k}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
