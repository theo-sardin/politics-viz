"""SQLite schema and connection helper.

The database is a build artefact: ``pv sync`` recreates it from the raw
sources (plus the legacy archive), so there are no migrations. Bump
``SCHEMA_VERSION`` when a table changes and the next sync rebuilds.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_VERSION = 5

# Vote positions are stored as small integers: the vote table has millions of rows.
POUR, CONTRE, ABSTENTION, NON_VOTANT = 1, 2, 3, 0
POSITION_LABELS = {POUR: "pour", CONTRE: "contre", ABSTENTION: "abstention", NON_VOTANT: "non-votant"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key    TEXT PRIMARY KEY,
    value  TEXT
);

CREATE TABLE IF NOT EXISTS groupe (
    id          INTEGER PRIMARY KEY,
    slug        TEXT NOT NULL UNIQUE,      -- 'an-rn', 'senat-ser'
    chamber     TEXT NOT NULL,             -- 'an' | 'senat'
    code        TEXT NOT NULL,
    label       TEXT NOT NULL,
    color       TEXT NOT NULL,             -- on the light surface
    color_dark  TEXT NOT NULL,             -- on the dark surface
    ord         INTEGER NOT NULL,          -- left → right
    organe_uid  TEXT
);

CREATE TABLE IF NOT EXISTS person (
    id          INTEGER PRIMARY KEY,
    slug        TEXT UNIQUE,               -- stable URL key (ids change at every rebuild)
    hatvp_slug  TEXT UNIQUE,               -- 'lahmar-abdelkader-27452'
    an_id       TEXT UNIQUE,               -- 'PA841729'
    senat_id    TEXT UNIQUE,               -- '21077M'
    civilite    TEXT,
    prenom      TEXT NOT NULL,
    nom         TEXT NOT NULL,
    name_key    TEXT NOT NULL,             -- fold(nom)|fold(prenom)
    search_key  TEXT NOT NULL,             -- fold('prenom nom')
    birth_date  TEXT,
    photo_url   TEXT,
    chamber     TEXT,                      -- an | senat | gouv | europe | local
    active      INTEGER NOT NULL DEFAULT 0,
    role        TEXT,
    departement TEXT,
    profession  TEXT,
    group_id    INTEGER REFERENCES groupe(id),
    seat        INTEGER,
    committee   TEXT,
    -- denormalised at the end of a sync
    current_decl_id  INTEGER,
    portfolio_eur    INTEGER NOT NULL DEFAULT 0,
    n_holdings       INTEGER NOT NULL DEFAULT 0,
    n_signals        INTEGER NOT NULL DEFAULT 0,
    top_score        INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS pending_filing (
    person_id  INTEGER NOT NULL REFERENCES person(id),
    doc_code   TEXT NOT NULL,
    filed      TEXT,
    status     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS declaration (
    id               INTEGER PRIMARY KEY,
    person_id        INTEGER NOT NULL REFERENCES person(id),
    uuid             TEXT NOT NULL UNIQUE,
    doc_type         TEXT NOT NULL,        -- DI | DIA | DSP | DSPFM
    is_modification  INTEGER NOT NULL DEFAULT 0,
    filed_at         TEXT NOT NULL,
    published_at     TEXT,
    mandate_kind     TEXT,
    mandate_label    TEXT,
    mandate_start    TEXT,
    source_file      TEXT,
    status           TEXT NOT NULL,        -- published | archived
    no_holdings      INTEGER NOT NULL DEFAULT 0,
    note             TEXT
);

CREATE TABLE IF NOT EXISTS company (
    id          INTEGER PRIMARY KEY,
    slug        TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL,
    search_key  TEXT NOT NULL,
    kind        TEXT NOT NULL,             -- listed | coop_bank | unlisted | fund | other | org
    ticker      TEXT,
    legal_form  TEXT,
    n_holders   INTEGER NOT NULL DEFAULT 0,
    total_eur   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS company_sector (
    company_id  INTEGER NOT NULL REFERENCES company(id),
    sector      TEXT NOT NULL,
    PRIMARY KEY (company_id, sector)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS holding (
    id              INTEGER PRIMARY KEY,
    declaration_id  INTEGER NOT NULL REFERENCES declaration(id),
    kind            TEXT NOT NULL,         -- participation | non_cotee | sci | compte_titres
    raw_name        TEXT,
    label           TEXT NOT NULL,
    company_id      INTEGER REFERENCES company(id),
    value_eur       INTEGER,
    shares          INTEGER,
    capital_pct     REAL,
    income          TEXT,
    comment         TEXT,
    sectors         TEXT
);

CREATE TABLE IF NOT EXISTS interest (
    id              INTEGER PRIMARY KEY,
    declaration_id  INTEGER NOT NULL REFERENCES declaration(id),
    kind            TEXT NOT NULL,         -- activite | conseil | dirigeant | conjoint | collaborateur | benevole | mandat
    org             TEXT,
    org_key         TEXT,
    company_id      INTEGER REFERENCES company(id),
    description     TEXT,
    person_name     TEXT,
    start_date      TEXT,
    end_date        TEXT,
    kept            INTEGER,
    pay_json        TEXT,
    pay_max         INTEGER,
    pay_basis       TEXT
);

CREATE TABLE IF NOT EXISTS asset (
    id              INTEGER PRIMARY KEY,
    declaration_id  INTEGER NOT NULL REFERENCES declaration(id),
    kind            TEXT NOT NULL,
    label           TEXT,
    value_eur       INTEGER,
    detail          TEXT
);

CREATE TABLE IF NOT EXISTS lobby_org (
    id              INTEGER PRIMARY KEY,
    national_id     TEXT NOT NULL UNIQUE,
    name            TEXT NOT NULL,
    name_key        TEXT NOT NULL,
    category        TEXT,
    website         TEXT,
    company_id      INTEGER REFERENCES company(id),
    sectors         TEXT,
    spend_year      INTEGER,
    spend_min       INTEGER,
    spend_max       INTEGER,
    staff           REAL,
    n_actions       INTEGER NOT NULL DEFAULT 0,
    n_actions_parl  INTEGER NOT NULL DEFAULT 0,
    active          INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS lobby_year (
    org_id     INTEGER NOT NULL REFERENCES lobby_org(id),
    year       INTEGER NOT NULL,
    spend_min  INTEGER,
    spend_max  INTEGER,
    staff      REAL,
    n_actions  INTEGER,
    PRIMARY KEY (org_id, year)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS lobby_action (
    id            INTEGER PRIMARY KEY,
    org_id        INTEGER NOT NULL REFERENCES lobby_org(id),
    year          INTEGER NOT NULL,
    subject       TEXT NOT NULL,
    domains       TEXT,
    targets_parl  INTEGER NOT NULL DEFAULT 0,
    on_law        INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS organe (
    uid          TEXT PRIMARY KEY,
    kind         TEXT NOT NULL,
    label        TEXT NOT NULL,
    short        TEXT,
    legislature  INTEGER
);

CREATE TABLE IF NOT EXISTS membership (
    person_id   INTEGER NOT NULL REFERENCES person(id),
    organe_uid  TEXT NOT NULL REFERENCES organe(uid),
    role        TEXT,
    start_date  TEXT,
    end_date    TEXT
);

CREATE TABLE IF NOT EXISTS deport (
    id           INTEGER PRIMARY KEY,
    uid          TEXT NOT NULL UNIQUE,
    person_id    INTEGER NOT NULL REFERENCES person(id),
    legislature  INTEGER,
    date         TEXT,
    scope        TEXT,
    target_kind  TEXT,
    target       TEXT,
    reason       TEXT,
    bill_id      INTEGER REFERENCES bill(id)
);

CREATE TABLE IF NOT EXISTS bill (
    id             INTEGER PRIMARY KEY,
    slug           TEXT UNIQUE,            -- stable URL key
    legislature    INTEGER NOT NULL,
    key            TEXT NOT NULL,
    title          TEXT NOT NULL,
    search_key     TEXT NOT NULL,
    dossier_uid    TEXT,
    url            TEXT,
    procedure      TEXT,
    committee_uid  TEXT,
    transversal    INTEGER NOT NULL DEFAULT 0,
    budget         INTEGER NOT NULL DEFAULT 0,
    first_vote     TEXT,
    last_vote      TEXT,
    n_scrutins     INTEGER NOT NULL DEFAULT 0,
    UNIQUE (legislature, key)
);

CREATE TABLE IF NOT EXISTS bill_sector (
    bill_id  INTEGER NOT NULL REFERENCES bill(id),
    sector   TEXT NOT NULL,
    PRIMARY KEY (bill_id, sector)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS bill_role (
    bill_id    INTEGER NOT NULL REFERENCES bill(id),
    person_id  INTEGER NOT NULL REFERENCES person(id),
    role       TEXT NOT NULL,              -- rapporteur | auteur
    PRIMARY KEY (bill_id, person_id, role)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS bill_company (
    bill_id     INTEGER NOT NULL REFERENCES bill(id),
    company_id  INTEGER NOT NULL REFERENCES company(id),
    PRIMARY KEY (bill_id, company_id)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS bill_lobby (
    bill_id    INTEGER NOT NULL REFERENCES bill(id),
    action_id  INTEGER NOT NULL REFERENCES lobby_action(id),
    PRIMARY KEY (bill_id, action_id)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS scrutin (
    id            INTEGER PRIMARY KEY,
    uid           TEXT NOT NULL UNIQUE,
    legislature   INTEGER NOT NULL,
    numero        INTEGER NOT NULL,
    date          TEXT NOT NULL,
    title         TEXT NOT NULL,
    kind          TEXT NOT NULL,           -- amendement | article | ensemble | motion | autre
    outcome       TEXT,
    n_pour        INTEGER,
    n_contre      INTEGER,
    n_abstention  INTEGER,
    bill_id       INTEGER REFERENCES bill(id),
    stage         TEXT
);

CREATE TABLE IF NOT EXISTS vote (
    scrutin_id  INTEGER NOT NULL,
    person_id   INTEGER NOT NULL,
    position    INTEGER NOT NULL,
    dissent     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (scrutin_id, person_id)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS person_bill (
    person_id       INTEGER NOT NULL,
    bill_id         INTEGER NOT NULL,
    n_votes         INTEGER NOT NULL,
    n_pour          INTEGER NOT NULL,
    n_contre        INTEGER NOT NULL,
    n_abst          INTEGER NOT NULL,
    n_dissent       INTEGER NOT NULL,
    first_date      TEXT NOT NULL,
    last_date       TEXT NOT NULL,
    final_position  INTEGER,
    PRIMARY KEY (person_id, bill_id)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS position_change (
    id             INTEGER PRIMARY KEY,
    person_id      INTEGER NOT NULL REFERENCES person(id),
    company_id     INTEGER NOT NULL REFERENCES company(id),
    prev_decl_id   INTEGER NOT NULL REFERENCES declaration(id),
    curr_decl_id   INTEGER NOT NULL REFERENCES declaration(id),
    window_start   TEXT NOT NULL,
    window_end     TEXT NOT NULL,
    kind           TEXT NOT NULL,          -- entree | sortie | renforcement | allegement
    basis          TEXT NOT NULL,          -- shares | value | presence
    shares_before  INTEGER,
    shares_after   INTEGER,
    value_before   INTEGER,
    value_after    INTEGER,
    same_mandate   INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS signal (
    id          INTEGER PRIMARY KEY,
    kind        TEXT NOT NULL,
    score       INTEGER NOT NULL,
    level       TEXT NOT NULL,             -- fort | moyen | faible | info
    person_id   INTEGER REFERENCES person(id),
    company_id  INTEGER REFERENCES company(id),
    bill_id     INTEGER REFERENCES bill(id),
    date        TEXT,
    title       TEXT NOT NULL,
    summary     TEXT NOT NULL,
    evidence    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS signal_person (
    signal_id  INTEGER NOT NULL REFERENCES signal(id),
    person_id  INTEGER NOT NULL REFERENCES person(id),
    PRIMARY KEY (signal_id, person_id)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS ix_person_name       ON person(name_key);
CREATE INDEX IF NOT EXISTS ix_person_chamber    ON person(chamber, active);
CREATE INDEX IF NOT EXISTS ix_person_group      ON person(group_id);
CREATE INDEX IF NOT EXISTS ix_decl_person       ON declaration(person_id, filed_at);
CREATE INDEX IF NOT EXISTS ix_pending_person    ON pending_filing(person_id);
CREATE INDEX IF NOT EXISTS ix_holding_decl      ON holding(declaration_id);
CREATE INDEX IF NOT EXISTS ix_holding_company   ON holding(company_id);
CREATE INDEX IF NOT EXISTS ix_interest_decl     ON interest(declaration_id);
CREATE INDEX IF NOT EXISTS ix_interest_company  ON interest(company_id);
CREATE INDEX IF NOT EXISTS ix_interest_orgkey   ON interest(org_key);
CREATE INDEX IF NOT EXISTS ix_asset_decl        ON asset(declaration_id);
CREATE INDEX IF NOT EXISTS ix_lobby_company     ON lobby_org(company_id);
CREATE INDEX IF NOT EXISTS ix_lobby_key         ON lobby_org(name_key);
CREATE INDEX IF NOT EXISTS ix_action_org        ON lobby_action(org_id, year);
CREATE INDEX IF NOT EXISTS ix_membership_person ON membership(person_id);
CREATE INDEX IF NOT EXISTS ix_membership_organe ON membership(organe_uid);
CREATE INDEX IF NOT EXISTS ix_deport_person     ON deport(person_id);
CREATE INDEX IF NOT EXISTS ix_scrutin_bill      ON scrutin(bill_id, date);
CREATE INDEX IF NOT EXISTS ix_vote_person       ON vote(person_id, scrutin_id);
CREATE INDEX IF NOT EXISTS ix_pb_bill           ON person_bill(bill_id);
CREATE INDEX IF NOT EXISTS ix_change_person     ON position_change(person_id);
CREATE INDEX IF NOT EXISTS ix_change_company    ON position_change(company_id);
CREATE INDEX IF NOT EXISTS ix_signal_score      ON signal(score DESC);
CREATE INDEX IF NOT EXISTS ix_signal_person     ON signal(person_id);
CREATE INDEX IF NOT EXISTS ix_signal_company    ON signal(company_id);
CREATE INDEX IF NOT EXISTS ix_signal_bill       ON signal(bill_id);
CREATE INDEX IF NOT EXISTS ix_signal_kind       ON signal(kind, score DESC);
CREATE INDEX IF NOT EXISTS ix_sp_person         ON signal_person(person_id);
"""


def connect(path: str | Path, *, readonly: bool = False) -> sqlite3.Connection:
    """Open a connection with row access by name and sane pragmas."""
    if readonly:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
    else:
        conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = OFF")  # bulk loads insert out of order
    if not readonly:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA temp_store = MEMORY")
    conn.execute("PRAGMA cache_size = -64000")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    set_meta(conn, "schema_version", str(SCHEMA_VERSION))
    conn.commit()


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def get_meta(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    try:
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    except sqlite3.OperationalError:
        return default
    return row[0] if row else default
