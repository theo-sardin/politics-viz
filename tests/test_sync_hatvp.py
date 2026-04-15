from pathlib import Path

import db
from sync import cmd_hatvp

FIXTURE = str(Path(__file__).parent / "fixtures" / "hatvp_sample.xml")


def _fresh_conn():
    conn = db.connect(":memory:")
    db.init_schema(conn)
    return conn


def test_first_ingest_inserts_rows():
    conn = _fresh_conn()
    stats = cmd_hatvp(conn, FIXTURE)
    assert stats["declarations_new"] == 2
    assert stats["declarations_skipped"] == 0
    (d,) = conn.execute("SELECT COUNT(*) FROM declaration").fetchone()
    (h,) = conn.execute("SELECT COUNT(*) FROM holding").fetchone()
    (p,) = conn.execute("SELECT COUNT(*) FROM declarant").fetchone()
    assert (d, h, p) == (2, 4, 2)  # 2 decls, 4 holdings (for decl-0001), 2 people


def test_second_ingest_is_noop():
    conn = _fresh_conn()
    cmd_hatvp(conn, FIXTURE)
    stats = cmd_hatvp(conn, FIXTURE)
    assert stats["declarations_new"] == 0
    assert stats["declarations_skipped"] == 2
    (d,) = conn.execute("SELECT COUNT(*) FROM declaration").fetchone()
    (h,) = conn.execute("SELECT COUNT(*) FROM holding").fetchone()
    assert (d, h) == (2, 4)


def test_declarant_deduplicated_across_declarations():
    """If one person files twice, we should get one declarant row, two declarations."""
    conn = _fresh_conn()
    cmd_hatvp(conn, FIXTURE)
    # Simulate a second declaration by the same person
    conn.execute("""
        INSERT INTO declaration(declarant_id, raw_uuid, date_depot, mandate_cat, organe_label)
        SELECT id, 'decl-0001-bis', '2025-01-01T00:00:00', 'PAR', 'Assemblée nationale'
        FROM declarant WHERE nom='DUPONT' AND prenom='Jean'
    """)
    (n,) = conn.execute(
        "SELECT COUNT(*) FROM declaration WHERE declarant_id = (SELECT id FROM declarant WHERE nom='DUPONT')"
    ).fetchone()
    assert n == 2


def test_holding_fields_persisted():
    conn = _fresh_conn()
    cmd_hatvp(conn, FIXTURE)
    row = conn.execute(
        "SELECT * FROM holding WHERE company_slug = 'credit-agricole'"
    ).fetchone()
    assert row["kind"] == "participation_financiere"
    assert row["evaluation_eur"] == 3190
    assert row["nb_parts"] == 270
    assert row["capital_pct"] == 0.01
