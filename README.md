# politics-viz

Minimalist visualization of French politicians' declared financial
interests (HATVP), with party enrichment for current parliamentarians
and surge detection across successive declarations.

## Setup

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
```

## Sync

```bash
# download HATVP XML once
curl -o data.xml https://www.hatvp.fr/livraison/merge/declarations.xml

# full sync: ingest XML, fetch party data, compute changes
python sync.py all data.xml
```

Cron this once a day.

## Run

```bash
uvicorn app:app --reload
```

Open http://localhost:8000.

## Test

```bash
pytest
```
