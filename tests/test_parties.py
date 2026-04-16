import db
from parsers.parties import PersonParty, _label_to_slug, _split_mep_name, match, upsert_palette


def _seed(conn):
    db.init_schema(conn)
    conn.executemany(
        "INSERT INTO declarant(nom, prenom, date_naissance) VALUES (?, ?, ?)",
        [
            ("DUPONT", "Jean", "1970-12-26"),
            ("MARTIN", "Héloïse", "1985-03-05"),
            ("LE FUR", "Anne-Sophie", "1975-01-01"),
            # an ambiguous key: two people with same name, different birth dates
            ("SMITH", "John", "1960-01-01"),
            ("SMITH", "John", "1990-01-01"),
        ],
    )


def test_label_to_slug_known_variants():
    assert _label_to_slug("Rassemblement National") == "rn"
    assert _label_to_slug("LR") == "dr"          # legislature 17: LR → DR
    assert _label_to_slug("Les Républicains") == "dr"
    assert _label_to_slug("SER") == "ps"
    assert _label_to_slug("UC") == "udi"
    assert _label_to_slug("Renaissance") == "epr"  # legislature 17: Renaissance → EPR
    assert _label_to_slug("") == "none"
    assert _label_to_slug("Martian Party") == "none"


def test_split_mep_name():
    assert _split_mep_name("Grégory ALLIONE") == ("Grégory", "ALLIONE")
    assert _split_mep_name("Marie-Pierre VEDRENNE") == ("Marie-Pierre", "VEDRENNE")


def test_upsert_palette_is_idempotent():
    conn = db.connect(":memory:")
    db.init_schema(conn)
    m1 = upsert_palette(conn)
    m2 = upsert_palette(conn)
    assert m1 == m2
    assert "rn" in m1 and "epr" in m1


def test_match_exact_with_birthdate():
    conn = db.connect(":memory:")
    _seed(conn)
    people = [PersonParty("DUPONT", "Jean", "1970-12-26", "dr", "an_opendata", slug_resolved=True)]
    stats = match(conn, people)
    assert stats["matched"] == 1
    assert stats["matched_exact"] == 1
    row = conn.execute(
        "SELECT p.slug AS slug, d.party_source FROM declarant d JOIN party p ON d.party_id=p.id WHERE d.nom='DUPONT'"
    ).fetchone()
    assert row["slug"] == "dr"
    assert row["party_source"] == "an_opendata"


def test_match_fuzzy_lastname():
    conn = db.connect(":memory:")
    _seed(conn)
    # 'Duppont' (one extra p) should fuzzy-match 'DUPONT' (same prenom+birth)
    people = [PersonParty("Duppont", "Jean", None, "RN", "europarl")]
    stats = match(conn, people)
    assert stats["matched_fuzzy"] == 1


def test_match_ambiguous_no_birthdate_is_unmatched():
    """Two SMITH Johns exist; without a birthdate we refuse to guess."""
    conn = db.connect(":memory:")
    _seed(conn)
    people = [PersonParty("SMITH", "John", None, "rn", "an_opendata", slug_resolved=True)]
    stats = match(conn, people)
    assert stats["matched"] == 0


def test_match_ambiguous_with_birthdate_resolves():
    conn = db.connect(":memory:")
    _seed(conn)
    people = [PersonParty("SMITH", "John", "1990-01-01", "rn", "an_opendata", slug_resolved=True)]
    stats = match(conn, people)
    assert stats["matched_exact"] == 1


def test_match_accent_insensitive():
    conn = db.connect(":memory:")
    _seed(conn)
    people = [PersonParty("Martin", "Heloise", "1985-03-05", "lfi", "an_opendata", slug_resolved=True)]
    stats = match(conn, people)
    assert stats["matched_exact"] == 1


def test_match_create_if_unmatched():
    """When create_if_unmatched=True, new declarant rows are created."""
    conn = db.connect(":memory:")
    _seed(conn)
    before = conn.execute("SELECT COUNT(*) FROM declarant").fetchone()[0]
    people = [PersonParty("NEWPERSON", "Alice", None, "epr", "an_opendata", slug_resolved=True)]
    stats = match(conn, people, create_if_unmatched=True)
    assert stats["created"] == 1
    after = conn.execute("SELECT COUNT(*) FROM declarant").fetchone()[0]
    assert after == before + 1
    row = conn.execute(
        "SELECT p.slug, d.party_source FROM declarant d JOIN party p ON d.party_id=p.id WHERE d.nom='NEWPERSON'"
    ).fetchone()
    assert row["slug"] == "epr"
    assert row["party_source"] == "an_opendata"
