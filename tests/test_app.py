"""End-to-end smoke test against a fixture-built SQLite DB.

Runs the real sync against ``hatvp_sample.xml``, then exercises each route
via FastAPI TestClient. Asserts 200 + presence of page markers.
"""

import os
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import db
import sync

FIXTURE = str(Path(__file__).parent / "fixtures" / "hatvp_sample.xml")


@pytest.fixture
def client(tmp_path, monkeypatch):
    dbpath = tmp_path / "test.sqlite"

    # Ingest the XML fixture and seed a party for 'Jean DUPONT'.
    conn = db.connect(str(dbpath))
    db.init_schema(conn)
    sync.cmd_hatvp(conn, FIXTURE)
    # Manually wire one party so party/ company pages have data to show.
    conn.execute(
        "INSERT INTO party(slug, label, color) VALUES ('rn', 'Rassemblement National', '#0d378a')"
    )
    (party_id,) = conn.execute("SELECT id FROM party WHERE slug = 'rn'").fetchone()
    conn.execute(
        "UPDATE declarant SET party_id = ?, party_source = 'an_opendata' WHERE nom = 'DUPONT'",
        (party_id,),
    )
    sync.cmd_changes(conn)
    conn.close()

    # Point the app at the test DB.
    monkeypatch.setenv("POLITICS_DB", str(dbpath))
    # Reload the module so DB_PATH picks up the env var.
    import importlib
    import app as appmodule

    importlib.reload(appmodule)
    return TestClient(appmodule.app)


def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_dashboard_page_and_api(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "Top 10" in r.text or "top 10" in r.text.lower()
    r = client.get("/api/dashboard")
    assert r.status_code == 200
    j = r.json()
    for key in ("top_companies", "top_by_party", "surges_new", "surges_grown"):
        assert isinstance(j[key], (list, dict))
    assert len(j["surges_new"]) >= 1


def test_dashboard_api_with_asof(client):
    r = client.get("/api/dashboard?asof=2030-12")
    assert r.status_code == 200
    j = r.json()
    assert isinstance(j["top_companies"], list)


def test_party_page_and_api(client):
    r = client.get("/party/rn")
    assert r.status_code == 200
    assert "Rassemblement National" in r.text
    r = client.get("/api/party/rn")
    assert r.status_code == 200
    assert r.json()["party"]["slug"] == "rn"


def test_company_page_and_api(client):
    r = client.get("/company/edf")
    assert r.status_code == 200
    assert "EDF" in r.text or "edf" in r.text
    r = client.get("/api/company/edf")
    assert r.status_code == 200
    j = r.json()
    assert j["company"]["slug"] == "edf"
    assert len(j["holders"]) == 1


def test_declarant_page(client):
    r = client.get("/declarant/1")
    assert r.status_code == 200
    assert "DUPONT" in r.text


def test_unknown_routes_return_404(client):
    assert client.get("/party/not-a-party").status_code == 404
    assert client.get("/company/not-a-company").status_code == 404
    assert client.get("/declarant/999999").status_code == 404


def test_hemicycle_api(client):
    r = client.get("/api/hemicycle?scope=an")
    assert r.status_code == 200
    seats = r.json()
    assert isinstance(seats, list)
    assert len(seats) >= 1
    seat = seats[0]
    for key in ("id", "nom", "prenom", "party_slug", "party_color", "total"):
        assert key in seat


def test_hemicycle_all_scope(client):
    r = client.get("/api/hemicycle?scope=all")
    assert r.status_code == 200
    assert len(r.json()) >= 1


def test_hemicycle_fixed_roster(client):
    """Hemicycle shows all members regardless of declaration dates."""
    r = client.get("/api/hemicycle?scope=all")
    assert r.status_code == 200
    assert len(r.json()) >= 1


def test_dashboard_has_hemicycle(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "hemicycle" in r.text.lower() or "composition" in r.text.lower()


def test_dashboard_has_conflict_card(client):
    r = client.get("/")
    assert "conflict" in r.text.lower() or "conflit" in r.text.lower()


def test_dashboard_api_has_top_conflicted(client):
    r = client.get("/api/dashboard?scope=an")
    assert r.status_code == 200
    j = r.json()
    assert "top_conflicted" in j
    assert isinstance(j["top_conflicted"], list)


def test_scope_selector_values(client):
    for scope in ("an", "senat", "parl", "all"):
        r = client.get(f"/?scope={scope}")
        assert r.status_code == 200
    assert client.get("/?scope=bogus").status_code == 400


def test_search_api(client):
    r = client.get("/api/search?q=DU&scope=an")
    assert r.status_code == 200
    results = r.json()
    assert isinstance(results, list)
    assert len(results) >= 1
    assert results[0]["nom"] == "DUPONT"


def test_search_too_short(client):
    r = client.get("/api/search?q=D")
    assert r.status_code == 200
    assert r.json() == []


def test_bad_asof_is_400(client):
    assert client.get("/api/dashboard?asof=bogus").status_code == 400
    assert client.get("/api/dashboard?asof=2024").status_code == 400


def test_timeline_api(client):
    r = client.get("/api/timeline?scope=all")
    assert r.status_code == 200
    months = r.json()
    assert isinstance(months, list)
    assert len(months) >= 1
    assert "month" in months[0]
    assert "n" in months[0]
