"""SQLite connection + schema for politics-viz.

The schema is a single module so anything (web, sync, tests) can create or
open a database against the same definition.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS party (
    id     INTEGER PRIMARY KEY,
    slug   TEXT NOT NULL UNIQUE,
    label  TEXT NOT NULL,
    color  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS declarant (
    id              INTEGER PRIMARY KEY,
    nom             TEXT NOT NULL,
    prenom          TEXT NOT NULL,
    date_naissance  TEXT,
    party_id        INTEGER REFERENCES party(id),
    party_source    TEXT,
    spouse_employers TEXT,  -- JSON array of employer strings, from activProfConjointDto
    UNIQUE(nom, prenom, date_naissance)
);

CREATE TABLE IF NOT EXISTS declaration (
    id            INTEGER PRIMARY KEY,
    declarant_id  INTEGER NOT NULL REFERENCES declarant(id),
    raw_uuid      TEXT NOT NULL UNIQUE,
    date_depot    TEXT NOT NULL,
    mandate_cat   TEXT,   -- coarse: LOC / PAR / EUR / ...
    mandate_type  TEXT,   -- fine:   Député / Sénateur / Membre d'EPCI / ...
    organe_label  TEXT
);

CREATE TABLE IF NOT EXISTS holding (
    id               INTEGER PRIMARY KEY,
    declaration_id   INTEGER NOT NULL REFERENCES declaration(id),
    kind             TEXT NOT NULL,
    company_name     TEXT,
    company_slug     TEXT,
    evaluation_eur   INTEGER,
    capital_pct      REAL,
    nb_parts         INTEGER,
    raw_commentaire  TEXT
);

CREATE TABLE IF NOT EXISTS holding_change (
    id            INTEGER PRIMARY KEY,
    declarant_id  INTEGER NOT NULL REFERENCES declarant(id),
    company_slug  TEXT NOT NULL,
    prev_decl_id  INTEGER REFERENCES declaration(id),
    curr_decl_id  INTEGER NOT NULL REFERENCES declaration(id),
    delta_eur     INTEGER,
    change_type   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS vote (
    id               INTEGER PRIMARY KEY,
    scrutin_numero   INTEGER NOT NULL UNIQUE,
    date             TEXT NOT NULL,
    type             TEXT,
    titre            TEXT NOT NULL,
    sort             TEXT,
    n_pour           INTEGER,
    n_contre         INTEGER,
    n_abstention     INTEGER
);

CREATE TABLE IF NOT EXISTS vote_position (
    id            INTEGER PRIMARY KEY,
    vote_id       INTEGER NOT NULL REFERENCES vote(id),
    declarant_id  INTEGER NOT NULL REFERENCES declarant(id),
    position      TEXT NOT NULL,
    groupe        TEXT
);

CREATE TABLE IF NOT EXISTS vote_sector_tag (
    id         INTEGER PRIMARY KEY,
    vote_id    INTEGER NOT NULL REFERENCES vote(id),
    sector     TEXT NOT NULL,
    direction  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS company_sector (
    company_slug  TEXT NOT NULL,
    sector        TEXT NOT NULL,
    source        TEXT,
    PRIMARY KEY (company_slug, sector)
);

CREATE TABLE IF NOT EXISTS conflict_flag (
    id            INTEGER PRIMARY KEY,
    declarant_id  INTEGER NOT NULL REFERENCES declarant(id),
    vote_id       INTEGER NOT NULL REFERENCES vote(id),
    company_slug  TEXT NOT NULL,
    sector        TEXT NOT NULL,
    score         INTEGER NOT NULL,
    reason        TEXT NOT NULL,
    source        TEXT NOT NULL DEFAULT 'holding'
);

CREATE INDEX IF NOT EXISTS idx_vote_position_vote       ON vote_position(vote_id);
CREATE INDEX IF NOT EXISTS idx_vote_position_declarant  ON vote_position(declarant_id);
CREATE INDEX IF NOT EXISTS idx_vote_sector_tag_vote     ON vote_sector_tag(vote_id);
CREATE INDEX IF NOT EXISTS idx_conflict_declarant       ON conflict_flag(declarant_id);
CREATE INDEX IF NOT EXISTS idx_conflict_vote            ON conflict_flag(vote_id);
CREATE INDEX IF NOT EXISTS idx_holding_company       ON holding(company_slug);
CREATE INDEX IF NOT EXISTS idx_holding_declaration   ON holding(declaration_id);
CREATE INDEX IF NOT EXISTS idx_change_declarant      ON holding_change(declarant_id);
CREATE INDEX IF NOT EXISTS idx_change_company        ON holding_change(company_slug);
CREATE INDEX IF NOT EXISTS idx_change_curr_decl      ON holding_change(curr_decl_id);
CREATE INDEX IF NOT EXISTS idx_declaration_declarant ON declaration(declarant_id);
CREATE INDEX IF NOT EXISTS idx_declaration_date      ON declaration(date_depot);
"""


def connect(path: str | Path) -> sqlite3.Connection:
    """Open a SQLite connection with sensible defaults."""
    conn = sqlite3.connect(path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """Create all tables and indexes. Idempotent."""
    conn.executescript(SCHEMA)
