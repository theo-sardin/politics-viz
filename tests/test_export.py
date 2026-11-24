"""The static export: every page written, every internal link resolving."""

import json

import pytest

from pv import config
from pv.web import export as exporter


@pytest.fixture(scope="module")
def site(built_db, tmp_path_factory):
    out = tmp_path_factory.mktemp("export") / "site"
    saved = config.DB_PATH
    config.DB_PATH = built_db
    try:
        stats = exporter.export(out, base="/politics-viz")
    finally:
        config.DB_PATH = saved
    return out, stats


def test_every_kind_of_page_is_written(site):
    out, stats = site
    for rel in (
        "index.html",
        "senat/index.html",
        "tous/index.html",
        "signaux/index.html",
        "signaux/gouv/index.html",
        "elus/senat/index.html",
        "textes/index.html",
        "methode/index.html",
        "404.html",
        ".nojekyll",
        "personne/martin-alice-1/index.html",
        "societe/totalenergies/index.html",
        "groupe/an-epr/index.html",
        "data/search.json",
        "data/reseau-an.json",
        "static/style.css",
        "static/vendor/d3.min.js",
    ):
        assert (out / rel).is_file(), rel
    assert stats["pages"] >= 40 and stats["files"] > stats["pages"]
    assert json.loads((out / "data/search.json").read_text())[0]["u"].startswith("/personne/")


def test_links_and_scripts_know_the_base_path(site):
    out, _ = site
    html = (out / "personne/martin-alice-1/index.html").read_text()
    assert '<html lang="fr" data-base="/politics-viz">' in html
    assert 'href="/politics-viz/static/style.css' in html
    assert 'href="/politics-viz/societe/totalenergies/"' in html
    assert 'href="/societe/' not in html and 'src="/static/' not in html
    assert 'href="https://www.hatvp.fr/' in html  # external links are left alone
    assert 'href="/politics-viz/"' in (out / "404.html").read_text()


def test_no_internal_link_is_dead(site):
    out, _ = site
    assert exporter.dead_links(out, "/politics-viz") == set()
    (out / "societe/totalenergies/index.html").unlink()
    assert exporter.dead_links(out, "/politics-viz") == {"/politics-viz/societe/totalenergies/"}


def test_rebase_is_a_no_op_at_a_domain_root():
    html = '<html lang="fr" data-base=""><a href="/x/">x</a>'
    assert exporter.rebase(html, "") == html
    assert exporter.rebase(html, "/b") == '<html lang="fr" data-base="/b"><a href="/b/x/">x</a>'
    assert exporter.rebase('<a href="//cdn.example/x">', "/b") == '<a href="//cdn.example/x">'


def test_a_failing_page_stops_the_export(built_db, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", built_db)
    monkeypatch.setattr(exporter, "site_urls", lambda conn: ["/", "/personne/nobody/"])
    with pytest.raises(RuntimeError, match="failed to render"):
        exporter.export(tmp_path / "site")
