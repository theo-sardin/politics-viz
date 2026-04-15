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
        "UPDATE declarant SET party_id = ?, party_source = 'test' WHERE nom = 'DUPONT'",
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
    assert "Dashboard" in r.text
    r = client.get("/api/dashboard?window=all")
    assert r.status_code == 200
    j = r.json()
    assert len(j["top_companies"]) >= 1
    assert len(j["surges_new"]) >= 1


def test_party_page_and_api(client):
    r = client.get("/party/rn")
    assert r.status_code == 200
    assert "Rassemblement National" in r.text
    r = client.get("/api/party/rn?window=all")
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


def test_bad_window_is_400(client):
    assert client.get("/api/dashboard?window=bogus").status_code == 400
