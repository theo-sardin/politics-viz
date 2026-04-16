"""Test the conflict scoring engine end-to-end on a fixture DB."""

import json

import db
from parsers.sectors import cmd_sectors
from sync import cmd_conflicts


def _build_db():
    """Build a minimal DB with one declarant, one vote, one holding, linked by sector."""
    conn = db.connect(":memory:")
    db.init_schema(conn)

    # Party
    conn.execute("INSERT INTO party(slug, label, color) VALUES('rn', 'RN', '#0d378a')")
    pid = conn.execute("SELECT id FROM party WHERE slug='rn'").fetchone()["id"]

    # Declarant with a holding in TotalEnergies (energy sector)
    conn.execute(
        "INSERT INTO declarant(nom, prenom, date_naissance, party_id, party_source) VALUES(?,?,?,?,?)",
        ("DUPONT", "Jean", "1970-01-01", pid, "test"),
    )
    did = conn.execute("SELECT id FROM declarant WHERE nom='DUPONT'").fetchone()["id"]

    conn.execute(
        "INSERT INTO declaration(declarant_id, raw_uuid, date_depot, mandate_cat, mandate_type) VALUES(?,?,?,?,?)",
        (did, "u-1", "2024-01-01T00:00:00", "PAR", "Député"),
    )
    decl_id = conn.execute("SELECT id FROM declaration").fetchone()["id"]
    conn.execute(
        "INSERT INTO holding(declaration_id, kind, company_name, company_slug, evaluation_eur) VALUES(?,?,?,?,?)",
        (decl_id, "participation_financiere", "TotalEnergies", "total-energies", 50000),
    )

    # A vote about energy, tagged pro
    conn.execute(
        "INSERT INTO vote(scrutin_numero, date, titre, sort) VALUES(?,?,?,?)",
        (1, "2024-06-01", "accélération des énergies renouvelables", "adopté"),
    )
    vote_id = conn.execute("SELECT id FROM vote").fetchone()["id"]

    # Declarant voted 'pour'
    conn.execute(
        "INSERT INTO vote_position(vote_id, declarant_id, position, groupe) VALUES(?,?,?,?)",
        (vote_id, did, "pour", "RN"),
    )

    return conn, did, vote_id


def test_sectors_tags_energy_vote():
    conn, _, _ = _build_db()
    stats = cmd_sectors(conn)
    assert stats["votes_tagged"] >= 1
    assert stats["companies_tagged"] >= 1
    # Check the vote got tagged as energy/pro
    tag = conn.execute(
        "SELECT sector, direction FROM vote_sector_tag"
    ).fetchone()
    assert tag["sector"] == "energy"
    assert tag["direction"] == "pro"


def test_conflict_flag_emitted():
    conn, did, vote_id = _build_db()
    cmd_sectors(conn)
    stats = cmd_conflicts(conn)
    assert stats["flags_holding"] >= 1
    flag = conn.execute(
        "SELECT * FROM conflict_flag WHERE declarant_id=?", (did,)
    ).fetchone()
    assert flag is not None
    assert flag["sector"] == "energy"
    assert flag["score"] == 50000
    assert "TotalEnergies" in flag["reason"]


def test_no_flag_when_vote_direction_misaligned():
    """Voting 'contre' on a 'pro' bill should NOT flag."""
    conn, did, vote_id = _build_db()
    # Change position to 'contre'
    conn.execute("UPDATE vote_position SET position='contre' WHERE declarant_id=?", (did,))
    cmd_sectors(conn)
    stats = cmd_conflicts(conn)
    assert stats["flags_holding"] == 0


def test_spouse_flag():
    conn, did, _ = _build_db()
    # Add spouse employer at EDF (energy)
    conn.execute(
        "UPDATE declarant SET spouse_employers=? WHERE id=?",
        (json.dumps(["EDF"]), did),
    )
    cmd_sectors(conn)
    stats = cmd_conflicts(conn)
    assert stats["flags_spouse"] >= 1
    spouse_flag = conn.execute(
        "SELECT * FROM conflict_flag WHERE source='spouse' AND declarant_id=?", (did,)
    ).fetchone()
    assert spouse_flag is not None
    assert "EDF" in spouse_flag["reason"]


def test_conflicts_idempotent():
    conn, _, _ = _build_db()
    cmd_sectors(conn)
    cmd_conflicts(conn)
    n1 = conn.execute("SELECT COUNT(*) FROM conflict_flag").fetchone()[0]
    cmd_conflicts(conn)
    n2 = conn.execute("SELECT COUNT(*) FROM conflict_flag").fetchone()[0]
    assert n1 == n2
