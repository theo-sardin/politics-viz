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

# Cache-bust static assets on restart so edits reach the browser without
# manual hard-refresh. Cheap enough to compute once at import time.
ASSET_VERSION = str(
    max(
        (BASE_DIR / "static" / name).stat().st_mtime_ns
        for name in ("style.css", "app.js")
    )
)

app = FastAPI(title="politics-viz", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")
templates.env.globals["asset_version"] = ASSET_VERSION


def get_conn() -> sqlite3.Connection:
    conn = db.connect(DB_PATH)
    try:
        yield conn
    finally:
        conn.close()


WINDOWS = {"12m", "24m", "all"}
SCOPES = {"an", "senat", "parl", "all"}


def _validate_window(window: str) -> str:
    if window not in WINDOWS:
        raise HTTPException(status_code=400, detail=f"window must be one of {sorted(WINDOWS)}")
    return window


def _validate_scope(scope: str) -> str:
    if scope not in SCOPES:
        raise HTTPException(status_code=400, detail=f"scope must be one of {sorted(SCOPES)}")
    return scope


# -- pages --------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
def index(
    request: Request,
    window: str = Query("all"),
    scope: str = Query("an"),
    conn: sqlite3.Connection = Depends(get_conn),
):
    window = _validate_window(window)
    scope = _validate_scope(scope)
    stats = _scope_stats(conn, scope)
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {"stats": stats, "window": window, "scope": scope},
    )


def _scope_stats(conn: sqlite3.Connection, scope: str) -> dict:
    clause = queries.scope_clause(scope)
    row = conn.execute(
        f"""SELECT COUNT(DISTINCT d.declarant_id) AS declarants,
                   COUNT(DISTINCT d.id)           AS declarations,
                   COUNT(h.id)                    AS holdings
            FROM declaration d
            LEFT JOIN holding h ON h.declaration_id = d.id
            WHERE {clause}"""
    ).fetchone()
    return dict(row)


@app.get("/declarant/{declarant_id}", response_class=HTMLResponse)
def declarant_page(
    request: Request,
    declarant_id: int,
    conn: sqlite3.Connection = Depends(get_conn),
):
    decl = queries.get_declarant(conn, declarant_id)
    if not decl:
        raise HTTPException(status_code=404, detail="declarant not found")
    return templates.TemplateResponse(
        request,
        "declarant.html",
        {
            "decl": decl,
            "declarations": queries.declarant_declarations(conn, declarant_id),
            "changes": queries.declarant_changes(conn, declarant_id),
            "conflicts": queries.declarant_conflicts(conn, declarant_id),
            "vote_count": queries.declarant_vote_count(conn, declarant_id),
        },
    )


@app.get("/company/{slug}", response_class=HTMLResponse)
def company_page(
    request: Request,
    slug: str,
    conn: sqlite3.Connection = Depends(get_conn),
):
    company = queries.get_company(conn, slug)
    if not company:
        raise HTTPException(status_code=404, detail="company not found")
    return templates.TemplateResponse(request, "company.html", {"company": company})


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


@app.get("/api/company/{slug}")
def api_company(
    slug: str, conn: sqlite3.Connection = Depends(get_conn)
) -> JSONResponse:
    company = queries.get_company(conn, slug)
    if not company:
        raise HTTPException(status_code=404, detail="company not found")
    return JSONResponse(
        {
            "company": company,
            "by_party": queries.company_by_party(conn, slug),
            "holders": queries.company_holders(conn, slug),
            "timeline": queries.company_timeline(conn, slug),
        }
    )


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


@app.get("/api/hemicycle")
def api_hemicycle(
    scope: str = Query("an"), conn: sqlite3.Connection = Depends(get_conn)
) -> JSONResponse:
    scope = _validate_scope(scope)
    return JSONResponse(queries.hemicycle(conn, scope=scope))


@app.get("/api/dashboard")
def api_dashboard(
    window: str = Query("all"),
    scope: str = Query("an"),
    conn: sqlite3.Connection = Depends(get_conn),
) -> JSONResponse:
    window = _validate_window(window)
    scope = _validate_scope(scope)
    return JSONResponse(
        {
            "window": window,
            "scope": scope,
            "top_companies": queries.top_companies(conn, window, scope=scope),
            "top_by_party": queries.top_companies_by_party(conn, window, scope=scope),
            "surges_new": queries.surges(conn, window, "new", scope=scope),
            "surges_grown": queries.surges(conn, window, "grown", scope=scope),
            "top_conflicted": queries.top_conflicted(conn, scope=scope),
        }
    )


# -- health -------------------------------------------------------------


@app.get("/healthz")
def healthz() -> JSONResponse:
    return JSONResponse({"ok": True})
