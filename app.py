"""FastAPI application serving the politics-viz site.

Skeleton + /healthz + base template mount. Pages are added in the
dashboard / party / company / declarant tasks.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import db

BASE_DIR = Path(__file__).parent
DB_PATH = os.environ.get("POLITICS_DB", str(BASE_DIR / "data.sqlite"))

app = FastAPI(title="politics-viz", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


def get_conn() -> sqlite3.Connection:
    """Read-only-ish SQLite connection per request."""
    conn = db.connect(DB_PATH)
    try:
        yield conn
    finally:
        conn.close()


@app.get("/healthz")
def healthz() -> JSONResponse:
    return JSONResponse({"ok": True})


@app.get("/", response_class=HTMLResponse)
def index(request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    # Placeholder until the dashboard task wires real data in.
    stats = {
        "declarants": conn.execute("SELECT COUNT(*) FROM declarant").fetchone()[0],
        "declarations": conn.execute("SELECT COUNT(*) FROM declaration").fetchone()[0],
        "holdings": conn.execute("SELECT COUNT(*) FROM holding").fetchone()[0],
    }
    return templates.TemplateResponse(
        request, "dashboard.html", {"stats": stats, "window": "12m"}
    )
