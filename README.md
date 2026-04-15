# politics-viz

Minimalist visualization of French politicians' declared financial
interests (HATVP), with party enrichment for current parliamentarians
and surge detection across successive declarations.

## Stack

- **Backend** — Python 3.13, FastAPI, SQLite (stdlib `sqlite3`).
- **Parser** — `lxml.iterparse` streams the 118 MB HATVP XML in ~2 s,
  under 25 MB RSS.
- **Party sources** — NosDéputés (legislature 16), data.senat.fr (ACTIFS),
  europarl.europa.eu French MEPs.
- **Frontend** — Vanilla HTML/CSS + Chart.js from a CDN. No build step,
  no `node_modules`.

## Setup

```bash
python3.13 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
```

## Sync

```bash
curl -o data.xml https://www.hatvp.fr/livraison/merge/declarations.xml
python sync.py all data.xml
```

Cron once a day. Subcommands: `hatvp <xml>`, `parties`, `changes`, `all <xml>`.
Pass `--db PATH` to target an alternate SQLite file.

## Run

```bash
uvicorn app:app --reload
```

Open http://localhost:8000.

Pages:
- `/` — dashboard (top companies, per-party breakdown, surges)
- `/party/{slug}` — party detail
- `/company/{slug}` — company detail (timeline, holders)
- `/declarant/{id}` — individual declarations + change log

## Test

```bash
pytest
```

## Docker

```bash
docker build -t politics-viz .
docker run -p 8000:8000 -v $PWD/data:/data politics-viz
# run sync inside the container:
docker run --rm -v $PWD/data:/data -v $PWD/data.xml:/data.xml politics-viz \
    python sync.py all /data.xml
```

## Data caveats

- HATVP is a snapshot declaration dump, not a trade log. Surges are
  inferred from diffs between a declarant's successive declarations.
- Only `participation_financiere` entries carry both a company name and
  a euro value, so they feed the top-company charts. `valeur_bourse` and
  `participation_dirigeant` are stored but only shown on declarant pages.
- Party enrichment only covers current AN / Sénat / French MEPs (~1k
  rows). The rest (local elected, former parliamentarians who never
  re-declared) shows as "Non affilié / Local".
- AN match rate on real data: ~62 %. Many NosDéputés entries (legislature
  16) have no matching HATVP declaration on file and can never be
  matched.

## Layout

```
app.py                 FastAPI routes + templates
sync.py                CLI: hatvp / parties / changes / all
queries.py             All read-side SQL used by the web layer
db.py                  schema + connection helper
parsers/
  hatvp.py             streaming XML parser
  parties.py           AN/Sénat/MEP fetchers + matcher + palette
  normalize.py         company_slug + person_key
templates/             Jinja2: base, dashboard, party, company, declarant
static/                style.css, app.js (Chart.js helpers)
tests/                 unit + fixture-driven end-to-end
docs/superpowers/      spec + implementation plan
```
