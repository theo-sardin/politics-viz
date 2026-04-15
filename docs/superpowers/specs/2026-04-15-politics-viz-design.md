# French Politicians Investments — Visualization Site

**Date:** 2026-04-15
**Status:** Design (approved)

## Goal

A minimalist, fast website that visualizes the holdings declared by French
politicians (HATVP "Déclaration d'intérêts" data), with party-level
aggregation for current parliamentarians and surge detection across
successive declarations.

## Non-goals

- Real-time trading data (HATVP is a snapshot, not a trade log).
- Coverage of every historical mandate. Party enrichment is current-sitting
  only (Assemblée nationale, Sénat, French MEPs).
- Single-page app polish, JS framework tooling, build pipelines.

## Data sources

| Source | What | URL |
|---|---|---|
| HATVP merged declarations | All declarations (interests + assets) | https://www.hatvp.fr/livraison/merge/declarations.xml |
| NosDéputés | Current AN deputies + party affiliation | https://www.nosdeputes.fr/deputes/enmandat/json |
| data.senat.fr | Current sénateurs + party | https://data.senat.fr/data/senateurs/ODSEN_GENERAL.csv |
| European Parliament | French MEPs + group | https://www.europarl.europa.eu/meps/en/full-list/xml/fr |

Of HATVP's ~10,148 declarations, only the **PAR + EUR mandate categories
(~2,131 declarations)** can possibly be matched to a party. Everyone else
falls under `party_id = NULL` (rendered as "Non affilié / Local").

## Architecture

```
                ┌────────────────────────────┐
                │  sync.py (cron)            │
                │  • download HATVP XML      │
                │  • download NosDéputés     │
                │  • parse + upsert SQLite   │
                │  • compute holding_change  │
                └────────────┬───────────────┘
                             ▼
                       data.sqlite
                             ▲
                ┌────────────┴───────────────┐
                │  app.py (FastAPI + Jinja)  │
                │  • / dashboard             │
                │  • /party/{slug}           │
                │  • /company/{slug}         │
                │  • /declarant/{id}         │
                │  • /api/* JSON for charts  │
                └────────────┬───────────────┘
                             ▼
            vanilla HTML/CSS + Chart.js (CDN)
```

One Python process, one SQLite file, one HTML template per page, one chart
library loaded from CDN. Zero `node_modules`.

**Runtime deps:** `fastapi`, `uvicorn`, `httpx`, `lxml`, `jinja2`. Nothing
else.

## Data model (SQLite)

```sql
CREATE TABLE party (
    id          INTEGER PRIMARY KEY,
    slug        TEXT NOT NULL UNIQUE,    -- 'rn', 'lfi', 'renaissance'...
    label       TEXT NOT NULL,           -- 'Rassemblement National'
    color       TEXT NOT NULL            -- '#0d378a' (hex, hardcoded palette)
);

CREATE TABLE declarant (
    id              INTEGER PRIMARY KEY,
    nom             TEXT NOT NULL,
    prenom          TEXT NOT NULL,
    date_naissance  TEXT,                -- ISO date or NULL
    party_id        INTEGER REFERENCES party(id),
    party_source    TEXT,                -- 'nosdeputes' | 'senat' | 'europarl' | NULL
    UNIQUE(nom, prenom, date_naissance)
);

CREATE TABLE declaration (
    id              INTEGER PRIMARY KEY,
    declarant_id    INTEGER NOT NULL REFERENCES declarant(id),
    raw_uuid        TEXT NOT NULL UNIQUE,    -- HATVP UUID, dedup key
    date_depot      TEXT NOT NULL,           -- ISO datetime
    mandate_cat     TEXT,                    -- 'LOC' | 'PAR' | 'EUR' | ...
    organe_label    TEXT
);

CREATE TABLE holding (
    id                  INTEGER PRIMARY KEY,
    declaration_id      INTEGER NOT NULL REFERENCES declaration(id),
    kind                TEXT NOT NULL,
        -- 'participation_financiere' | 'valeur_bourse' | 'participation_dirigeant'
    company_name        TEXT,                -- raw name as declared
    company_slug        TEXT,                -- normalized for grouping
    evaluation_eur      INTEGER,             -- nullable (free-text in source)
    capital_pct         REAL,                -- nullable
    nb_parts            INTEGER,             -- nullable
    raw_commentaire     TEXT
);

CREATE TABLE holding_change (
    id              INTEGER PRIMARY KEY,
    declarant_id    INTEGER NOT NULL REFERENCES declarant(id),
    company_slug    TEXT NOT NULL,
    prev_decl_id    INTEGER REFERENCES declaration(id),  -- NULL if 'new'
    curr_decl_id    INTEGER NOT NULL REFERENCES declaration(id),
    delta_eur       INTEGER,
    change_type     TEXT NOT NULL    -- 'new' | 'grown' | 'reduced' | 'sold'
);

CREATE INDEX idx_holding_company       ON holding(company_slug);
CREATE INDEX idx_holding_declaration   ON holding(declaration_id);
CREATE INDEX idx_change_declarant      ON holding_change(declarant_id);
CREATE INDEX idx_change_company        ON holding_change(company_slug);
CREATE INDEX idx_change_curr_decl      ON holding_change(curr_decl_id);
CREATE INDEX idx_declaration_declarant ON declaration(declarant_id);
CREATE INDEX idx_declaration_date      ON declaration(date_depot);
```

`company_slug` normalization: lowercase, strip accents, strip common
suffixes (` SA`, ` SAS`, ` SARL`, ` SCI`, ` SC`), collapse whitespace,
trim. Stays best-effort; HATVP free-text data is messy.

## Sync layer (`sync.py`)

Single CLI with subcommands:

- `sync.py hatvp` — download XML, parse with `lxml`, upsert
  declarants/declarations/holdings. Use `iterparse` if the in-memory parse
  blows past a couple of GB on a dev laptop; otherwise the simple full-tree
  parse is fine. **Idempotent:** skip declarations whose `raw_uuid` already
  exists.
- `sync.py parties` — fetch NosDéputés JSON + Sénat CSV + EP XML, normalize
  into `(nom, prenom, date_naissance, party_label)` rows, match to
  `declarant` rows, populate `party_id` + `party_source`.
- `sync.py changes` — recompute `holding_change` from scratch (cheap,
  derived). Group by `(declarant_id, company_slug)`, walk declarations in
  chronological order, emit `new` / `grown` / `reduced` / `sold` rows.
- `sync.py all` — runs all three in order. Cron once a day.

**Matching strategy** (parties → declarants):

1. Exact: normalized last name + normalized first name + `date_naissance`.
2. Fallback: same names, no birthdate compared (HATVP doesn't always have
   it).
3. Fallback: Levenshtein ≤ 2 on last name + exact first name +
   birthdate. Logged for review.

Unmatched parliamentarians are dropped (not added as orphan declarants).

## Web layer

Routes (Jinja-rendered HTML pages + JSON APIs):

| Route | What |
|---|---|
| `/` | Dashboard (4 cards, year selector) |
| `/party/{slug}` | Party detail page |
| `/company/{slug}` | Company detail page |
| `/declarant/{id}` | Declarant detail page |
| `/api/dashboard?window=12m\|24m\|all` | JSON for dashboard charts |
| `/api/party/{slug}` | JSON for party charts |
| `/api/company/{slug}` | JSON for company timeline |

### Dashboard (`/`)

Four cards, all respect a top-level window selector (`12m` / `24m` /
`all`). Window is computed against `MAX(date_depot)` in the DB (not
`now()`) — keeps the page deterministic between syncs and avoids "empty
window" weeks if HATVP is slow to publish.

1. **Top 20 companies by total declared €** — horizontal bar.
2. **Same, stacked by party** — horizontal bar, segments coloured by party.
3. **Recent surges — new positions** — table sorted by aggregate € of
   `change_type='new'` rows in window. Columns: company, # politicians,
   total €, top party.
4. **Recent surges — biggest growths** — table sorted by sum of `delta_eur`
   for `change_type='grown'` in window.

### Party page (`/party/{slug}`)

- Top 20 companies for members of this party (bar).
- Member list (linked).
- Surges within the party in window.

### Company page (`/company/{slug}`)

- Holders list grouped by party.
- Stacked bar of total declared € per party.
- Timeline: total declared € across all holders by `date_depot` quarter.

### Declarant page (`/declarant/{id}`)

- All declarations in chronological order.
- Per declaration: holdings table (kind, company, €, %, parts).
- Change log derived from `holding_change`.

### Drilldown nav

Plain `<a href>` links. Clicking a bar in a chart navigates to the matching
detail page (chart `onclick` → `window.location`). No SPA router, no
client-side state.

## Frontend

- `static/style.css` — ~200 lines, hand-rolled. Light + dark via
  `prefers-color-scheme`.
- `static/app.js` — ~50 lines: `fetch()` JSON, hand off to Chart.js.
- Chart.js v4 from jsDelivr, single `<script>` in `base.html`.
- Total page weight target: < 150 KB gzipped per page (Chart.js + CSS + HTML).

## Party palette (hardcoded)

Standard French palette, applied via the `party.color` column:

| Party | Slug | Color |
|---|---|---|
| Rassemblement National | `rn` | `#0d378a` |
| La France Insoumise | `lfi` | `#cc2229` |
| Renaissance | `renaissance` | `#ffc832` |
| Les Républicains | `lr` | `#0066cc` |
| Parti Socialiste | `ps` | `#ff8080` |
| Les Écologistes (EELV) | `eelv` | `#77b82a` |
| Horizons | `horizons` | `#00a4a6` |
| MoDem | `modem` | `#ff8000` |
| UDI | `udi` | `#1e90ff` |
| PCF | `pcf` | `#dd0000` |
| Reconquête | `rec` | `#1c1c4f` |
| LIOT (groupe) | `liot` | `#9b59b6` |
| Non affilié / Local | `none` | `#999999` |

Unknown party labels coming from sources fall back to a stable hash colour.

## File layout

```
politics-viz/
├── data.xml                   # source, gitignored
├── data.sqlite                # gitignored
├── app.py                     # FastAPI app
├── sync.py                    # CLI sync
├── db.py                      # schema + connection helpers
├── parsers/
│   ├── __init__.py
│   ├── hatvp.py               # iterparse → records
│   ├── parties.py             # AN/Sénat/EU fetchers + matcher
│   └── normalize.py           # company_slug, name normalization
├── templates/
│   ├── base.html
│   ├── dashboard.html
│   ├── party.html
│   ├── company.html
│   └── declarant.html
├── static/
│   ├── style.css
│   └── app.js
├── pyproject.toml
└── README.md
```

## Deployment

Single Dockerfile, `uvicorn app:app` as entrypoint. SQLite file on a
mounted volume. Cron either inside the container (busybox crond) or
external (host cron / GitHub Actions hitting an authenticated
`/admin/sync` endpoint).

Target hosts: Fly.io, Render, Railway, or any small VPS. Resource
footprint: < 256 MB RAM, < 500 MB disk after sync.

## Testing

- `pytest` for parsers (sample XML fixtures + a few NosDéputés/Sénat rows).
- Smoke test for FastAPI routes (each page returns 200 with non-empty
  content).
- No `pytest-django` style mark — plain `pytest`, in-memory SQLite for
  tests.

## Acceptance criteria

Sync runs once a day, so sync perf is **"finishes in a reasonable wall
clock and doesn't OOM a laptop"** — no tight budget.

1. `sync.py all` against the real 118 MB XML completes successfully on a
   developer laptop. (Soft target: under 15 minutes, under 2 GB RAM. If
   we're way over, fix it; otherwise move on.)
2. Dashboard `/` renders in < 500 ms server-side after sync, with all four
   cards populated. (User-facing — this one matters.)
3. Drilldown: clicking a company bar on `/` navigates to
   `/company/{slug}` and renders timeline + holders.
4. Parliamentarian party-match rate is logged after `sync.py parties`.
   Floor: at least 60 % of NosDéputés entries resolve to a declarant
   (the ceiling is data availability — many former deputies have no
   HATVP declaration on file).
5. Re-running `sync.py hatvp` on unchanged input is a no-op (no inserts).
6. Total runtime JS payload per page (excluding CDN cache) is < 150 KB
   gzipped. (User-facing — this one matters.)

## Out of scope (explicitly)

- Cross-source enrichment beyond party label (e.g. linking to Wikipedia,
  enriching companies with SIRENE).
- User accounts, write operations, alerts.
- Mobile-first design (responsive desktop-first is enough).
- i18n (French only).
- Historical legislatures (only current sitting parliamentarians).
