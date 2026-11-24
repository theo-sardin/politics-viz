"""Caching, request budget and browser-side lockdown."""

import io
import os
import re
from html import unescape

import pytest
from PIL import Image

from pv import config, db
from pv.pipeline import photos
from pv.web.hardening import RateLimiter

PAGES = [
    "/",
    "/signaux/",
    "/elus/",
    "/societes/tous/",
    "/textes/",
    "/reseau/",
    "/methode/",
    "/personne/martin-alice-1/",
    "/societe/totalenergies/",
    "/groupe/an-epr/",
]


# -- caching ---------------------------------------------------------------


def test_a_page_is_cacheable_and_revalidates_for_free(client):
    first = client.get("/")
    assert first.headers["cache-control"].startswith("public, max-age=")
    etag = first.headers["etag"]
    again = client.get("/", headers={"If-None-Match": etag})
    assert again.status_code == 304 and again.content == b""
    assert again.headers["etag"] == etag
    assert client.get("/senat/").headers["etag"] != etag  # another page, another tag


def test_a_new_database_invalidates_every_page(client, built_db):
    etag = client.get("/").headers["etag"]
    stat = built_db.stat()
    os.utime(built_db, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
    try:
        assert client.get("/", headers={"If-None-Match": etag}).status_code == 200
    finally:
        os.utime(built_db, ns=(stat.st_atime_ns, stat.st_mtime_ns))


def test_errors_are_never_cached(client):
    assert client.get("/personne/nobody/").headers["cache-control"] == "no-store"


def test_versioned_assets_are_cached_for_good(client):
    assert "immutable" in client.get("/static/style.css?v=123").headers["cache-control"]
    assert client.get("/static/style.css").headers["cache-control"] == "public, max-age=3600"


# -- nothing from anywhere else ----------------------------------------------


@pytest.mark.parametrize("path", ["/", "/personne/nobody/", "/data/search.json", "/static/style.css", "/healthz"])
def test_security_headers_on_every_response(client, path):
    h = client.get(path).headers
    assert "script-src 'self'" in h["content-security-policy"]
    assert "frame-ancestors 'none'" in h["content-security-policy"]
    assert h["x-content-type-options"] == "nosniff"
    assert h["x-frame-options"] == "DENY"


@pytest.mark.parametrize("path", PAGES)
def test_pages_have_no_inline_script_and_load_nothing_third_party(client, path):
    html = client.get(path).text
    meta = re.search(r'http-equiv="Content-Security-Policy" content="([^"]+)"', html).group(1)
    assert "script-src 'self'" in unescape(meta)  # the fallback for hosts where headers cannot be set
    assert not re.search(r"<script(?![^>]*\bsrc=)", html), "inline <script> would be blocked by the CSP"
    assert not re.search(r"\son[a-z]+=\"", html), "inline event handler would be blocked by the CSP"
    loaded = re.findall(r"<(?:script|link|img)[^>]*?\s(?:src|href)=\"([^\"]+)\"", html)
    assert loaded and all(u.startswith("/") and not u.startswith("//") for u in loaded if "example.org" not in u), (
        loaded
    )  # example.org: the fixture's fake portrait


def test_d3_is_served_locally(client):
    assert "/static/vendor/d3.min.js" in client.get("/reseau/").text
    r = client.get("/static/vendor/d3.min.js")
    assert r.status_code == 200 and r.text.startswith("// https://d3js.org v7.9.0")


# -- request budget ----------------------------------------------------------


def test_rate_limiter_counts_per_client_over_a_sliding_window():
    rl = RateLimiter(3, window=60)
    assert [rl.check("a", now=t) for t in (0, 1, 2)] == [0, 0, 0]
    assert rl.check("a", now=3) == pytest.approx(57)  # the first hit leaves the window at t=60
    assert rl.check("b", now=3) == 0  # someone else is unaffected
    assert rl.check("a", now=60.5) == 0
    assert RateLimiter(0).check("a") == 0  # 0 disables it


def test_rate_limiter_memory_is_bounded():
    rl = RateLimiter(5, window=60, max_clients=100)
    for i in range(1000):
        rl.check(f"client-{i}", now=i * 0.01)
    assert len(rl.hits) <= 100


def test_too_many_requests_get_a_429_but_assets_are_exempt(client, monkeypatch):
    from pv.web import app as appmod

    appmod.limiter.hits.clear()
    monkeypatch.setattr(appmod.limiter, "limit", 2)
    try:
        assert [client.get("/").status_code for _ in range(3)] == [200, 200, 429]
        blocked = client.get("/signaux/")
        assert blocked.status_code == 429 and int(blocked.headers["retry-after"]) >= 1
        assert blocked.headers["cache-control"] == "no-store"
        assert client.get("/static/style.css").status_code == 200
        assert client.get("/healthz").status_code == 200
        assert client.get("/healthz-but-not-really").status_code == 429  # the exemption is exact
    finally:
        appmod.limiter.hits.clear()


# -- portraits ---------------------------------------------------------------


def _jpeg(width=600, height=800) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (width, height), (120, 90, 60)).save(out, "JPEG")
    return out.getvalue()


@pytest.fixture
def people_db(tmp_path):
    conn = db.connect(tmp_path / "p.sqlite")
    db.init_schema(conn)
    for slug, url in (
        ("martin-alice-1", "https://an.example/1.jpg"),
        ("leroy-denis", "https://senat.example/dead.jpg"),
        ("durand-chloe", None),
    ):
        conn.execute(
            "INSERT INTO person(slug, prenom, nom, name_key, search_key, photo_url) VALUES (?, 'x', 'y', 'k', 's', ?)",
            (slug, url),
        )
    conn.commit()
    yield conn
    conn.close()


def test_portraits_are_mirrored_as_thumbnails_and_dead_links_remembered(people_db, tmp_path):
    dest = tmp_path / "photos"
    calls = []

    def fetch(url):
        calls.append(url)
        return _jpeg() if "an.example" in url else None

    stats = photos.sync_photos(people_db, dest, fetch=fetch)
    assert stats == {"people": 2, "downloaded": 1, "unavailable": 1, "local": 1}
    thumb = Image.open(dest / "martin-alice-1.jpg")
    assert thumb.size == (192, 256) and (dest / "martin-alice-1.jpg").stat().st_size < 10_000
    urls = dict(people_db.execute("SELECT slug, photo_url FROM person"))
    assert urls["martin-alice-1"].startswith("/photos/martin-alice-1.jpg?v=")
    assert urls["leroy-denis"] is None and urls["durand-chloe"] is None


def test_a_second_sync_downloads_nothing(people_db, tmp_path):
    dest = tmp_path / "photos"
    photos.sync_photos(people_db, dest, fetch=lambda url: _jpeg() if "an.example" in url else None)
    people_db.execute("UPDATE person SET photo_url = 'https://an.example/1.jpg' WHERE slug = 'martin-alice-1'")
    people_db.execute("UPDATE person SET photo_url = 'https://senat.example/dead.jpg' WHERE slug = 'leroy-denis'")

    def must_not_fetch(url):
        raise AssertionError(f"refetched {url}")

    stats = photos.sync_photos(people_db, dest, fetch=must_not_fetch)
    assert stats["downloaded"] == 0 and stats["local"] == 1


def test_offline_sync_uses_what_is_there(people_db, tmp_path):
    stats = photos.sync_photos(people_db, tmp_path / "photos", offline=True)
    assert stats["local"] == 0
    assert people_db.execute("SELECT COUNT(*) FROM person WHERE photo_url IS NOT NULL").fetchone()[0] == 0


def test_a_corrupt_image_is_treated_as_no_portrait(people_db, tmp_path):
    stats = photos.sync_photos(people_db, tmp_path / "photos", fetch=lambda url: b"<html>not an image</html>")
    assert stats["downloaded"] == 0 and stats["unavailable"] == 2


def test_portrait_route_serves_only_plain_jpeg_names(client, monkeypatch, tmp_path):
    (tmp_path / "photos").mkdir()
    (tmp_path / "photos" / "martin-alice-1.jpg").write_bytes(_jpeg(192, 256))
    (tmp_path / "secret.txt").write_text("no")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    ok = client.get("/photos/martin-alice-1.jpg?v=1")
    assert ok.status_code == 200 and ok.headers["content-type"] == "image/jpeg"
    assert "immutable" in ok.headers["cache-control"]
    for bad in ("/photos/nobody.jpg", "/photos/..%2Fsecret.txt", "/photos/secret.txt", "/photos/Martin.JPG"):
        assert client.get(bad).status_code == 404
