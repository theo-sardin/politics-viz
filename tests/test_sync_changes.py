"""Tests for ``cmd_changes``.

We build a tiny database by hand (no XML) and verify each transition type.
"""

import db
from sync import cmd_changes


def _setup_declarant_with_timeline(conn, timeline):
    """Seed one declarant with a sequence of (date_depot, [(company, eur), ...])."""
    db.init_schema(conn)
    conn.execute(
        "INSERT INTO declarant(nom, prenom, date_naissance) VALUES ('DOE', 'Jane', '1980-01-01')"
    )
    declarant_id = conn.execute("SELECT id FROM declarant").fetchone()["id"]

    for idx, (date_depot, holdings) in enumerate(timeline, start=1):
        conn.execute(
            "INSERT INTO declaration(declarant_id, raw_uuid, date_depot) VALUES (?, ?, ?)",
            (declarant_id, f"u-{idx}", date_depot),
        )
        decl_id = conn.execute(
            "SELECT id FROM declaration WHERE raw_uuid = ?", (f"u-{idx}",)
        ).fetchone()["id"]
        for slug, eur in holdings:
            conn.execute(
                """INSERT INTO holding
                   (declaration_id, kind, company_name, company_slug, evaluation_eur)
                   VALUES (?, 'participation_financiere', ?, ?, ?)""",
                (decl_id, slug.upper(), slug, eur),
            )


def _changes(conn):
    return [
        (r["company_slug"], r["change_type"], r["delta_eur"])
        for r in conn.execute(
            """SELECT company_slug, change_type, delta_eur
               FROM holding_change ORDER BY curr_decl_id, company_slug"""
        )
    ]


def test_new_position_on_first_declaration():
    conn = db.connect(":memory:")
    _setup_declarant_with_timeline(conn, [("2024-01-01T00:00:00", [("edf", 1000)])])
    stats = cmd_changes(conn)
    assert stats["new"] == 1
    assert _changes(conn) == [("edf", "new", 1000)]


def test_grown_then_reduced_then_sold():
    conn = db.connect(":memory:")
    _setup_declarant_with_timeline(
        conn,
        [
            ("2023-01-01T00:00:00", [("edf", 1000)]),
            ("2024-01-01T00:00:00", [("edf", 1500)]),  # grown +500
            ("2025-01-01T00:00:00", [("edf", 800)]),   # reduced -700
            ("2026-01-01T00:00:00", [("edf", 0)]),     # sold -800
        ],
    )
    stats = cmd_changes(conn)
    assert stats == {"new": 1, "grown": 1, "reduced": 1, "sold": 1}
    rows = _changes(conn)
    assert rows == [
        ("edf", "new", 1000),
        ("edf", "grown", 500),
        ("edf", "reduced", -700),
        ("edf", "sold", -800),
    ]


def test_multiple_holdings_same_company_are_summed():
    """Two lines declaring EDF in the same declaration should sum before diffing."""
    conn = db.connect(":memory:")
    _setup_declarant_with_timeline(
        conn,
        [
            ("2023-01-01T00:00:00", [("edf", 500)]),
            ("2024-01-01T00:00:00", [("edf", 300), ("edf", 400)]),  # 700 total → grown +200
        ],
    )
    stats = cmd_changes(conn)
    assert stats["grown"] == 1
    rows = _changes(conn)
    assert rows[-1] == ("edf", "grown", 200)


def test_different_companies_tracked_independently():
    conn = db.connect(":memory:")
    _setup_declarant_with_timeline(
        conn,
        [
            ("2023-01-01T00:00:00", [("edf", 500), ("total", 1000)]),
            ("2024-01-01T00:00:00", [("edf", 700)]),  # total disappears → sold
        ],
    )
    stats = cmd_changes(conn)
    # edf: new + grown; total: new + sold
    assert stats["new"] == 2
    assert stats["grown"] == 1
    assert stats["sold"] == 1


def test_rerun_is_idempotent():
    conn = db.connect(":memory:")
    _setup_declarant_with_timeline(
        conn, [("2023-01-01T00:00:00", [("edf", 1000)]), ("2024-01-01T00:00:00", [("edf", 1500)])]
    )
    cmd_changes(conn)
    first = _changes(conn)
    cmd_changes(conn)
    second = _changes(conn)
    assert first == second
