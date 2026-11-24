"""FastAPI application: server-rendered pages over the read-only database.

Every page is a pure function of its path. Nothing depends on a query string
on the server side: list filters, sorting and search run in the browser. That
is what lets ``pv export`` turn the whole site into static files.
"""

from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from pv import companies, config, db, labels, sectors
from pv.pipeline.photos import photos_dir
from pv.web import hardening, queries

WEB_DIR = Path(__file__).parent
REPO_URL = os.environ.get("PV_REPO_URL", "https://github.com/theo-sardin/politics-viz")

app = FastAPI(title="politics-viz", docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")
templates = Jinja2Templates(directory=WEB_DIR / "templates")


def _asset_version() -> str:
    files = [p for p in (WEB_DIR / "static").glob("*") if p.is_file()]
    return str(max((p.stat().st_mtime_ns for p in files), default=0))


def page_url(page: str = "", scope: str = queries.DEFAULT_SCOPE) -> str:
    """'/signaux/' for the default scope, '/signaux/senat/' otherwise; page '' is the home page."""
    base = f"/{page}/" if page else "/"
    return base if scope == queries.DEFAULT_SCOPE else f"{base}{scope}/"


limiter = hardening.install(app, db_path=lambda: Path(config.DB_PATH), asset_version=_asset_version)

env = templates.env
env.globals.update(
    labels=labels,
    sectors=sectors.SECTORS,
    scopes=queries.SCOPES,
    page_url=page_url,
    csp=hardening.META_CSP,
    repo_url=REPO_URL,
    hatvp_base=config.HATVP_BASE,
    hatvp_files=config.HATVP_FILE_BASE,
    an_base=config.AN_BASE,
)
env.filters.update(
    eur=labels.fmt_eur,
    intfr=labels.fmt_int,
    date=labels.fmt_date,
    date_short=lambda s: labels.fmt_date(s, short=True),
    short_bill=labels.short_bill,
    sector=sectors.label,
    pct=lambda v: "—" if v is None else (f"{v:.1f}".rstrip("0").rstrip(".").replace(".", ",") + " %"),
)


def get_conn():
    path = Path(config.DB_PATH)
    if not path.exists():
        raise HTTPException(status_code=503, detail="Base absente : lancer « pv sync ».")
    conn = db.connect(path, readonly=True)
    try:
        yield conn
    finally:
        conn.close()


def _scope(scope: str | None) -> str:
    """The default scope has no path segment; anything else must be a known one."""
    if scope is None:
        return queries.DEFAULT_SCOPE
    if scope not in queries.SCOPES or scope == queries.DEFAULT_SCOPE:
        raise HTTPException(status_code=404, detail="Page introuvable")
    return scope


def _render(request: Request, name: str, conn: sqlite3.Connection | None, *, status: int = 200, **ctx) -> HTMLResponse:
    ctx.setdefault("scope", queries.DEFAULT_SCOPE)
    ctx.setdefault("page", None)
    ctx["fresh"] = queries.freshness(conn) if conn is not None else None
    # Recomputed per request so an edited stylesheet reaches browsers without a restart.
    ctx["asset_version"] = _asset_version()
    # Search engines stay out unless PV_INDEXABLE=1: the site names people.
    ctx["noindex"] = os.environ.get("PV_INDEXABLE", "") != "1"
    return templates.TemplateResponse(request, name, ctx, status_code=status)


# -- lists (one page per scope) ---------------------------------------------


@app.get("/signaux/", response_class=HTMLResponse)
@app.get("/signaux/{scope}/", response_class=HTMLResponse)
def signals_page(request: Request, scope: str | None = None, conn: sqlite3.Connection = Depends(get_conn)):
    scope = _scope(scope)
    rows, _ = queries.signals(conn, scope=scope)
    return _render(
        request,
        "signals.html",
        conn,
        scope=scope,
        page="signaux",
        rows=rows,
        groups=queries.groups(conn, scope),
        facets=queries.signal_facets(conn, scope),
    )


@app.get("/mouvements/", response_class=HTMLResponse)
@app.get("/mouvements/{scope}/", response_class=HTMLResponse)
def moves_page(request: Request, scope: str | None = None, conn: sqlite3.Connection = Depends(get_conn)):
    scope = _scope(scope)
    rows, _ = queries.moves(conn, scope=scope)
    return _render(request, "moves.html", conn, scope=scope, page="mouvements", rows=rows)


@app.get("/elus/", response_class=HTMLResponse)
@app.get("/elus/{scope}/", response_class=HTMLResponse)
def people_page(request: Request, scope: str | None = None, conn: sqlite3.Connection = Depends(get_conn)):
    scope = _scope(scope)
    return _render(
        request,
        "people.html",
        conn,
        scope=scope,
        page="elus",
        rows=queries.people_list(conn, scope),
        groups=queries.groups(conn, scope),
    )


@app.get("/societes/", response_class=HTMLResponse)
@app.get("/societes/{scope}/", response_class=HTMLResponse)
def companies_page(request: Request, scope: str | None = None, conn: sqlite3.Connection = Depends(get_conn)):
    scope = _scope(scope)
    return _render(
        request, "companies.html", conn, scope=scope, page="societes", rows=queries.companies_list(conn, scope)
    )


@app.get("/reseau/", response_class=HTMLResponse)
@app.get("/reseau/{scope}/", response_class=HTMLResponse)
def network_page(request: Request, scope: str | None = None, conn: sqlite3.Connection = Depends(get_conn)):
    return _render(request, "network.html", conn, scope=_scope(scope), page="reseau")


@app.get("/textes/", response_class=HTMLResponse)
def bills_page(request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    return _render(request, "bills.html", conn, page="textes", rows=queries.bills_list(conn))


@app.get("/methode/", response_class=HTMLResponse)
def method_page(request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    counts = {
        t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        for t in (
            "person",
            "declaration",
            "holding",
            "interest",
            "lobby_org",
            "lobby_action",
            "bill",
            "scrutin",
            "vote",
            "position_change",
            "signal",
        )
    }
    counts["registry"] = len(companies.REGISTRY)
    return _render(request, "method.html", conn, page="methode", counts=counts)


# -- detail pages ---------------------------------------------------------


@app.get("/personne/{slug}/", response_class=HTMLResponse)
def person_page(request: Request, slug: str, conn: sqlite3.Connection = Depends(get_conn)):
    person = queries.person(conn, slug)
    if person is None:
        raise HTTPException(status_code=404, detail="Personne introuvable")
    scope = person["chamber"] if person["active"] and person["chamber"] in queries.SCOPES else "tous"
    return _render(request, "person.html", conn, p=person, scope=scope, page="elus")


@app.get("/societe/{slug}/", response_class=HTMLResponse)
def company_page(request: Request, slug: str, conn: sqlite3.Connection = Depends(get_conn)):
    company = queries.company(conn, slug)
    if company is None:
        raise HTTPException(status_code=404, detail="Société introuvable")
    return _render(request, "company.html", conn, c=company, page="societes")


@app.get("/texte/{slug}/", response_class=HTMLResponse)
def bill_page(request: Request, slug: str, conn: sqlite3.Connection = Depends(get_conn)):
    bill = queries.bill(conn, slug)
    if bill is None:
        raise HTTPException(status_code=404, detail="Texte introuvable")
    return _render(request, "bill.html", conn, b=bill, page="textes")


@app.get("/groupe/{slug}/", response_class=HTMLResponse)
def group_page(request: Request, slug: str, conn: sqlite3.Connection = Depends(get_conn)):
    group = queries.group(conn, slug)
    if group is None:
        raise HTTPException(status_code=404, detail="Groupe introuvable")
    return _render(request, "group.html", conn, g=group, scope=group["chamber"], page="elus")


# -- data the browser filters itself ---------------------------------------


@app.get("/data/search.json")
def data_search(conn: sqlite3.Connection = Depends(get_conn)) -> JSONResponse:
    return JSONResponse(queries.search_index(conn))


@app.get("/data/reseau-{scope}.json")
def data_network(scope: str, conn: sqlite3.Connection = Depends(get_conn)) -> JSONResponse:
    if scope not in queries.SCOPES:
        raise HTTPException(status_code=404, detail="Périmètre inconnu")
    return JSONResponse(queries.network(conn, scope))


@app.get("/photos/{name}")
def photo(name: str) -> FileResponse:
    """Portraits mirrored by ``pv sync``."""
    if not re.fullmatch(r"[a-z0-9-]+\.jpg", name):
        raise HTTPException(status_code=404, detail="Portrait introuvable")
    path = photos_dir() / name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Portrait introuvable")
    return FileResponse(path, media_type="image/jpeg")


@app.get("/healthz")
def healthz() -> JSONResponse:
    return JSONResponse({"ok": True, "db": Path(config.DB_PATH).exists()})


# -- home, last: '/{scope}/' would otherwise shadow every route above ---------


@app.get("/", response_class=HTMLResponse)
@app.get("/{scope}/", response_class=HTMLResponse)
def home(request: Request, scope: str | None = None, conn: sqlite3.Connection = Depends(get_conn)):
    scope = _scope(scope)
    return _render(request, "home.html", conn, scope=scope, page="", data=queries.home(conn, scope))


@app.exception_handler(404)
async def not_found(request: Request, exc) -> HTMLResponse:
    return _render(
        request, "error.html", None, status=404, code=404, message=getattr(exc, "detail", None) or "Page introuvable"
    )


@app.exception_handler(503)
async def unavailable(request: Request, exc) -> HTMLResponse:
    return _render(
        request,
        "error.html",
        None,
        status=503,
        code=503,
        message=getattr(exc, "detail", None) or "Service indisponible",
    )
