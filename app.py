"""FastAPI application serving the politics-viz site."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import db
import queries

BASE_DIR = Path(__file__).parent
DB_PATH = os.environ.get("POLITICS_DB", str(BASE_DIR / "data.sqlite"))

app = FastAPI(title="politics-viz", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


def get_conn() -> sqlite3.Connection:
    conn = db.connect(DB_PATH)
    try:
        yield conn
    finally:
        conn.close()


WINDOWS = {"12m", "24m", "all"}


def _validate_window(window: str) -> str:
    if window not in WINDOWS:
        raise HTTPException(status_code=400, detail=f"window must be one of {sorted(WINDOWS)}")
    return window


# -- pages --------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
def index(
    request: Request,
    window: str = Query("12m"),
    conn: sqlite3.Connection = Depends(get_conn),
):
    window = _validate_window(window)
    stats = {
        "declarants": conn.execute("SELECT COUNT(*) FROM declarant").fetchone()[0],
        "declarations": conn.execute("SELECT COUNT(*) FROM declaration").fetchone()[0],
        "holdings": conn.execute("SELECT COUNT(*) FROM holding").fetchone()[0],
    }
    return templates.TemplateResponse(
        request, "dashboard.html", {"stats": stats, "window": window}
    )


@app.get("/party/{slug}", response_class=HTMLResponse)
def party_page(
    request: Request,
    slug: str,
    window: str = Query("12m"),
    conn: sqlite3.Connection = Depends(get_conn),
):
    window = _validate_window(window)
    party = queries.get_party(conn, slug)
    if not party:
        raise HTTPException(status_code=404, detail="party not found")
    return templates.TemplateResponse(
        request,
        "party.html",
        {"party": party, "window": window},
    )


# -- JSON APIs ----------------------------------------------------------


@app.get("/api/party/{slug}")
def api_party(
    slug: str,
    window: str = Query("12m"),
    conn: sqlite3.Connection = Depends(get_conn),
) -> JSONResponse:
    window = _validate_window(window)
    party = queries.get_party(conn, slug)
    if not party:
        raise HTTPException(status_code=404, detail="party not found")
    return JSONResponse(
        {
            "party": party,
            "window": window,
            "top_companies": queries.party_top_companies(conn, slug, window),
            "members": queries.party_members(conn, slug),
            "surges": queries.party_surges(conn, slug, window),
        }
    )


@app.get("/api/dashboard")
def api_dashboard(
    window: str = Query("12m"), conn: sqlite3.Connection = Depends(get_conn)
) -> JSONResponse:
    window = _validate_window(window)
    return JSONResponse(
        {
            "window": window,
            "top_companies": queries.top_companies(conn, window),
            "top_by_party": queries.top_companies_by_party(conn, window),
            "surges_new": queries.surges(conn, window, "new"),
            "surges_grown": queries.surges(conn, window, "grown"),
        }
    )


# -- health -------------------------------------------------------------


@app.get("/healthz")
def healthz() -> JSONResponse:
    return JSONResponse({"ok": True})
