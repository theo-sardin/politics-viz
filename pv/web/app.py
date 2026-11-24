"""FastAPI application: server-rendered pages over the read-only database."""

from __future__ import annotations

import math
import sqlite3
from pathlib import Path
from urllib.parse import urlencode

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from pv import companies, config, db, labels, sectors
from pv.web import queries

WEB_DIR = Path(__file__).parent
PER_PAGE = 40

app = FastAPI(title="politics-viz", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")
templates = Jinja2Templates(directory=WEB_DIR / "templates")


def _asset_version() -> str:
    files = [p for p in (WEB_DIR / "static").glob("*") if p.is_file()]
    return str(max((p.stat().st_mtime_ns for p in files), default=0))


env = templates.env
env.globals.update(
    asset_version=_asset_version(),
    labels=labels,
    sectors=sectors.SECTORS,
    scopes=queries.SCOPES,
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
    return scope if scope in queries.SCOPES else queries.DEFAULT_SCOPE


def _page(page: int, total: int, per_page: int = PER_PAGE) -> dict:
    pages = max(1, math.ceil(total / per_page))
    page = min(max(1, page), pages)
    return {"page": page, "pages": pages, "total": total, "offset": (page - 1) * per_page, "per_page": per_page}


def _render(request: Request, name: str, conn: sqlite3.Connection, **ctx) -> HTMLResponse:
    def url_with(**changes) -> str:
        params = {k: v for k, v in request.query_params.items()}
        for k, v in changes.items():
            if v is None or v == "":
                params.pop(k, None)
            else:
                params[k] = v
        if "page" not in changes:
            params.pop("page", None)
        qs = urlencode(params)
        return f"{request.url.path}?{qs}" if qs else request.url.path

    ctx.setdefault("scope", _scope(request.query_params.get("scope")))
    ctx["url_with"] = url_with
    # Recomputed per request so an edited stylesheet reaches browsers without a restart.
    ctx["asset_version"] = _asset_version()
    ctx["fresh"] = queries.freshness(conn)
    ctx["path"] = request.url.path
    return templates.TemplateResponse(request, name, ctx)


# -- pages --------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
def home(request: Request, scope: str = Query(None), conn: sqlite3.Connection = Depends(get_conn)):
    scope = _scope(scope)
    return _render(request, "home.html", conn, scope=scope, data=queries.home(conn, scope))


@app.get("/signaux", response_class=HTMLResponse)
def signals_page(
    request: Request,
    scope: str = Query(None),
    niveau: str = Query(None),
    type: str = Query(None),
    groupe: str = Query(None),
    q: str = Query(None),
    tri: str = Query("score"),
    page: int = Query(1, ge=1),
    conn: sqlite3.Connection = Depends(get_conn),
):
    scope = _scope(scope)
    _, total = queries.signals(conn, scope=scope, level=niveau, kind=type, group=groupe, q=q, limit=0)
    pg = _page(page, total)
    rows, _ = queries.signals(conn, scope=scope, level=niveau, kind=type, group=groupe, q=q, sort=tri,
                              limit=pg["per_page"], offset=pg["offset"])
    return _render(request, "signals.html", conn, scope=scope, rows=rows, pg=pg,
                   facets=queries.signal_facets(conn, scope),
                   filters={"niveau": niveau, "type": type, "groupe": groupe, "q": q or "", "tri": tri})


@app.get("/mouvements", response_class=HTMLResponse)
def moves_page(
    request: Request,
    scope: str = Query(None),
    type: str = Query(None),
    q: str = Query(None),
    page: int = Query(1, ge=1),
    conn: sqlite3.Connection = Depends(get_conn),
):
    scope = _scope(scope)
    _, total = queries.moves(conn, scope=scope, kind=type, q=q, limit=0)
    pg = _page(page, total)
    rows, _ = queries.moves(conn, scope=scope, kind=type, q=q, limit=pg["per_page"], offset=pg["offset"])
    return _render(request, "moves.html", conn, scope=scope, rows=rows, pg=pg, filters={"type": type, "q": q or ""})


@app.get("/elus", response_class=HTMLResponse)
def people_page(
    request: Request,
    scope: str = Query(None),
    groupe: str = Query(None),
    q: str = Query(None),
    tri: str = Query("signals"),
    page: int = Query(1, ge=1),
    conn: sqlite3.Connection = Depends(get_conn),
):
    scope = _scope(scope)
    _, total = queries.people_list(conn, scope=scope, q=q, group=groupe, limit=0)
    pg = _page(page, total, 60)
    rows, _ = queries.people_list(conn, scope=scope, q=q, group=groupe, sort=tri, limit=60, offset=pg["offset"])
    groups = [dict(r) for r in conn.execute(
        "SELECT slug, code, label, color, color_dark FROM groupe WHERE chamber = ? ORDER BY ord", (scope,))]
    return _render(request, "people.html", conn, scope=scope, rows=rows, pg=pg, groups=groups,
                   filters={"groupe": groupe, "q": q or "", "tri": tri})


@app.get("/personne/{slug}", response_class=HTMLResponse)
def person_page(request: Request, slug: str, conn: sqlite3.Connection = Depends(get_conn)):
    person = queries.person(conn, slug)
    if person is None:
        raise HTTPException(status_code=404, detail="Personne introuvable")
    return _render(request, "person.html", conn, p=person)


@app.get("/societes", response_class=HTMLResponse)
def companies_page(
    request: Request,
    scope: str = Query(None),
    secteur: str = Query(None),
    q: str = Query(None),
    tri: str = Query("holders"),
    page: int = Query(1, ge=1),
    conn: sqlite3.Connection = Depends(get_conn),
):
    scope = _scope(scope)
    _, total = queries.companies_list(conn, scope=scope, sector=secteur, q=q, limit=0)
    pg = _page(page, total, 60)
    rows, _ = queries.companies_list(conn, scope=scope, sector=secteur, q=q, sort=tri, limit=60, offset=pg["offset"])
    return _render(request, "companies.html", conn, scope=scope, rows=rows, pg=pg,
                   filters={"secteur": secteur, "q": q or "", "tri": tri})


@app.get("/societe/{slug}", response_class=HTMLResponse)
def company_page(request: Request, slug: str, conn: sqlite3.Connection = Depends(get_conn)):
    company = queries.company(conn, slug)
    if company is None:
        raise HTTPException(status_code=404, detail="Société introuvable")
    return _render(request, "company.html", conn, c=company)


@app.get("/textes", response_class=HTMLResponse)
def bills_page(
    request: Request,
    secteur: str = Query(None),
    q: str = Query(None),
    exposes: int = Query(0),
    page: int = Query(1, ge=1),
    conn: sqlite3.Connection = Depends(get_conn),
):
    _, total = queries.bills_list(conn, sector=secteur, q=q, exposed=bool(exposes), limit=0)
    pg = _page(page, total)
    rows, _ = queries.bills_list(conn, sector=secteur, q=q, exposed=bool(exposes), limit=pg["per_page"], offset=pg["offset"])
    return _render(request, "bills.html", conn, rows=rows, pg=pg,
                   filters={"secteur": secteur, "q": q or "", "exposes": exposes})


@app.get("/texte/{slug}", response_class=HTMLResponse)
def bill_page(request: Request, slug: str, conn: sqlite3.Connection = Depends(get_conn)):
    bill = queries.bill(conn, slug)
    if bill is None:
        raise HTTPException(status_code=404, detail="Texte introuvable")
    return _render(request, "bill.html", conn, b=bill)


@app.get("/groupe/{slug}", response_class=HTMLResponse)
def group_page(request: Request, slug: str, conn: sqlite3.Connection = Depends(get_conn)):
    group = queries.group(conn, slug)
    if group is None:
        raise HTTPException(status_code=404, detail="Groupe introuvable")
    return _render(request, "group.html", conn, g=group, scope=group["chamber"])


@app.get("/reseau", response_class=HTMLResponse)
def network_page(
    request: Request,
    scope: str = Query(None),
    secteur: str = Query(None),
    min: int = Query(3, ge=2, le=20),
    conn: sqlite3.Connection = Depends(get_conn),
):
    return _render(request, "network.html", conn, scope=_scope(scope), filters={"secteur": secteur, "min": min})


@app.get("/methode", response_class=HTMLResponse)
def method_page(request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in ("person", "declaration", "holding", "interest", "lobby_org", "lobby_action", "bill",
                        "scrutin", "vote", "position_change", "signal")}
    counts["archived"] = conn.execute("SELECT COUNT(*) FROM declaration WHERE status = 'archived'").fetchone()[0]
    counts["registry"] = len(companies.REGISTRY)
    return _render(request, "method.html", conn, counts=counts)


# -- JSON ---------------------------------------------------------------


@app.get("/api/search")
def api_search(q: str = Query(""), conn: sqlite3.Connection = Depends(get_conn)) -> JSONResponse:
    return JSONResponse(queries.search(conn, q))


@app.get("/api/reseau")
def api_network(
    scope: str = Query(None),
    secteur: str = Query(None),
    min: int = Query(3, ge=2, le=20),
    conn: sqlite3.Connection = Depends(get_conn),
) -> JSONResponse:
    return JSONResponse(queries.network(conn, _scope(scope), sector=secteur or None, min_holders=min))


@app.get("/healthz")
def healthz() -> JSONResponse:
    return JSONResponse({"ok": True, "db": Path(config.DB_PATH).exists()})


# -- links from the previous version of the site --------------------------


@app.get("/company/{slug}")
def legacy_company(slug: str) -> RedirectResponse:
    return RedirectResponse(f"/societe/{slug}", status_code=308)


@app.exception_handler(404)
async def not_found(request: Request, exc) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "error.html", {"code": 404, "message": getattr(exc, "detail", "Page introuvable"), "scope": "an",
                                "path": request.url.path, "fresh": None},
        status_code=404,
    )


@app.exception_handler(503)
async def unavailable(request: Request, exc) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "error.html", {"code": 503, "message": getattr(exc, "detail", "Service indisponible"), "scope": "an",
                                "path": request.url.path, "fresh": None},
        status_code=503,
    )
