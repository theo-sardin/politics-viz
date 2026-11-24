import pytest


@pytest.mark.parametrize(
    "path, marker",
    [
        ("/", "Qui détient quoi"),
        ("/?scope=senat", "Sénat"),
        ("/?scope=tous", "Tous les déclarants"),
        ("/signaux", "Signaux"),
        ("/signaux?niveau=fort&type=votes&tri=date&q=total", "TotalEnergies"),
        ("/mouvements?type=renforcement", "TotalEnergies"),
        ("/elus?tri=portfolio", "Alice Martin"),
        ("/societes?scope=tous&secteur=energie", "TotalEnergies"),
        ("/textes?exposes=1", "énergies renouvelables"),
        ("/reseau", "Qui détient les mêmes sociétés"),
        ("/methode", "Ce qu'est un signal"),
        ("/personne/martin-alice-1", "Participations, déclaration après déclaration"),
        ("/personne/leroy-denis-s19999x", "Aucune déclaration publiée"),
        ("/societe/totalenergies", "Lobbying déclaré"),
        ("/societe/societe-generale", "Autres liens déclarés"),
        ("/groupe/an-epr", "Ensemble pour la République"),
    ],
)
def test_pages_render(client, path, marker):
    r = client.get(path)
    assert r.status_code == 200, r.text[:400]
    assert marker in r.text


def test_bill_page_shows_exposed_voters_lobbying_and_recusals(client, conn):
    slug = conn.execute("SELECT slug FROM bill WHERE title LIKE '%renouvelables%'").fetchone()[0]
    r = client.get(f"/texte/{slug}")
    assert r.status_code == 200
    for marker in ("Alice Martin", "TOTALENERGIES SE", "Déports sur ce texte", "Bruno Petit", "Rapporteur"):
        assert marker in r.text


def test_person_page_links_to_the_source_documents(client):
    html = client.get("/personne/martin-alice-1").text
    assert "https://www.hatvp.fr/livraison/dossiers/martin-alice-diam2.pdf" in html
    assert "https://www.hatvp.fr/pages_nominatives/martin-alice-1" in html
    assert "/dyn/17/scrutins/1" in html  # the roll call behind the signal


def test_unknown_things_are_404(client):
    for path in ("/personne/nobody", "/societe/nothing", "/texte/l17-none", "/groupe/an-xx"):
        assert client.get(path).status_code == 404


def test_unknown_scope_falls_back_to_the_default(client):
    assert client.get("/?scope=bogus").status_code == 200


def test_json_endpoints(client):
    assert client.get("/healthz").json() == {"ok": True, "db": True}
    hits = client.get("/api/search", params={"q": "martin"}).json()
    assert hits[0]["url"] == "/personne/martin-alice-1"
    graph = client.get("/api/reseau", params={"scope": "tous", "min": 2}).json()
    assert graph == {"nodes": [], "links": []}  # nobody shares a company in the fixture


def test_old_company_urls_redirect(client):
    r = client.get("/company/sanofi", follow_redirects=False)
    assert r.status_code == 308 and r.headers["location"] == "/societe/sanofi"


def test_missing_database_is_a_503_not_a_crash(client, monkeypatch, tmp_path):
    from pv import config

    monkeypatch.setattr(config, "DB_PATH", tmp_path / "absent.sqlite")
    r = client.get("/")
    assert r.status_code == 503 and "pv sync" in r.text
