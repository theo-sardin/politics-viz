from parsers.sectors import tag_vote, tag_company, _classify_direction, cmd_sectors
import db


def test_tag_vote_energy():
    tags = tag_vote("projet de loi relatif à l'accélération des énergies renouvelables")
    sectors = [s for s, _ in tags]
    assert "energy" in sectors


def test_tag_vote_direction_pro():
    tags = tag_vote("accélération des énergies renouvelables")
    for sector, direction in tags:
        if sector == "energy":
            assert direction == "pro"


def test_tag_vote_direction_anti():
    tags = tag_vote("régulation du marché de l'énergie")
    for sector, direction in tags:
        if sector == "energy":
            assert direction == "anti"


def test_tag_vote_neutral():
    tags = tag_vote("projet de loi relatif à l'énergie")
    for sector, direction in tags:
        if sector == "energy":
            assert direction == "neutral"


def test_tag_vote_banking():
    tags = tag_vote("encadrement des frais bancaires")
    sectors = [s for s, _ in tags]
    assert "banking" in sectors


def test_tag_vote_no_match():
    tags = tag_vote("motion de censure déposée par le groupe LFI")
    assert tags == []


def test_tag_company_manual_override():
    assert "energy" in tag_company("total-energies", "TotalEnergies SE")
    assert "luxury" in tag_company("l-oreal", "L'Oréal")
    assert "banking" in tag_company("credit-agricole", "Crédit Agricole")


def test_tag_company_keyword():
    assert "energy" in tag_company("francaise-energie", "Francaise Energie SA")
    assert "pharma" in tag_company("sanofi", "Sanofi")


def test_tag_company_no_match():
    assert tag_company("random-thing", "Blabla Corp") == []


def test_classify_direction():
    assert _classify_direction("acceleration des choses") == "pro"
    assert _classify_direction("regulation stricte") == "anti"
    assert _classify_direction("discussion generale") == "neutral"
    # both pro + anti → neutral
    assert _classify_direction("acceleration et regulation") == "neutral"


def test_cmd_sectors_on_empty_db():
    conn = db.connect(":memory:")
    db.init_schema(conn)
    stats = cmd_sectors(conn)
    assert stats["votes_tagged"] == 0
    assert stats["companies_tagged"] == 0
