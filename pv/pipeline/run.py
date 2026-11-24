"""Build the database from the raw sources.

A sync always rebuilds from scratch into a temporary file and swaps it in at
the end, so the web app never reads a half-built database and a failed sync
leaves the previous one untouched.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path

from pv import config, db
from pv.pipeline import changes, lobby, parliament, people, photos, signals
from pv.sources import fetch

log = logging.getLogger(__name__)


def sync(db_path: Path | None = None, *, offline: bool = False, force: bool = False) -> dict:
    db_path = Path(db_path or config.DB_PATH)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    report: dict = {}
    t0 = time.monotonic()

    paths = {}
    for name in config.SOURCES:
        paths[name] = fetch.fetch(name, force=force, offline=offline)
        log.info("source %-20s %s", name, paths[name] or "indisponible")

    building = db_path.with_name(db_path.name + ".building")
    for suffix in ("", "-wal", "-shm"):
        Path(str(building) + suffix).unlink(missing_ok=True)
    conn = db.connect(building)
    try:
        db.init_schema(conn)
        build(conn, paths, report)
        report["photos"] = photos.sync_photos(conn, offline=offline)
        conn.commit()
        log.info("%-14s %s", "photos", report["photos"])
        for name in config.SOURCES:
            meta = fetch.read_meta(name)
            db.set_meta(conn, f"source:{name}", meta.get("last_modified") or meta.get("fetched_at") or "")
        db.set_meta(conn, "synced_at", datetime.now(UTC).isoformat(timespec="seconds"))
        conn.commit()
        conn.execute("ANALYZE")
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.execute("PRAGMA journal_mode = DELETE")
        conn.commit()
    finally:
        conn.close()

    for suffix in ("-wal", "-shm"):
        Path(str(db_path) + suffix).unlink(missing_ok=True)
    os.replace(building, db_path)
    report["seconds"] = round(time.monotonic() - t0, 1)
    return report


def build(conn: sqlite3.Connection, paths: dict, report: dict) -> None:
    """Run every pipeline stage against an initialised, empty database."""

    def stage(name: str, fn, *args):
        t = time.monotonic()
        out = fn(*args)
        conn.commit()
        log.info("%-14s %5.1fs  %s", name, time.monotonic() - t, out if out is not None else "")
        if out is not None:
            report[name] = out
        return out

    reg = people.People(conn)
    store = people.CompanyStore(conn)
    index = people.load_index(paths["hatvp_index"])

    stage("declarations", people.load_declarations, conn, reg, store, paths["hatvp_declarations"], index)
    stage("assemblee", parliament.load_assemblee_roles, conn, reg, paths["an_acteurs"], index)
    stage("senat", parliament.load_senat_roles, conn, reg, paths["senat_senateurs"], index)
    stage("roles", people.assign_fallback_roles, conn, index)
    stage("slugs", people.assign_slugs, conn)

    scrutins = {leg: paths[f"an_scrutins_{leg}"] for leg in config.LEGISLATURES if paths.get(f"an_scrutins_{leg}")}
    dossiers = {leg: paths.get(f"an_dossiers_{leg}") for leg in config.LEGISLATURES}
    stage("votes", parliament.load_votes, conn, reg, scrutins, dossiers, lambda known: store.get(_known_ref(known)))
    stage("lobby", lobby.load_lobby, conn, store, paths["agora"])
    stage("bill_links", lobby.match_bills, conn)
    stage("changes", changes.compute_changes, conn)
    stage("signals", signals.compute_signals, conn)
    stage("summary", finalize, conn)


def _known_ref(known):
    from pv.companies import CompanyRef

    return CompanyRef(known.slug, known.name, known.kind, known.sectors, known.ticker)


def finalize(conn: sqlite3.Connection) -> dict[str, int]:
    """Denormalise what every page needs: current portfolio, holder counts."""
    conn.execute(
        """UPDATE person SET current_decl_id = (
               SELECT d.id FROM declaration d
               WHERE d.person_id = person.id AND d.doc_type IN ('DI', 'DIA')
               ORDER BY d.filed_at DESC, d.id DESC LIMIT 1)"""
    )
    conn.execute(
        """UPDATE person SET
             portfolio_eur = COALESCE((SELECT SUM(h.value_eur) FROM holding h
                                       WHERE h.declaration_id = person.current_decl_id
                                         AND h.kind = 'participation'), 0),
             n_holdings = (SELECT COUNT(*) FROM holding h
                           WHERE h.declaration_id = person.current_decl_id AND h.kind = 'participation'),
             n_signals = (SELECT COUNT(*) FROM signal_person sp JOIN signal s ON s.id = sp.signal_id
                          WHERE sp.person_id = person.id AND s.level <> 'info'),
             top_score = COALESCE((SELECT MAX(s.score) FROM signal_person sp JOIN signal s ON s.id = sp.signal_id
                                   WHERE sp.person_id = person.id AND s.level <> 'info'), 0)"""
    )
    conn.execute(
        """UPDATE company SET
             n_holders = (SELECT COUNT(DISTINCT p.id) FROM holding h
                          JOIN person p ON p.current_decl_id = h.declaration_id
                          WHERE h.company_id = company.id),
             total_eur = COALESCE((SELECT SUM(h.value_eur) FROM holding h
                                   JOIN person p ON p.current_decl_id = h.declaration_id
                                   WHERE h.company_id = company.id), 0)"""
    )
    # The most common spelling becomes the display name of companies outside the registry.
    conn.execute(
        """UPDATE company SET name = COALESCE((
               SELECT h.label FROM holding h WHERE h.company_id = company.id
               GROUP BY h.label ORDER BY COUNT(*) DESC, LENGTH(h.label) LIMIT 1), name)
           WHERE kind = 'other'"""
    )
    return {
        "people": conn.execute("SELECT COUNT(*) FROM person").fetchone()[0],
        "companies": conn.execute("SELECT COUNT(*) FROM company").fetchone()[0],
        "signals": conn.execute("SELECT COUNT(*) FROM signal").fetchone()[0],
    }
