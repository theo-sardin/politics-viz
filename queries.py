"""Read-side SQL queries feeding the pages and JSON APIs.

Kept separate from app.py so routes stay thin and queries stay testable.
All money figures are in integer euros.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from typing import Literal

from parsers.normalize import display_name

Window = Literal["12m", "24m", "all"]
Scope = Literal["an", "senat", "parl", "all"]

# Sentinel used when the caller wants no lower bound. Anything greater-than
# this is always true in lexical ISO-date comparison.
_MIN_DATE = "0000-01-01T00:00:00"

# SQL-injectable-looking but safe: these strings are baked into queries,
# not user-supplied — ``scope_clause`` raises on anything unknown.
_SCOPE_CLAUSES: dict[str, str] = {
    "an":    "d.mandate_type = 'Député'",
    "senat": "d.mandate_type = 'Sénateur'",
    "parl":  "d.mandate_type IN ('Député', 'Sénateur')",
    "all":   "1=1",
}


def _prettify(rows: list[dict]) -> list[dict]:
    """Apply display_name to rows that have slug + name columns."""
    for r in rows:
        if "slug" in r and "name" in r:
            r["name"] = display_name(r["slug"], r.get("name"))
    return rows


def scope_clause(scope: Scope) -> str:
    clause = _SCOPE_CLAUSES.get(scope)
    if clause is None:
        raise ValueError(f"unknown scope: {scope!r}")
    return clause


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


def top_companies(
    conn: sqlite3.Connection,
    window: Window,
    scope: Scope = "an",
    limit: int = 10,
    min_holders: int = 2,
) -> list[dict]:
    """Top companies by SUM(evaluation_eur) within a scope, filtered to
    those held by at least *min_holders* distinct declarants.

    The min-holders filter hides single-individual outliers (one wealthy
    declarant's personal SCI valued at tens of millions) so the dashboard
    surfaces companies with collective holding patterns.
    """
    start = window_start(conn, window)
    rows = conn.execute(
        f"""
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
          AND {scope_clause(scope)}
        GROUP BY h.company_slug
        HAVING n_people >= ?
        ORDER BY total DESC
        LIMIT ?
        """,
        (start, min_holders, limit),
    ).fetchall()
    return _prettify([dict(r) for r in rows])


def top_companies_by_party(
    conn: sqlite3.Connection, window: Window, scope: Scope = "an", limit: int = 10
) -> dict:
    """Top-N companies with per-party euro breakdowns.

    Shape:
      {
        'companies': [{slug, name, total, n_people}, ...],
        'parties':   [{slug, label, color}, ...]  # includes synthetic 'none'
        'matrix':    [[eur_for_party_j_on_company_i, ...], ...]
      }
    """
    companies = top_companies(conn, window, scope=scope, limit=limit)
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
          AND {scope_clause(scope)}
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


def get_declarant(conn: sqlite3.Connection, declarant_id: int) -> dict | None:
    row = conn.execute(
        """SELECT de.id, de.nom, de.prenom, de.date_naissance,
                  p.slug  AS party_slug,
                  p.label AS party_label,
                  p.color AS party_color
           FROM declarant de
           LEFT JOIN party p ON de.party_id = p.id
           WHERE de.id = ?""",
        (declarant_id,),
    ).fetchone()
    return dict(row) if row else None


def declarant_declarations(conn: sqlite3.Connection, declarant_id: int) -> list[dict]:
    rows = conn.execute(
        """SELECT id, date_depot, mandate_cat, organe_label
           FROM declaration
           WHERE declarant_id = ?
           ORDER BY date_depot""",
        (declarant_id,),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["holdings"] = [
            dict(h)
            for h in conn.execute(
                """SELECT kind, company_slug, company_name, evaluation_eur,
                          capital_pct, nb_parts, raw_commentaire
                   FROM holding WHERE declaration_id = ?
                   ORDER BY kind, company_slug""",
                (r["id"],),
            )
        ]
        out.append(d)
    return out


def declarant_changes(conn: sqlite3.Connection, declarant_id: int) -> list[dict]:
    rows = conn.execute(
        """SELECT hc.company_slug AS slug,
                  (SELECT MIN(h.company_name) FROM holding h
                   WHERE h.company_slug = hc.company_slug) AS name,
                  hc.change_type AS change_type,
                  hc.delta_eur   AS delta,
                  d.date_depot   AS date_depot
           FROM holding_change hc
           JOIN declaration d ON hc.curr_decl_id = d.id
           WHERE hc.declarant_id = ?
           ORDER BY d.date_depot DESC""",
        (declarant_id,),
    ).fetchall()
    return _prettify([dict(r) for r in rows])


def get_company(conn: sqlite3.Connection, slug: str) -> dict | None:
    row = conn.execute(
        """SELECT company_slug AS slug,
                  MIN(company_name) AS name,
                  SUM(evaluation_eur) AS total,
                  COUNT(DISTINCT declaration_id) AS n_declarations
           FROM holding
           WHERE kind = 'participation_financiere'
             AND company_slug = ?
           GROUP BY company_slug""",
        (slug,),
    ).fetchone()
    if not row:
        return None
    d = dict(row)
    d["name"] = display_name(d["slug"], d.get("name"))
    return d


def company_holders(conn: sqlite3.Connection, slug: str) -> list[dict]:
    """Declarants who have declared holdings in *slug*, with their party + total."""
    rows = conn.execute(
        """
        SELECT de.id AS id, de.nom AS nom, de.prenom AS prenom,
               COALESCE(p.slug, 'none')    AS party_slug,
               COALESCE(p.label, 'Non affilié / Local') AS party_label,
               COALESCE(p.color, '#999999') AS party_color,
               SUM(h.evaluation_eur)       AS total
        FROM holding h
        JOIN declaration d ON h.declaration_id = d.id
        JOIN declarant   de ON d.declarant_id = de.id
        LEFT JOIN party  p ON de.party_id = p.id
        WHERE h.kind = 'participation_financiere'
          AND h.company_slug = ?
          AND h.evaluation_eur IS NOT NULL
        GROUP BY de.id
        ORDER BY total DESC
        """,
        (slug,),
    ).fetchall()
    return [dict(r) for r in rows]


def company_by_party(conn: sqlite3.Connection, slug: str) -> list[dict]:
    """Aggregate declared € in *slug* grouped by party."""
    rows = conn.execute(
        """
        SELECT COALESCE(p.slug, 'none')    AS slug,
               COALESCE(p.label, 'Non affilié / Local') AS label,
               COALESCE(p.color, '#999999') AS color,
               SUM(h.evaluation_eur)       AS total,
               COUNT(DISTINCT de.id)       AS n_people
        FROM holding h
        JOIN declaration d ON h.declaration_id = d.id
        JOIN declarant   de ON d.declarant_id = de.id
        LEFT JOIN party  p ON de.party_id = p.id
        WHERE h.kind = 'participation_financiere'
          AND h.company_slug = ?
          AND h.evaluation_eur IS NOT NULL
        GROUP BY p.slug
        ORDER BY total DESC
        """,
        (slug,),
    ).fetchall()
    return [dict(r) for r in rows]


def company_timeline(conn: sqlite3.Connection, slug: str) -> list[dict]:
    """Quarterly totals of declared € for *slug*, keyed on date_depot."""
    rows = conn.execute(
        """
        SELECT substr(d.date_depot, 1, 4)                 AS year,
               (CAST(substr(d.date_depot, 6, 2) AS INTEGER) - 1) / 3 + 1 AS quarter,
               SUM(h.evaluation_eur)                      AS total,
               COUNT(DISTINCT de.id)                      AS n_people
        FROM holding h
        JOIN declaration d ON h.declaration_id = d.id
        JOIN declarant   de ON d.declarant_id = de.id
        WHERE h.kind = 'participation_financiere'
          AND h.company_slug = ?
          AND h.evaluation_eur IS NOT NULL
        GROUP BY year, quarter
        ORDER BY year, quarter
        """,
        (slug,),
    ).fetchall()
    return [
        {"period": f"{r['year']}-Q{r['quarter']}", "total": r["total"], "n_people": r["n_people"]}
        for r in rows
    ]


def get_party(conn: sqlite3.Connection, slug: str) -> dict | None:
    row = conn.execute(
        "SELECT slug, label, color FROM party WHERE slug = ?", (slug,)
    ).fetchone()
    return dict(row) if row else None


def party_top_companies(
    conn: sqlite3.Connection, slug: str, window: Window, limit: int = 20
) -> list[dict]:
    """Top companies declared by members of *slug*."""
    start = window_start(conn, window)
    rows = conn.execute(
        """
        SELECT h.company_slug              AS slug,
               MIN(h.company_name)         AS name,
               SUM(h.evaluation_eur)       AS total,
               COUNT(DISTINCT d.declarant_id) AS n_people
        FROM holding h
        JOIN declaration d ON h.declaration_id = d.id
        JOIN declarant  de ON d.declarant_id = de.id
        JOIN party      p  ON de.party_id = p.id
        WHERE h.kind = 'participation_financiere'
          AND h.evaluation_eur IS NOT NULL
          AND h.company_slug <> ''
          AND p.slug = ?
          AND d.date_depot >= ?
        GROUP BY h.company_slug
        ORDER BY total DESC
        LIMIT ?
        """,
        (slug, start, limit),
    ).fetchall()
    return _prettify([dict(r) for r in rows])


def party_members(conn: sqlite3.Connection, slug: str) -> list[dict]:
    """All declarants of *slug*, with aggregate declared € for context."""
    rows = conn.execute(
        """
        SELECT de.id AS id, de.nom AS nom, de.prenom AS prenom,
               COALESCE(SUM(h.evaluation_eur), 0) AS total
        FROM declarant de
        JOIN party p ON de.party_id = p.id
        LEFT JOIN declaration d ON d.declarant_id = de.id
        LEFT JOIN holding h ON h.declaration_id = d.id
             AND h.kind = 'participation_financiere'
             AND h.evaluation_eur IS NOT NULL
        WHERE p.slug = ?
        GROUP BY de.id
        ORDER BY total DESC, de.nom
        """,
        (slug,),
    ).fetchall()
    return [dict(r) for r in rows]


def party_surges(
    conn: sqlite3.Connection, slug: str, window: Window, limit: int = 20
) -> list[dict]:
    start = window_start(conn, window)
    rows = conn.execute(
        """
        SELECT hc.company_slug AS slug,
               (SELECT MIN(h.company_name) FROM holding h
                WHERE h.company_slug = hc.company_slug) AS name,
               hc.change_type AS change_type,
               hc.delta_eur AS delta
        FROM holding_change hc
        JOIN declarant  de ON hc.declarant_id = de.id
        JOIN party      p  ON de.party_id = p.id
        JOIN declaration d ON hc.curr_decl_id = d.id
        WHERE p.slug = ?
          AND d.date_depot >= ?
          AND hc.change_type IN ('new', 'grown')
        ORDER BY hc.delta_eur DESC
        LIMIT ?
        """,
        (slug, start, limit),
    ).fetchall()
    return _prettify([dict(r) for r in rows])


def surges(
    conn: sqlite3.Connection,
    window: Window,
    change_type: str,
    scope: Scope = "an",
    limit: int = 10,
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
          AND {scope_clause(scope)}
        GROUP BY hc.company_slug
        ORDER BY {order}
        LIMIT ?
        """,
        (change_type, start, limit),
    ).fetchall()
    return _prettify([dict(r) for r in rows])


# ---------------------------------------------------------------------------
# Conflict queries
# ---------------------------------------------------------------------------


def top_conflicted(
    conn: sqlite3.Connection, scope: Scope = "an", limit: int = 10
) -> list[dict]:
    """Top declarants by number of conflict flags, within scope."""
    rows = conn.execute(
        f"""
        SELECT de.id AS id, de.nom AS nom, de.prenom AS prenom,
               COALESCE(p.slug, 'none') AS party_slug,
               COALESCE(p.label, 'Non affilié / Local') AS party_label,
               COALESCE(p.color, '#999999') AS party_color,
               COUNT(cf.id)                             AS n_flags,
               COUNT(DISTINCT cf.vote_id)               AS n_votes,
               SUM(CASE WHEN cf.source='holding' THEN cf.score ELSE 0 END) AS holding_eur,
               COUNT(CASE WHEN cf.source='spouse' THEN 1 END) AS n_spouse_flags
        FROM conflict_flag cf
        JOIN declarant de ON cf.declarant_id = de.id
        LEFT JOIN party p ON de.party_id = p.id
        WHERE EXISTS (
            SELECT 1 FROM declaration d
            WHERE d.declarant_id = de.id AND {scope_clause(scope)}
        )
        GROUP BY de.id
        ORDER BY n_flags DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def declarant_conflicts(conn: sqlite3.Connection, declarant_id: int) -> list[dict]:
    """All conflict flags for a declarant, newest first."""
    rows = conn.execute(
        """
        SELECT cf.sector, cf.reason, cf.source, cf.score,
               v.date, v.titre, v.scrutin_numero
        FROM conflict_flag cf
        JOIN vote v ON cf.vote_id = v.id
        WHERE cf.declarant_id = ?
        ORDER BY v.date DESC
        """,
        (declarant_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def hemicycle(conn: sqlite3.Connection, scope: Scope = "an") -> list[dict]:
    """All declarants in scope with party + total holdings, for the hemicycle.

    Sorted by political spectrum (left → right) for realistic seating.
    """
    rows = conn.execute(
        f"""
        SELECT de.id, de.nom, de.prenom,
               COALESCE(p.slug, 'none')    AS party_slug,
               COALESCE(p.label, 'Non affilié') AS party_label,
               COALESCE(p.color, '#999999') AS party_color,
               COALESCE(SUM(h.evaluation_eur), 0) AS total
        FROM declarant de
        LEFT JOIN party p ON de.party_id = p.id
        LEFT JOIN declaration d ON d.declarant_id = de.id AND {scope_clause(scope)}
        LEFT JOIN holding h ON h.declaration_id = d.id
             AND h.kind = 'participation_financiere'
        WHERE EXISTS (
            SELECT 1 FROM declaration dd
            WHERE dd.declarant_id = de.id AND {scope_clause(scope)}
        )
        GROUP BY de.id
        ORDER BY
            CASE COALESCE(p.slug, 'none')
                WHEN 'lfi' THEN 1
                WHEN 'pcf' THEN 2
                WHEN 'eelv' THEN 3
                WHEN 'ps' THEN 4
                WHEN 'liot' THEN 5
                WHEN 'renaissance' THEN 6
                WHEN 'modem' THEN 7
                WHEN 'horizons' THEN 8
                WHEN 'udi' THEN 9
                WHEN 'lr' THEN 10
                WHEN 'rn' THEN 11
                WHEN 'rec' THEN 12
                ELSE 6
            END,
            de.nom
        """,
    ).fetchall()
    return [dict(r) for r in rows]


def declarant_vote_count(conn: sqlite3.Connection, declarant_id: int) -> int:
    """Total votes cast by this declarant."""
    row = conn.execute(
        "SELECT COUNT(*) FROM vote_position WHERE declarant_id = ?",
        (declarant_id,),
    ).fetchone()
    return row[0] if row else 0
