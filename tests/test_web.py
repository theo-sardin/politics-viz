import re

import pytest

from pv.web.app import page_url


def test_page_url_puts_the_scope_in_the_path():
    assert page_url() == "/"
    assert page_url("", "senat") == "/senat/"
    assert page_url("signaux") == "/signaux/"
    assert page_url("signaux", "tous") == "/signaux/tous/"


@pytest.mark.parametrize(
    "path, marker",
    [
        ("/", "Qui détient quoi"),
        ("/senat/", "Sénat"),
        ("/tous/", "Tous les déclarants"),
        ("/signaux/", "Signaux"),
        ("/signaux/tous/", "TotalEnergies"),
        ("/mouvements/", "TotalEnergies"),
        ("/elus/", "Alice Martin"),
        ("/elus/senat/", "Denis Leroy"),
        ("/societes/tous/", "TotalEnergies"),
        ("/textes/", "énergies renouvelables"),
        ("/reseau/", "Qui détient les mêmes sociétés"),
        ("/methode/", "Ce qu'est un signal"),
        ("/personne/martin-alice-1/", "Participations, déclaration après déclaration"),
        ("/personne/leroy-denis-s19999x/", "Aucune déclaration publiée"),
        ("/societe/totalenergies/", "Lobbying déclaré"),
        ("/societe/societe-generale/", "Autres liens déclarés"),
        ("/groupe/an-epr/", "Ensemble pour la République"),
    ],
)
def test_pages_render(client, path, marker):
    r = client.get(path)
    assert r.status_code == 200, r.text[:400]
    assert marker in r.text


def test_a_query_string_changes_nothing_on_the_server(client):
    # Filters run in the browser: that is what makes a static export possible.
    assert client.get("/signaux/?niveau=fort&q=xyz").text == client.get("/signaux/").text


def test_list_rows_carry_what_the_browser_filters_on(client):
    html = client.get("/signaux/").text
    (card,) = [c for c in re.findall(r"<article[^>]*data-item[^>]*>", html) if "mouvement_vote" in c]
    assert 'data-niveau="fort"' in card and 'data-groupe="an-epr"' in card
    assert 'data-type="mouvement_vote votes"' in card  # the kind, and the family the filter offers
    assert "alice martin" in card and "totalenergies" in card  # folded text for the search box
    people = client.get("/elus/").text
    assert re.search(r'<tr data-item data-groupe="an-epr" data-text="alice martin" data-sort-portfolio="48000"', people)
    bills = client.get("/textes/").text
    assert 'data-secteur="energie" data-exposes="1"' in bills


def test_bill_page_shows_exposed_voters_lobbying_and_recusals(client, conn):
    slug = conn.execute("SELECT slug FROM bill WHERE title LIKE '%renouvelables%'").fetchone()[0]
    r = client.get(f"/texte/{slug}/")
    assert r.status_code == 200
    for marker in ("Alice Martin", "TOTALENERGIES SE", "Déports sur ce texte", "Bruno Petit", "Rapporteur"):
        assert marker in r.text


def test_person_page_links_to_the_source_documents(client):
    html = client.get("/personne/martin-alice-1/").text
    assert "https://www.hatvp.fr/livraison/dossiers/martin-alice-diam2.pdf" in html
    assert "https://www.hatvp.fr/pages_nominatives/martin-alice-1" in html
    assert "/dyn/17/scrutins/1" in html  # the roll call behind the signal


@pytest.mark.parametrize(
    "path",
    [
        "/personne/nobody/",
        "/societe/nothing/",
        "/texte/l17-none/",
        "/groupe/an-xx/",
        "/an/",
        "/signaux/an/",
        "/signaux/bogus/",
        "/nowhere/",
        "/data/reseau-bogus.json",
    ],
)
def test_unknown_things_are_404(client, path):
    assert client.get(path).status_code == 404


def test_pages_ask_search_engines_to_stay_out_unless_told_otherwise(client, monkeypatch):
    assert '<meta name="robots" content="noindex, nofollow">' in client.get("/").text
    monkeypatch.setenv("PV_INDEXABLE", "1")
    assert "noindex" not in client.get("/").text


def test_search_index_and_network_data(client):
    index = client.get("/data/search.json").json()
    assert {
        "t": "Élu",
        "l": "Alice Martin",
        "s": "EPR · Députée · Rhône (69), 1re circ.",
        "u": "/personne/martin-alice-1/",
        "k": "alice martin",
    } in index
    assert [r["t"] for r in index if "totalenergies" in r["k"]] == ["Société"]
    assert any(r["t"] == "Texte" and "energies renouvelables" in r["k"] for r in index)
    assert client.get("/data/reseau-tous.json").json() == {"nodes": [], "links": []}  # nobody shares a company


def test_healthz(client):
    assert client.get("/healthz").json() == {"ok": True, "db": True}


def test_missing_database_is_a_503_not_a_crash(client, monkeypatch, tmp_path):
    from pv import config

    monkeypatch.setattr(config, "DB_PATH", tmp_path / "absent.sqlite")
    r = client.get("/")
    assert r.status_code == 503 and "pv sync" in r.text
