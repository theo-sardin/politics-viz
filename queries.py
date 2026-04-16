"""Read-side SQL queries feeding the pages and JSON APIs.

Kept separate from app.py so routes stay thin and queries stay testable.
All money figures are in integer euros.
"""

from __future__ import annotations

import sqlite3
from typing import Literal

from parsers.normalize import display_name, strip_accents

Scope = Literal["an", "senat", "parl", "all"]

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


def _next_month(asof: str | None) -> str:
    """Convert 'YYYY-MM' to the first of the following month.

    Used in SQL as ``date_depot < _next_month(asof)`` to include everything
    up to and including the selected month.  Returns a far-future date when
    *asof* is None (no upper bound = latest data).
    """
    if asof is None:
        return "9999-12-31"
    year, month = int(asof[:4]), int(asof[5:7])
    if month == 12:
        return f"{year + 1:04d}-01"
    return f"{year:04d}-{month + 1:02d}"


def _trailing_start(asof: str | None, months: int = 3) -> str:
    """Start month for a trailing window of *months* ending at *asof*.

    Used for surges: show changes in the N months leading up to asof.
    Returns '0000-01' when asof is None (no lower bound).
    """
    if asof is None:
        return "0000-01"
    year, mo = int(asof[:4]), int(asof[5:7])
    mo -= months - 1  # inclusive of asof month
    while mo <= 0:
        year -= 1
        mo += 12
    return f"{year:04d}-{mo:02d}"


def timeline(conn: sqlite3.Connection, scope: Scope = "an") -> list[dict]:
    """Months with at least one declaration in scope, for the timeline slider.

    Returns [{month: 'YYYY-MM', n: int}] sorted chronologically.
    """
    rows = conn.execute(
        f"""
        SELECT SUBSTR(date_depot, 1, 7) AS month,
               COUNT(*)                 AS n
        FROM declaration d
        WHERE date_depot IS NOT NULL AND {scope_clause(scope)}
        GROUP BY month
        ORDER BY month
        """,
    ).fetchall()
    return [dict(r) for r in rows]


def top_companies(
    conn: sqlite3.Connection,
    scope: Scope = "an",
    asof: str | None = None,
    limit: int = 10,
    min_holders: int = 2,
) -> list[dict]:
    """Top companies by SUM(evaluation_eur) within a scope as of a month.

    The min-holders filter hides single-individual outliers (one wealthy
    declarant's personal SCI valued at tens of millions) so the dashboard
    surfaces companies with collective holding patterns.
    """
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
          AND d.date_depot < ?
          AND {scope_clause(scope)}
        GROUP BY h.company_slug
        HAVING n_people >= ?
        ORDER BY total DESC
        LIMIT ?
        """,
        (_next_month(asof), min_holders, limit),
    ).fetchall()
    return _prettify([dict(r) for r in rows])


def top_companies_by_party(
    conn: sqlite3.Connection, scope: Scope = "an", asof: str | None = None, limit: int = 10
) -> dict:
    """Top-N companies with per-party euro breakdowns.

    Shape:
      {
        'companies': [{slug, name, total, n_people}, ...],
        'parties':   [{slug, label, color}, ...]  # includes synthetic 'none'
        'matrix':    [[eur_for_party_j_on_company_i, ...], ...]
      }
    """
    companies = top_companies(conn, scope=scope, asof=asof, limit=limit)
    if not companies:
        return {"companies": [], "parties": [], "matrix": []}

    slugs = [c["slug"] for c in companies]
    placeholders = ",".join(["?"] * len(slugs))
    cutoff = _next_month(asof)

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
          AND d.date_depot < ?
          AND {scope_clause(scope)}
        GROUP BY h.company_slug, party_slug
        """,
        [*slugs, cutoff],
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
    conn: sqlite3.Connection, slug: str, asof: str | None = None, limit: int = 20
) -> list[dict]:
    """Top companies declared by members of *slug* as of a month."""
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
          AND d.date_depot < ?
        GROUP BY h.company_slug
        ORDER BY total DESC
        LIMIT ?
        """,
        (slug, _next_month(asof), limit),
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
    conn: sqlite3.Connection, slug: str, asof: str | None = None, limit: int = 20
) -> list[dict]:
    """Recent surges for members of a party, within 3 months of asof."""
    start = _trailing_start(asof)
    cutoff = _next_month(asof)
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
          AND SUBSTR(d.date_depot, 1, 7) >= ?
          AND d.date_depot < ?
          AND hc.change_type IN ('new', 'grown')
        ORDER BY hc.delta_eur DESC
        LIMIT ?
        """,
        (slug, start, cutoff, limit),
    ).fetchall()
    return _prettify([dict(r) for r in rows])


def surges(
    conn: sqlite3.Connection,
    change_type: str,
    scope: Scope = "an",
    asof: str | None = None,
    limit: int = 10,
) -> list[dict]:
    """Top companies by aggregate delta_eur for a given change_type.

    Uses a 3-month trailing window ending at *asof* so the table shows
    recent changes rather than all-time accumulations.
    """
    start = _trailing_start(asof)
    cutoff = _next_month(asof)
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
          AND SUBSTR(d.date_depot, 1, 7) >= ?
          AND d.date_depot < ?
          AND {scope_clause(scope)}
        GROUP BY hc.company_slug
        ORDER BY {order}
        LIMIT ?
        """,
        (change_type, start, cutoff, limit),
    ).fetchall()
    return _prettify([dict(r) for r in rows])



_SPECTRUM_ORDER = """
    CASE COALESCE(p.slug, 'none')
        WHEN 'lfi' THEN 1
        WHEN 'pcf' THEN 2   WHEN 'gdr' THEN 2
        WHEN 'ecos' THEN 3  WHEN 'eelv' THEN 3
        WHEN 'ps' THEN 4
        WHEN 'liot' THEN 5
        WHEN 'epr' THEN 6   WHEN 'renaissance' THEN 6
        WHEN 'dem' THEN 7   WHEN 'modem' THEN 7
        WHEN 'horizons' THEN 8
        WHEN 'udr' THEN 9   WHEN 'udi' THEN 9
        WHEN 'dr' THEN 10   WHEN 'lr' THEN 10
        WHEN 'rn' THEN 11
        WHEN 'rec' THEN 12
        ELSE 6
    END
"""

# For the hemicycle, the source of truth is party_source — this tells us
# who actually sits, regardless of whether they've filed HATVP declarations.
_ROSTER_FILTERS: dict[str, str] = {
    "an": "de.party_source = 'an_opendata'",
    "senat": "de.party_source = 'senat'",
    "parl": "de.party_source IN ('an_opendata', 'senat')",
}


def hemicycle_groups(conn: sqlite3.Connection, scope: Scope = "an") -> list[dict]:
    """Party-level aggregates for the hemicycle.

    Returns [{slug, label, color, n_seats, total_eur}] sorted left→right.
    For AN/Sénat/Parl scopes, uses the authoritative roster (party_source)
    so ALL members appear regardless of whether they've filed HATVP declarations.
    """
    roster = _ROSTER_FILTERS.get(scope)
    if roster:
        where = roster
        params = ()
    else:
        # scope=all: fall back to declaration-based filter
        where = f"EXISTS (SELECT 1 FROM declaration dd WHERE dd.declarant_id = de.id AND {scope_clause(scope)})"
        params = ()

    rows = conn.execute(
        f"""
        SELECT COALESCE(p.slug, 'none')    AS slug,
               COALESCE(p.label, 'Non inscrit') AS label,
               COALESCE(p.color, '#999999') AS color,
               COUNT(DISTINCT de.id)        AS n_seats,
               COALESCE(SUM(h.evaluation_eur), 0) AS total_eur
        FROM declarant de
        LEFT JOIN party p ON de.party_id = p.id
        LEFT JOIN declaration d ON d.declarant_id = de.id
        LEFT JOIN holding h ON h.declaration_id = d.id
             AND h.kind = 'participation_financiere'
        WHERE {where}
        GROUP BY COALESCE(p.slug, 'none')
        ORDER BY {_SPECTRUM_ORDER}
        """,
        params,
    ).fetchall()
    return [dict(r) for r in rows]


def hemicycle(conn: sqlite3.Connection, scope: Scope = "an") -> list[dict]:
    """All members in scope with party + total holdings, for the hemicycle.

    Sorted by political spectrum (left → right) for representative seating.
    For AN/Sénat/Parl, shows the full roster from the authoritative source
    (party_source), not just those with HATVP filings.
    """
    roster = _ROSTER_FILTERS.get(scope)
    if roster:
        where = roster
        params = ()
    else:
        where = f"EXISTS (SELECT 1 FROM declaration dd WHERE dd.declarant_id = de.id AND {scope_clause(scope)})"
        params = ()

    rows = conn.execute(
        f"""
        SELECT de.id, de.nom, de.prenom,
               COALESCE(p.slug, 'none')    AS party_slug,
               COALESCE(p.label, 'Non inscrit') AS party_label,
               COALESCE(p.color, '#999999') AS party_color,
               COALESCE(SUM(h.evaluation_eur), 0) AS total
        FROM declarant de
        LEFT JOIN party p ON de.party_id = p.id
        LEFT JOIN declaration d ON d.declarant_id = de.id
        LEFT JOIN holding h ON h.declaration_id = d.id
             AND h.kind = 'participation_financiere'
        WHERE {where}
        GROUP BY de.id
        ORDER BY {_SPECTRUM_ORDER}, de.nom
        """,
        params,
    ).fetchall()
    return [dict(r) for r in rows]


def search_declarants(
    conn: sqlite3.Connection, q: str, scope: Scope = "an", limit: int = 20
) -> list[dict]:
    """Search declarants by name prefix (accent-insensitive)."""
    folded = "%" + strip_accents(q).lower() + "%"
    rows = conn.execute(
        f"""
        SELECT de.id, de.nom, de.prenom,
               COALESCE(p.slug, 'none') AS party_slug,
               COALESCE(p.label, 'Non affilié') AS party_label,
               COALESCE(p.color, '#999999') AS party_color
        FROM declarant de
        LEFT JOIN party p ON de.party_id = p.id
        WHERE EXISTS (
            SELECT 1 FROM declaration d
            WHERE d.declarant_id = de.id AND {scope_clause(scope)}
        )
        AND (LOWER(de.nom) LIKE ? OR LOWER(de.prenom) LIKE ?
             OR (LOWER(de.prenom) || ' ' || LOWER(de.nom)) LIKE ?)
        ORDER BY de.nom
        LIMIT ?
        """,
        (folded, folded, folded, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def declarant_vote_count(conn: sqlite3.Connection, declarant_id: int) -> int:
    """Total votes cast by this declarant."""
    row = conn.execute(
        "SELECT COUNT(*) FROM vote_position WHERE declarant_id = ?",
        (declarant_id,),
    ).fetchone()
    return row[0] if row else 0


def declarant_votes(
    conn: sqlite3.Connection, declarant_id: int, limit: int = 50
) -> list[dict]:
    """Recent votes cast by this declarant, newest first."""
    rows = conn.execute(
        """
        SELECT v.date, v.titre, v.sort,
               vp.position, vp.groupe,
               v.n_pour, v.n_contre, v.n_abstention
        FROM vote_position vp
        JOIN vote v ON vp.vote_id = v.id
        WHERE vp.declarant_id = ?
        ORDER BY v.date DESC
        LIMIT ?
        """,
        (declarant_id, limit),
    ).fetchall()
    return [dict(r) for r in rows]
