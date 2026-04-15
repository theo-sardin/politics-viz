# Politics-Viz Implementation Plan

**Goal:** Ship a minimalist FastAPI+SQLite site visualizing HATVP French politician holdings with party enrichment and surge detection.

**Architecture:** Daily `sync.py` CLI downloads HATVP XML + NosDéputés/Sénat/EP data, upserts into SQLite. `app.py` (FastAPI + Jinja) serves 4 pages with Chart.js from CDN. No JS build step.

**Tech Stack:** Python 3.12, FastAPI, Uvicorn, httpx, lxml, Jinja2, pytest. Chart.js v4 via jsDelivr.

---

## Workflow per task

1. Implement the slice.
2. Run tests (and/or smoke-test the route).
3. **Re-read the diff, simplify, delete dead code.**
4. Commit with a focused message.

Commits stay small and single-responsibility.

---

### Task 1 — Project bootstrap

**Files:** `.gitignore`, `pyproject.toml`, `README.md`, empty `__init__.py` in packages.

- `git init`
- `pyproject.toml` with deps: `fastapi`, `uvicorn[standard]`, `httpx`, `lxml`, `jinja2`, `pytest` (dev).
- `.gitignore`: `.venv/`, `data.sqlite`, `data.xml`, `__pycache__/`, `*.pyc`, `.pytest_cache/`.
- Empty `parsers/__init__.py`, `tests/__init__.py`.
- `README.md` one-pager: what, setup, sync, run.
- Commit: `chore: project bootstrap`.

### Task 2 — SQLite schema module (`db.py`)

**Files:** `db.py`, `tests/test_db.py`.

- `connect(path)` returns `sqlite3.Connection` with `row_factory=sqlite3.Row`, `PRAGMA foreign_keys=ON`.
- `init_schema(conn)` executes CREATE TABLE IF NOT EXISTS for all 5 tables (party, declarant, declaration, holding, holding_change) + indexes from the spec.
- Test: `init_schema` is idempotent; running twice doesn't error.
- Commit: `feat(db): schema + connection helper`.

### Task 3 — Normalization helpers (`parsers/normalize.py`)

**Files:** `parsers/normalize.py`, `tests/test_normalize.py`.

- `strip_accents(s)` — unicodedata NFKD + ASCII filter.
- `company_slug(name)` — lowercase, strip accents, drop `SA|SAS|SARL|SCI|SC|SASU|EURL` suffixes, collapse whitespace, `[^a-z0-9]+` → `-`.
- `person_key(nom, prenom)` — similar, for name matching.
- Tests: accent folding, suffix trimming, empty input.
- Commit: `feat(normalize): company+person slug helpers`.

### Task 4 — HATVP XML parser (`parsers/hatvp.py`)

**Files:** `parsers/hatvp.py`, `tests/test_hatvp.py`, `tests/fixtures/hatvp_sample.xml`.

- `parse(path) -> Iterator[DeclarationRecord]` using `lxml.etree.iterparse` on `<declaration>` tags, clearing elements after processing to stay memory-bounded.
- `DeclarationRecord` dataclass: uuid, date_depot (ISO), mandate_cat, organe_label, declarant (nom, prenom, date_naissance), holdings: list of (kind, company_name, evaluation_eur, capital_pct, nb_parts, commentaire).
- `_parse_eur(s)` — strip spaces, commas, `€` → int or None.
- Sources extracted: `participationFinanciereDto` (→ kind=`participation_financiere`, uses `nomSociete`), `valeursEnBourseDto` (→ kind=`valeur_bourse`, uses `etablissement` as fallback company name), `participationDirigeantDto` (→ kind=`participation_dirigeant`).
- Tests: fixture XML with 2 declarations, parser yields 2 records with expected holdings.
- Commit: `feat(parsers): hatvp xml streaming parser`.

### Task 5 — HATVP ingestion in `sync.py`

**Files:** `sync.py`, `tests/test_sync_hatvp.py`.

- `cmd_hatvp(conn, xml_path)` — iterate parser records, upsert declarant (by normalized key), insert declaration if raw_uuid is new, insert holdings.
- Idempotent: second call with same XML inserts 0 new rows.
- CLI entry: `python sync.py hatvp path/to/data.xml`.
- Test: run against fixture, assert row counts; run again, assert unchanged.
- Commit: `feat(sync): hatvp ingest into sqlite`.

### Task 6 — Parties fetchers + matcher (`parsers/parties.py`)

**Files:** `parsers/parties.py`, `tests/test_parties.py`.

- `fetch_an() -> list[PersonParty]` — GET `https://www.nosdeputes.fr/deputes/enmandat/json`, extract (nom, prenom, date_naissance, party_slug, party_label).
- `fetch_senat() -> list[PersonParty]` — GET the ODSEN CSV, parse.
- `fetch_europarl_fr() -> list[PersonParty]` — GET the XML, filter country=FR.
- `PALETTE` dict for the 13 slugs from the spec; `party_for(label)` maps free-text labels to slug+color, unknown → stable hash color.
- `match(conn, people)` — for each person, find declarant by (person_key, date_naissance), else by person_key alone, else fuzzy lastname (Levenshtein ≤ 2). Populate `party_id` + `party_source`. Log match-rate.
- Tests: `party_for` for known + unknown labels; `match` against fixture declarants.
- Commit: `feat(parties): fetchers + matcher`.

### Task 7 — Holding change computation

**Files:** extend `sync.py`, `tests/test_sync_changes.py`.

- `cmd_changes(conn)` — `DELETE FROM holding_change`, then for each (declarant_id, company_slug), walk declarations chronologically; emit `new` on first appearance, `grown`/`reduced`/`sold` on subsequent, with `delta_eur = curr - prev`.
- Test: fixture with 3 declarations of same person over time; assert expected change rows.
- Commit: `feat(sync): compute holding_change deltas`.

### Task 8 — Sync CLI glue

**Files:** extend `sync.py`.

- argparse with subcommands: `hatvp <path>`, `parties`, `changes`, `all <path>`.
- `cmd_all` runs hatvp → parties → changes in one transaction per stage.
- Commit: `feat(sync): top-level CLI with subcommands`.

### Task 9 — FastAPI skeleton + base template

**Files:** `app.py`, `templates/base.html`, `static/style.css`, `static/app.js`.

- `app = FastAPI()`, mount `static/`, Jinja2Templates on `templates/`.
- `get_conn()` dependency opens SQLite read-only.
- `base.html` — doctype, `<nav>`, `<main>` block, Chart.js `<script>` from jsDelivr, `app.js` script.
- `style.css` — CSS variables for palette, light/dark via `prefers-color-scheme`, grid layout for dashboard cards, table styles. Keep under ~200 lines.
- `app.js` — `renderBarChart(canvasId, url)`, `renderStackedBar`, `renderTable` helpers.
- Route `/healthz` → `{ok: true}`.
- Commit: `feat(web): fastapi skeleton + base template`.

### Task 10 — Dashboard page + API

**Files:** `templates/dashboard.html`, extend `app.py`.

- `GET /` renders dashboard.html with window selector links (12m/24m/all).
- `GET /api/dashboard?window=…` returns JSON for all 4 cards:
  - `top_companies`: [{slug, name, total_eur}] limit 20, filtered by date_depot ≥ window_start.
  - `top_by_party`: same but grouped by party (datasets per party).
  - `surges_new`: from holding_change where change_type='new' and curr declaration in window, aggregated by company.
  - `surges_grown`: same but change_type='grown', sum of delta_eur.
- Window anchor = `MAX(date_depot)` in DB.
- `dashboard.html` — 4 `<section class="card">` with canvas or table, window selector.
- Manual smoke test: load `/`, verify all 4 cards populated.
- Commit: `feat(web): dashboard page + api`.

### Task 11 — Party detail page + API

**Files:** `templates/party.html`, extend `app.py`.

- `GET /party/{slug}` page.
- `GET /api/party/{slug}` returns top companies for that party + member count + party-scope surges.
- Page: header with party label+color, top-20 bar, member list (linked to /declarant/{id}), surge table.
- Commit: `feat(web): party detail page + api`.

### Task 12 — Company detail page + API

**Files:** `templates/company.html`, extend `app.py`.

- `GET /company/{slug}` page.
- `GET /api/company/{slug}` returns holders grouped by party, quarterly timeline of total declared €.
- Page: stacked bar by party, timeline chart, holders table (linked to /declarant/{id}).
- Drill-down wire-up: in dashboard.html's `top_companies` bar, on-click → navigate to `/company/{slug}`.
- Commit: `feat(web): company detail page + api + drilldown`.

### Task 13 — Declarant detail page

**Files:** `templates/declarant.html`, extend `app.py`.

- `GET /declarant/{id}` page.
- Renders server-side (no API needed): header (name, party badge, mandate), declarations table chronologically, holdings per declaration, change log from `holding_change`.
- Commit: `feat(web): declarant detail page`.

### Task 14 — End-to-end smoke test

**Files:** `tests/test_app.py`.

- Use TestClient to hit `/`, `/party/rn`, `/company/edf`, `/declarant/1` after a small fixture-based sync.
- Assert 200 and non-empty body markers.
- Commit: `test: end-to-end smoke test`.

### Task 15 — Dockerfile + run docs

**Files:** `Dockerfile`, update `README.md`.

- Python 3.12 slim, `pip install .`, expose 8000, `uvicorn app:app --host 0.0.0.0 --port 8000`.
- README: setup (`.venv` + `pip install -e .[dev]`), run sync, run server, run tests, run via Docker.
- Commit: `chore: dockerfile + readme`.

---

## Acceptance (from spec)

- [ ] Sync completes on real 118 MB XML without OOM.
- [ ] `/` renders < 500 ms with 4 cards populated.
- [ ] Click company bar → `/company/{slug}` loads with timeline + holders.
- [ ] ≥ 80 % AN députés matched to party (logged).
- [ ] Second `sync.py hatvp` on unchanged input: 0 new rows.
- [ ] Per-page JS (excl. CDN) < 150 KB gzipped.

After all tasks are done, run the real sync end-to-end and tick acceptance.
