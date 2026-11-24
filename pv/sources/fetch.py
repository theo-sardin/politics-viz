"""Download upstream files into the raw cache.

Each source is fetched with a conditional GET, written atomically, and
resumed with a Range request if the connection drops mid-file (the
Assemblée nationale archive server stalls often enough to matter).
"""

from __future__ import annotations

import json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

from pv import config

log = logging.getLogger(__name__)

_UA = "politics-viz/0.2 (open-data sync)"
_TIMEOUT = httpx.Timeout(connect=20.0, read=60.0, write=60.0, pool=20.0)
_ATTEMPTS = 6


def raw_path(name: str) -> Path:
    return config.RAW_DIR / config.SOURCES[name].filename


def _meta_path(path: Path) -> Path:
    return path.with_name(path.name + ".meta.json")


def read_meta(name: str) -> dict:
    p = _meta_path(raw_path(name))
    if p.exists():
        try:
            return json.loads(p.read_text())
        except ValueError:
            return {}
    return {}


def fetch(name: str, *, force: bool = False, offline: bool = False) -> Path | None:
    """Return the local path for *name*, downloading it when stale.

    Offline, or when the server is unreachable but a cached copy exists, the
    cached copy is returned. Returns None only for an optional source that
    could not be obtained at all.
    """
    src = config.SOURCES[name]
    path = raw_path(name)
    if offline:
        if path.exists():
            return path
        if src.optional:
            return None
        raise FileNotFoundError(f"{src.filename} absent du cache ({config.RAW_DIR}) ; relancer sans --offline")

    path.parent.mkdir(parents=True, exist_ok=True)
    meta = read_meta(name)
    headers = {"User-Agent": _UA}
    if path.exists() and not force:
        if meta.get("etag"):
            headers["If-None-Match"] = meta["etag"]
        if meta.get("last_modified"):
            headers["If-Modified-Since"] = meta["last_modified"]

    try:
        return _download(src, path, headers, None if force else meta)
    except (httpx.HTTPError, OSError) as exc:
        if path.exists():
            log.warning("%s: téléchargement impossible (%s) — copie en cache utilisée", name, exc)
            return path
        if src.optional:
            log.warning("%s: source optionnelle indisponible (%s)", name, exc)
            return None
        raise


def _unchanged(response_headers: httpx.Headers, meta: dict | None, path: Path) -> bool:
    """True when a 200 response describes the very file already in the cache.

    Some servers send validators but ignore conditional requests; comparing
    them ourselves avoids pulling 140 Mo again for nothing.
    """
    if not meta or not path.exists():
        return False
    length = response_headers.get("content-length")
    if not length or int(length) != path.stat().st_size:
        return False
    etag, modified = response_headers.get("etag"), response_headers.get("last-modified")
    if etag and meta.get("etag"):
        return etag == meta["etag"]
    return bool(modified) and modified == meta.get("last_modified")


def _download(src: config.Source, path: Path, headers: dict, meta: dict | None = None) -> Path:
    part = path.with_name(path.name + ".part")
    part.unlink(missing_ok=True)
    done = 0
    response_headers: httpx.Headers | None = None

    with httpx.Client(timeout=_TIMEOUT, follow_redirects=True) as client:
        for attempt in range(1, _ATTEMPTS + 1):
            req_headers = dict(headers)
            if done:
                req_headers["Range"] = f"bytes={done}-"
                req_headers.pop("If-None-Match", None)
                req_headers.pop("If-Modified-Since", None)
            try:
                with client.stream("GET", src.url, headers=req_headers) as r:
                    if r.status_code == 304:
                        log.info("%s: à jour", src.name)
                        return path
                    if done and r.status_code == 200:
                        done = 0  # server ignored the Range header: start over
                    r.raise_for_status()
                    if not done and _unchanged(r.headers, meta, path):
                        log.info("%s: à jour", src.name)
                        return path
                    response_headers = r.headers
                    with open(part, "ab" if done else "wb") as f:
                        for chunk in r.iter_bytes(1 << 16):
                            f.write(chunk)
                            done += len(chunk)
                break
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code < 500:
                    raise
                if attempt == _ATTEMPTS:
                    raise
                log.warning("%s: interruption à %d octets (%s), reprise %d/%d", src.name, done, exc, attempt, _ATTEMPTS)
                time.sleep(min(2 * attempt, 10))

    part.replace(path)
    assert response_headers is not None
    _meta_path(path).write_text(
        json.dumps(
            {
                "url": src.url,
                "etag": response_headers.get("etag"),
                "last_modified": response_headers.get("last-modified"),
                "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "bytes": done,
            }
        )
    )
    log.info("%s: %.1f Mo téléchargés", src.name, done / 1e6)
    return path
