import sqlite3

import db


def test_init_schema_creates_tables():
    conn = db.connect(":memory:")
    db.init_schema(conn)
    names = {
        r["name"]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert {"party", "declarant", "declaration", "holding", "holding_change"} <= names


def test_init_schema_is_idempotent():
    conn = db.connect(":memory:")
    db.init_schema(conn)
    db.init_schema(conn)  # must not raise


def test_connect_enables_foreign_keys():
    conn = db.connect(":memory:")
    (val,) = conn.execute("PRAGMA foreign_keys").fetchone()
    assert val == 1


def test_declarant_unique_constraint():
    conn = db.connect(":memory:")
    db.init_schema(conn)
    conn.execute(
        "INSERT INTO declarant(nom, prenom, date_naissance) VALUES (?, ?, ?)",
        ("DUPONT", "Jean", "1970-01-01"),
    )
    try:
        conn.execute(
            "INSERT INTO declarant(nom, prenom, date_naissance) VALUES (?, ?, ?)",
            ("DUPONT", "Jean", "1970-01-01"),
        )
    except sqlite3.IntegrityError:
        return
    raise AssertionError("expected IntegrityError on duplicate declarant")
