"""Render the whole site to static files.

The application answers every URL from its path alone, so the export is a
crawl of a known list of URLs through the real routes — no second rendering
path to keep in sync. The result can be served by any static host; ``base``
is the path it will live under (``/politics-viz`` on GitHub project pages).
"""

from __future__ import annotations

import logging
import re
import shutil
import sqlite3
from pathlib import Path

from pv import config, db
from pv.pipeline.photos import photos_dir
from pv.web import queries

log = logging.getLogger(__name__)

_ROOT_LINK = re.compile(r'(\s(?:href|src|action)=")/(?!/)')
_LINK = re.compile(r'\s(?:href|src)="([^"#?]+)[^"]*"')


def site_urls(conn: sqlite3.Connection) -> list[str]:
    """Every URL of the site, in a stable order."""
    from pv.web.app import page_url

    urls = [page_url("", s) for s in queries.SCOPES]
    for page in ("signaux", "mouvements", "elus", "societes", "reseau"):
        urls += [page_url(page, s) for s in queries.SCOPES]
    urls += ["/textes/", "/methode/", "/data/search.json"]
    urls += [f"/data/reseau-{s}.json" for s in queries.SCOPES]
    for table, prefix in (("groupe", "groupe"), ("bill", "texte"), ("company", "societe"), ("person", "personne")):
        urls += [
            f"/{prefix}/{r[0]}/" for r in conn.execute(f"SELECT slug FROM {table} WHERE slug IS NOT NULL ORDER BY slug")
        ]
    return urls


def _target(out: Path, url: str) -> Path:
    path = url.lstrip("/")
    return out / path / "index.html" if url.endswith("/") else out / path


def rebase(html: str, base: str) -> str:
    """Point root-relative links at *base* and tell the scripts about it."""
    if not base:
        return html
    html = _ROOT_LINK.sub(rf"\g<1>{base}/", html)
    return html.replace('<html lang="fr" data-base="">', f'<html lang="fr" data-base="{base}">', 1)


def export(out: Path, *, base: str = "") -> dict[str, int]:
    """Write the site under *out*. Raises if any page fails or any internal link is dead."""
    from fastapi.testclient import TestClient

    from pv.web.app import WEB_DIR, app, limiter

    base = base.rstrip("/")
    if base and not base.startswith("/"):
        base = "/" + base
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    conn = db.connect(config.DB_PATH, readonly=True)
    try:
        urls = site_urls(conn)
    finally:
        conn.close()

    saved, limiter.limit = limiter.limit, 0  # the crawl is us, not a visitor
    failed: list[str] = []
    try:
        client = TestClient(app)
        for i, url in enumerate(urls, 1):
            r = client.get(url)
            if r.status_code != 200:
                failed.append(f"{r.status_code} {url}")
                continue
            target = _target(out, url)
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.suffix == ".html":
                target.write_text(rebase(r.text, base), encoding="utf-8")
            else:
                target.write_bytes(r.content)
            if i % 1000 == 0:
                log.info("export: %d / %d pages", i, len(urls))
        missing = client.get("/introuvable/")
        (out / "404.html").write_text(rebase(missing.text, base), encoding="utf-8")
    finally:
        limiter.limit = saved
    if failed:
        raise RuntimeError(f"{len(failed)} page(s) failed to render: {failed[:5]}")

    shutil.copytree(WEB_DIR / "static", out / "static", ignore=shutil.ignore_patterns("*.md"))
    if photos_dir().is_dir():
        shutil.copytree(photos_dir(), out / "photos", ignore=shutil.ignore_patterns("_missing.json"))
    (out / ".nojekyll").write_text("")  # GitHub Pages: serve the files as they are

    broken = dead_links(out, base)
    if broken:
        raise RuntimeError(f"{len(broken)} dead internal link(s): {sorted(broken)[:5]}")
    files = [p for p in out.rglob("*") if p.is_file()]
    return {"pages": len(urls), "files": len(files), "megabytes": round(sum(p.stat().st_size for p in files) / 1e6)}


def dead_links(out: Path, base: str = "") -> set[str]:
    """Internal links and assets, across the whole export, that point at nothing."""
    seen: dict[str, bool] = {}
    broken: set[str] = set()
    for page in out.rglob("*.html"):
        for link in _LINK.findall(page.read_text(encoding="utf-8")):
            if not link.startswith(base + "/") or link.startswith("//"):
                continue
            if link not in seen:
                rel = link[len(base) :].lstrip("/")
                target = out / rel / "index.html" if link.endswith("/") else out / rel
                seen[link] = target.is_file()
            if not seen[link]:
                broken.add(link)
    return broken
