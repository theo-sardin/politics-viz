"""Load the lobbying registry and connect it to companies, employers and bills."""

from __future__ import annotations

import logging
import sqlite3
from collections import Counter
from pathlib import Path

from pv import companies, sectors
from pv.pipeline import billmatch
from pv.pipeline.people import CompanyStore
from pv.sources import agora
from pv.text import fold

log = logging.getLogger(__name__)

# Employer strings that are too generic to be matched to one organisation.
_GENERIC_KEYS = {"association", "conseil", "cabinet", "federation", "syndicat", "mairie", "commune", "region"}


def _slugs(org: agora.LobbyOrg) -> list[companies.CompanyRef]:
    out, seen = [], set()
    for name in [org.name, *org.aliases]:
        ref = companies.canonicalize(name)
        if ref is not None and ref.identifiable and ref.kind != "fund" and ref.slug not in seen:
            seen.add(ref.slug)
            out.append(ref)
    return out


def load_lobby(conn: sqlite3.Connection, store: CompanyStore, path: Path) -> dict[str, int]:
    stats = {"orgs": 0, "actions": 0, "orgs_linked": 0, "interests_linked": 0}
    by_slug: dict[str, list[tuple[int, str]]] = {}

    for org in agora.read_orgs(path):
        refs = _slugs(org)
        known = next((r for r in refs if companies.known(r.slug)), None)
        company_id = store.get(known) if known else None
        if company_id is None:
            company_id = next((store.ids[r.slug] for r in refs if r.slug in store.ids), None)

        domain_counts: Counter = Counter()
        for a in org.actions:
            for s in sectors.sectors_for_domains(a.domains):
                domain_counts[s] += 1
        n = len(org.actions)
        org_sectors = [s for s, c in domain_counts.most_common(3) if n and c / n >= 0.25]
        latest = max((y for y in org.years if y.spend_min is not None or y.spend_max is not None),
                     key=lambda y: y.year, default=None)

        cur = conn.execute(
            """INSERT OR IGNORE INTO lobby_org(national_id, name, name_key, category, website, company_id,
                                               sectors, spend_year, spend_min, spend_max, staff, n_actions,
                                               n_actions_parl, active)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (org.national_id, org.name, refs[0].slug if refs else fold(org.name).replace(" ", "-"),
             org.category, org.website, company_id, ",".join(org_sectors) or None,
             latest.year if latest else None, latest.spend_min if latest else None,
             latest.spend_max if latest else None, latest.staff if latest else None,
             n, sum(a.targets_parl for a in org.actions), int(org.active)),
        )
        if not cur.rowcount:
            continue
        oid = cur.lastrowid
        stats["orgs"] += 1
        stats["orgs_linked"] += company_id is not None
        for r in refs:
            by_slug.setdefault(r.slug, []).append((oid, org.name))
        conn.executemany(
            "INSERT OR REPLACE INTO lobby_year(org_id, year, spend_min, spend_max, staff, n_actions) VALUES (?,?,?,?,?,?)",
            [(oid, y.year, y.spend_min, y.spend_max, y.staff, y.n_actions) for y in org.years],
        )
        conn.executemany(
            "INSERT INTO lobby_action(org_id, year, subject, domains, targets_parl, on_law) VALUES (?,?,?,?,?,?)",
            [(oid, a.year, a.subject, "; ".join(a.domains) or None, int(a.targets_parl), int(a.on_law))
             for a in org.actions],
        )
        stats["actions"] += n

    # Employers, clients and boards named in declarations that are registered lobbies.
    rows = conn.execute(
        """SELECT DISTINCT org_key FROM interest
           WHERE company_id IS NULL AND org_key IS NOT NULL AND kind <> 'mandat'"""
    ).fetchall()
    for r in rows:
        key = r["org_key"]
        if key not in by_slug or len(key) < 4 or key in _GENERIC_KEYS:
            continue
        oid, name = by_slug[key][0]
        org_row = conn.execute("SELECT company_id, sectors FROM lobby_org WHERE id = ?", (oid,)).fetchone()
        cid = org_row["company_id"]
        if cid is None:
            secs = tuple((org_row["sectors"] or "").split(",")) if org_row["sectors"] else ()
            cid = store.get(companies.CompanyRef(key, companies.prettify(name), "org", secs), kind="org")
            conn.executemany("UPDATE lobby_org SET company_id = ? WHERE id = ?", [(cid, o) for o, _ in by_slug[key]])
        cur = conn.execute(
            "UPDATE interest SET company_id = ? WHERE org_key = ? AND company_id IS NULL AND kind <> 'mandat'",
            (cid, key),
        )
        stats["interests_linked"] += cur.rowcount
    return stats


def match_bills(conn: sqlite3.Connection) -> dict[str, int]:
    """Link lobbying actions and recusal notices to the bills they mention."""
    stats = {"bill_lobby": 0, "bills_with_lobby": 0, "deports_linked": 0}
    bills = [
        b for b in conn.execute("SELECT id, title, first_vote, last_vote FROM bill WHERE first_vote IS NOT NULL")
        if "commission d enquete" not in fold(b["title"])
    ]
    patterns = [billmatch.bill_pattern(b["id"], b["title"], b["first_vote"], b["last_vote"]) for b in bills]

    corpus = billmatch.Corpus(
        [(r["id"], r["year"], r["subject"]) for r in conn.execute("SELECT id, year, subject FROM lobby_action")]
    )
    for pat in patterns:
        ids = corpus.match(pat)
        if ids:
            conn.executemany("INSERT OR IGNORE INTO bill_lobby(bill_id, action_id) VALUES (?, ?)", [(pat.bill_id, i) for i in ids])
            stats["bill_lobby"] += len(ids)
            stats["bills_with_lobby"] += 1

    for d in conn.execute("SELECT id, date, target, reason FROM deport").fetchall():
        text = d["target"] or ""
        year = int(d["date"][:4]) if d["date"] else None
        for pat in patterns:
            if year is not None and not (pat.year_min <= year <= pat.year_max + 1):
                continue
            if text and billmatch.text_refers_to(text, pat):
                conn.execute("UPDATE deport SET bill_id = ? WHERE id = ?", (pat.bill_id, d["id"]))
                stats["deports_linked"] += 1
                break
    return stats
