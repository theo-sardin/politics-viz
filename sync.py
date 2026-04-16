"""Sync CLI — download sources and upsert into SQLite.

Subcommands:
  hatvp <path>   — ingest HATVP XML
  parties        — fetch AN/Sénat/MEP sources and match to declarants
  changes        — recompute holding_change deltas
  all <path>     — hatvp → parties → changes
"""

from __future__ import annotations

import json
import logging
import sqlite3
import sys
import time as _time
from pathlib import Path

import httpx

import db
from parsers.hatvp import DeclarationRecord, parse
from parsers.normalize import person_key
from parsers.parties import cmd_parties
from parsers.normalize import company_slug as make_slug
from parsers.sectors import cmd_sectors, tag_company
from parsers.votes import fetch_scrutin_detail, fetch_scrutin_list

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
    """Find or create a declarant by (nom, prenom, date_naissance).

    Also updates spouse_employers if the new declaration carries any.
    """
    row = cur.execute(
        """SELECT id FROM declarant
           WHERE nom = ? AND prenom = ?
             AND (date_naissance IS ? OR date_naissance = ?)""",
        (rec.nom, rec.prenom, rec.date_naissance, rec.date_naissance),
    ).fetchone()
    if row is not None:
        did = row["id"]
    else:
        cur.execute(
            "INSERT INTO declarant(nom, prenom, date_naissance) VALUES (?, ?, ?)",
            (rec.nom, rec.prenom, rec.date_naissance),
        )
        _ = person_key(rec.nom, rec.prenom)
        did = cur.lastrowid
    # Merge spouse employers from every declaration (best-effort accumulate).
    if rec.spouse_employers:
        existing = cur.execute(
            "SELECT spouse_employers FROM declarant WHERE id = ?", (did,)
        ).fetchone()["spouse_employers"]
        prev = json.loads(existing) if existing else []
        merged = list(dict.fromkeys(prev + rec.spouse_employers))  # dedup, preserve order
        cur.execute(
            "UPDATE declarant SET spouse_employers = ? WHERE id = ?",
            (json.dumps(merged, ensure_ascii=False), did),
        )
    return did


def cmd_votes(conn: sqlite3.Connection) -> dict[str, int]:
    """Fetch all AN scrutins from NosDéputés and upsert votes + positions.

    Idempotent: only fetches scrutins whose numero isn't already in the DB.
    """
    stats = {"scrutins_fetched": 0, "scrutins_skipped": 0, "positions_inserted": 0}

    # Build declarant lookup: parlementaire_slug → declarant_id.
    # NosDéputés slugs are "firstname-lastname" lowercased.
    slug_to_decl: dict[str, int] = {}
    for r in conn.execute("SELECT id, nom, prenom FROM declarant"):
        slug = f"{r['prenom']}-{r['nom']}".lower().replace(" ", "-")
        slug_to_decl[slug] = r["id"]

    existing = {
        r[0]
        for r in conn.execute("SELECT scrutin_numero FROM vote").fetchall()
    }

    with httpx.Client(timeout=30.0, follow_redirects=True) as client:
        all_numeros = fetch_scrutin_list(client)
        to_fetch = [n for n in all_numeros if n not in existing]
        log.info("votes: %d total, %d to fetch, %d already in DB",
                 len(all_numeros), len(to_fetch), len(existing))

        for i, numero in enumerate(to_fetch):
            try:
                record, positions = fetch_scrutin_detail(client, numero)
            except Exception as e:
                log.warning("scrutin %d: %s", numero, e)
                continue

            cur = conn.cursor()
            cur.execute(
                """INSERT OR IGNORE INTO vote
                   (scrutin_numero, date, type, titre, sort, n_pour, n_contre, n_abstention)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record.scrutin_numero, record.date, record.type,
                    record.titre, record.sort, record.n_pour,
                    record.n_contre, record.n_abstention,
                ),
            )
            vote_id = cur.execute(
                "SELECT id FROM vote WHERE scrutin_numero = ?",
                (record.scrutin_numero,),
            ).fetchone()["id"]

            for pos in positions:
                decl_id = slug_to_decl.get(pos.parlementaire_slug)
                if decl_id is None:
                    continue
                cur.execute(
                    """INSERT INTO vote_position
                       (vote_id, declarant_id, position, groupe)
                       VALUES (?, ?, ?, ?)""",
                    (vote_id, decl_id, pos.position, pos.groupe),
                )
                stats["positions_inserted"] += 1

            stats["scrutins_fetched"] += 1
            if (i + 1) % 100 == 0:
                conn.commit()
                log.info("votes: %d/%d fetched", i + 1, len(to_fetch))
            _time.sleep(0.15)

        stats["scrutins_skipped"] = len(existing)
        conn.commit()
    return stats


def cmd_conflicts(conn: sqlite3.Connection) -> dict[str, int]:
    """Cross-reference holdings × votes × sectors → conflict_flag rows.

    A flag is emitted when a declarant:
      1. Holds a company in sector S (via company_sector)
      2. A vote is tagged sector S with direction D (via vote_sector_tag)
      3. The declarant voted in the aligned direction:
         - direction='pro' AND position='pour'  → flagged
         - direction='anti' AND position='contre' → flagged

    Additionally, if the declarant's spouse works at a company in sector S,
    the same logic applies with source='spouse'.
    """
    cur = conn.cursor()
    cur.execute("DELETE FROM conflict_flag")
    stats = {"flags_holding": 0, "flags_spouse": 0}

    # ---- Flags from direct holdings ----
    # Build {declarant_id: {sector: total_eur}} from holdings × company_sector.
    decl_sectors: dict[int, dict[str, int]] = {}
    decl_companies: dict[int, dict[str, list[str]]] = {}  # sector → [company names]
    for r in cur.execute("""
        SELECT d.declarant_id, cs.sector, h.company_slug,
               MIN(h.company_name) AS name,
               SUM(h.evaluation_eur) AS total
        FROM holding h
        JOIN declaration d ON h.declaration_id = d.id
        JOIN company_sector cs ON cs.company_slug = h.company_slug
        WHERE h.kind = 'participation_financiere' AND h.evaluation_eur > 0
        GROUP BY d.declarant_id, cs.sector, h.company_slug
    """).fetchall():
        did = r["declarant_id"]
        sec = r["sector"]
        decl_sectors.setdefault(did, {})
        decl_sectors[did][sec] = decl_sectors[did].get(sec, 0) + r["total"]
        decl_companies.setdefault(did, {}).setdefault(sec, [])
        if r["name"] and r["name"] not in decl_companies[did][sec]:
            decl_companies[did][sec].append(r["name"])

    # Walk vote_positions joined with vote_sector_tags.
    for r in cur.execute("""
        SELECT vp.declarant_id, vp.vote_id, vp.position,
               vst.sector, vst.direction,
               v.titre, v.date
        FROM vote_position vp
        JOIN vote_sector_tag vst ON vst.vote_id = vp.vote_id
        JOIN vote v ON v.id = vp.vote_id
    """).fetchall():
        did = r["declarant_id"]
        sector = r["sector"]
        direction = r["direction"]
        position = r["position"]
        if sector not in decl_sectors.get(did, {}):
            continue
        if not _is_aligned(direction, position):
            continue
        score = decl_sectors[did][sector]
        companies = decl_companies.get(did, {}).get(sector, [])[:3]
        company_names = ", ".join(companies)
        slug = make_slug(companies[0]) if companies else ""
        reason = (
            f"Détient {_fmt_eur(score)} dans {company_names} ({sector}). "
            f"A voté {position} sur «{r['titre'][:80]}…» ({direction}-{sector})."
        )
        cur.execute(
            """INSERT INTO conflict_flag
               (declarant_id, vote_id, company_slug, sector, score, reason, source)
               VALUES (?, ?, ?, ?, ?, ?, 'holding')""",
            (did, r["vote_id"], slug, sector, score, reason),
        )
        stats["flags_holding"] += 1

    # ---- Flags from spouse employers ----
    for r in cur.execute(
        "SELECT id, spouse_employers FROM declarant WHERE spouse_employers IS NOT NULL"
    ).fetchall():
        employers = json.loads(r["spouse_employers"])
        spouse_sectors: dict[str, tuple[str, str]] = {}  # sector → (slug, name)
        for emp in employers:
            slug = make_slug(emp)
            for sec in tag_company(slug, emp):
                spouse_sectors.setdefault(sec, (slug, emp))
        if not spouse_sectors:
            continue
        did = r["id"]
        for vp in cur.execute("""
            SELECT vp.vote_id, vp.position, vst.sector, vst.direction, v.titre
            FROM vote_position vp
            JOIN vote_sector_tag vst ON vst.vote_id = vp.vote_id
            JOIN vote v ON v.id = vp.vote_id
            WHERE vp.declarant_id = ?
        """, (did,)).fetchall():
            sector = vp["sector"]
            if sector not in spouse_sectors:
                continue
            if not _is_aligned(vp["direction"], vp["position"]):
                continue
            emp_slug, emp_name = spouse_sectors[sector]
            reason = (
                f"Conjoint(e) travaille chez {emp_name} ({sector}). "
                f"A voté {vp['position']} sur «{vp['titre'][:80]}…» ({vp['direction']}-{sector})."
            )
            cur.execute(
                """INSERT INTO conflict_flag
                   (declarant_id, vote_id, company_slug, sector, score, reason, source)
                   VALUES (?, ?, ?, ?, ?, ?, 'spouse')""",
                (did, vp["vote_id"], emp_slug, sector, 1, reason),
            )
            stats["flags_spouse"] += 1

    conn.commit()
    return stats


def _is_aligned(direction: str, position: str) -> bool:
    return (direction == "pro" and position == "pour") or \
           (direction == "anti" and position == "contre")


def _fmt_eur(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f} M€"
    if n >= 1_000:
        return f"{n // 1_000} k€"
    return f"{n} €"


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

    cmds = {"hatvp", "parties", "changes", "votes", "sectors", "conflicts", "all"}
    if len(argv) < 2 or argv[1] not in cmds:
        print(
            "usage: sync.py {hatvp <xml> | parties | changes | votes | all <xml>} [--db PATH]",
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
    if cmd == "votes":
        return _run(cmd_votes, conn)
    if cmd == "sectors":
        return _run(cmd_sectors, conn)
    if cmd == "conflicts":
        return _run(cmd_conflicts, conn)
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
        code = _run(cmd_changes, conn)
        if code:
            return code
        code = _run(cmd_votes, conn)
        if code:
            return code
        code = _run(cmd_sectors, conn)
        if code:
            return code
        return _run(cmd_conflicts, conn)
    return 2


def _run(fn, *args) -> int:
    stats = fn(*args)
    for k, v in stats.items():
        print(f"{k}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
