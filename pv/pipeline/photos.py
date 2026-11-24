"""Mirror official portraits locally, as small thumbnails.

The HATVP index points at portraits on assemblee-nationale.fr and senat.fr.
Embedding those URLs made every visitor's browser call both sites, and
served a 57 KB image for a 36 px avatar. Portraits are fetched once here,
shrunk, and served from our own origin; a link that is dead (about half of
the Sénat ones are) is remembered so it is not retried every night.
"""

from __future__ import annotations

import io
import json
import logging
import sqlite3
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
from PIL import Image

from pv import config

log = logging.getLogger(__name__)

SIZE = (192, 256)  # displayed at 36 px and 84 px; this covers high-density screens
RETRY_MISSING_AFTER = 30 * 86400
_WORKERS = 6
_MAX_BYTES = 3_000_000

Fetch = Callable[[str], bytes | None]


def photos_dir() -> Path:
    return config.DATA_DIR / "photos"


def _http_fetch(client: httpx.Client) -> Fetch:
    def fetch(url: str) -> bytes | None:
        try:
            r = client.get(url)
        except httpx.HTTPError:
            return None
        if r.status_code != 200 or not r.headers.get("content-type", "").startswith("image/"):
            return None
        return r.content if len(r.content) <= _MAX_BYTES else None

    return fetch


def _thumbnail(raw: bytes) -> bytes | None:
    try:
        img = Image.open(io.BytesIO(raw))
        img = img.convert("RGB")
        img.thumbnail(SIZE, Image.LANCZOS)
        out = io.BytesIO()
        img.save(out, "JPEG", quality=82, optimize=True)
        return out.getvalue()
    except Exception:  # a truncated or mislabelled file is simply "no portrait"
        return None


def sync_photos(
    conn: sqlite3.Connection,
    dest: Path | None = None,
    *,
    offline: bool = False,
    fetch: Fetch | None = None,
) -> dict[str, int]:
    """Download what is missing, then point ``person.photo_url`` at the local copy.

    People whose portrait could not be obtained end up with no photo: the
    templates fall back to their initials.
    """
    dest = dest or photos_dir()
    dest.mkdir(parents=True, exist_ok=True)
    missing_path = dest / "_missing.json"
    try:
        missing: dict[str, float] = json.loads(missing_path.read_text())
    except (OSError, ValueError):
        missing = {}
    now = time.time()

    rows = conn.execute("SELECT id, slug, photo_url FROM person WHERE photo_url LIKE 'http%'").fetchall()
    stats = {"people": len(rows), "downloaded": 0, "unavailable": 0, "local": 0}
    todo = [
        r
        for r in rows
        if not (dest / f"{r['slug']}.jpg").exists() and now - missing.get(r["slug"], 0) > RETRY_MISSING_AFTER
    ]

    if todo and not offline:
        client = None
        if fetch is None:
            client = httpx.Client(
                timeout=20.0, follow_redirects=True, headers={"User-Agent": "politics-viz/0.2 (portrait cache)"}
            )
            fetch = _http_fetch(client)

        def work(row) -> tuple[str, bytes | None]:
            raw = fetch(row["photo_url"])
            return row["slug"], _thumbnail(raw) if raw else None

        try:
            with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
                for slug, thumb in pool.map(work, todo):
                    if thumb is None:
                        missing[slug] = now
                        stats["unavailable"] += 1
                    else:
                        (dest / f"{slug}.jpg").write_bytes(thumb)
                        missing.pop(slug, None)
                        stats["downloaded"] += 1
        finally:
            if client is not None:
                client.close()
        missing_path.write_text(json.dumps(missing))

    updates = []
    for r in rows:
        path = dest / f"{r['slug']}.jpg"
        if path.exists():
            # The version makes the URL change whenever the file does, so it can be cached for good.
            updates.append((f"/photos/{r['slug']}.jpg?v={int(path.stat().st_mtime)}", r["id"]))
            stats["local"] += 1
        else:
            updates.append((None, r["id"]))
    conn.executemany("UPDATE person SET photo_url = ? WHERE id = ?", updates)
    return stats
