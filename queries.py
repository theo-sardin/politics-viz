"""Read-side SQL queries feeding the pages and JSON APIs.

Kept separate from app.py so routes stay thin and queries stay testable.
All money figures are in integer euros.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from typing import Literal

Window = Literal["12m", "24m", "all"]

# Sentinel used when the caller wants no lower bound. Anything greater-than
# this is always true in lexical ISO-date comparison.
_MIN_DATE = "0000-01-01T00:00:00"


def window_start(conn: sqlite3.Connection, window: Window) -> str:
    """ISO datetime floor for *window*, anchored on MAX(date_depot)."""
    if window == "all":
        return _MIN_DATE
    row = conn.execute("SELECT MAX(date_depot) AS d FROM declaration").fetchone()
    if not row or not row["d"]:
        return _MIN_DATE
    max_dt = datetime.fromisoformat(row["d"])
    months = {"12m": 12, "24m": 24}[window]
    return (max_dt - timedelta(days=months * 30)).isoformat()


def top_companies(conn: sqlite3.Connection, window: Window, limit: int = 20) -> list[dict]:
    start = window_start(conn, window)
    rows = conn.execute(
        """
        SELECT h.company_slug          AS slug,
               MIN(h.company_name)     AS name,
               SUM(h.evaluation_eur)   AS total,
               COUNT(DISTINCT d.declarant_id) AS n_people
        FROM holding h
        JOIN declaration d ON h.declaration_id = d.id
        WHERE h.kind = 'participation_financiere'
          AND h.evaluation_eur IS NOT NULL
          AND h.company_slug <> ''
          AND d.date_depot >= ?
        GROUP BY h.company_slug
        ORDER BY total DESC
        LIMIT ?
        """,
        (start, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def top_companies_by_party(
    conn: sqlite3.Connection, window: Window, limit: int = 20
) -> dict:
    """Top-N companies with per-party euro breakdowns.

    Shape:
      {
        'companies': [{slug, name, total, n_people}, ...],
        'parties':   [{slug, label, color}, ...]  # includes synthetic 'none'
        'matrix':    [[eur_for_party_j_on_company_i, ...], ...]
      }
    """
    companies = top_companies(conn, window, limit)
    if not companies:
        return {"companies": [], "parties": [], "matrix": []}

    slugs = [c["slug"] for c in companies]
    placeholders = ",".join(["?"] * len(slugs))
    start = window_start(conn, window)

    breakdown = conn.execute(
        f"""
        SELECT h.company_slug              AS company_slug,
               COALESCE(p.slug, 'none')    AS party_slug,
               COALESCE(p.label, 'Non affilié / Local') AS party_label,
               COALESCE(p.color, '#999999') AS party_color,
               SUM(h.evaluation_eur)        AS total
        FROM holding h
        JOIN declaration d ON h.declaration_id = d.id
        JOIN declarant  de ON d.declarant_id = de.id
        LEFT JOIN party p  ON de.party_id = p.id
        WHERE h.kind = 'participation_financiere'
          AND h.evaluation_eur IS NOT NULL
          AND h.company_slug IN ({placeholders})
          AND d.date_depot >= ?
        GROUP BY h.company_slug, party_slug
        """,
        [*slugs, start],
    ).fetchall()

    # Collect unique parties preserving first-seen order for stable stacking.
    parties_seen: dict[str, dict] = {}
    for r in breakdown:
        parties_seen.setdefault(
            r["party_slug"],
            {"slug": r["party_slug"], "label": r["party_label"], "color": r["party_color"]},
        )
    parties = sorted(parties_seen.values(), key=lambda p: p["label"])

    party_idx = {p["slug"]: i for i, p in enumerate(parties)}
    slug_idx = {s: i for i, s in enumerate(slugs)}
    matrix = [[0 for _ in parties] for _ in slugs]
    for r in breakdown:
        matrix[slug_idx[r["company_slug"]]][party_idx[r["party_slug"]]] = r["total"]

    return {"companies": companies, "parties": parties, "matrix": matrix}


def surges(
    conn: sqlite3.Connection, window: Window, change_type: str, limit: int = 20
) -> list[dict]:
    """Top companies by aggregate delta_eur for a given change_type.

    For 'new' and 'grown' we sort by SUM(delta_eur) DESC. For 'reduced'
    and 'sold' the deltas are negative, so we sort by magnitude.
    """
    start = window_start(conn, window)
    order = "ABS(total) DESC" if change_type in ("reduced", "sold") else "total DESC"
    rows = conn.execute(
        f"""
        SELECT hc.company_slug AS slug,
               (SELECT MIN(h.company_name)
                FROM holding h
                WHERE h.company_slug = hc.company_slug) AS name,
               COUNT(DISTINCT hc.declarant_id)         AS n_people,
               SUM(hc.delta_eur)                       AS total
        FROM holding_change hc
        JOIN declaration d ON hc.curr_decl_id = d.id
        WHERE hc.change_type = ?
          AND d.date_depot >= ?
        GROUP BY hc.company_slug
        ORDER BY {order}
        LIMIT ?
        """,
        (change_type, start, limit),
    ).fetchall()
    return [dict(r) for r in rows]
