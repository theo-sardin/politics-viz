"""What a site needs before strangers can reach it: caching, a request
budget per client, and headers that tell the browser to load nothing from
anywhere else.

The database only changes when ``pv sync`` swaps a new one in, so every page
is a pure function of (URL, database, code). That makes validation cheap: the
ETag is derived from those three, and a repeat visit costs a ``stat`` and a
304 instead of a render.
"""

from __future__ import annotations

import hashlib
import os
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path

from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response

# Pages change once a day at most. Browsers reuse them for ten minutes, then
# revalidate; a CDN in front may serve a stale copy while it does.
PAGE_CACHE = "public, max-age=600, stale-while-revalidate=86400"
VERSIONED_ASSET_CACHE = "public, max-age=31536000, immutable"
ASSET_CACHE = "public, max-age=3600"

# Everything comes from this origin. Inline *styles* stay allowed: group
# colours and bar widths are set per element from data.
CSP = (
    "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; "
    "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
)
# The same policy for hosts where we cannot set headers (a static export):
# shipped as a <meta> tag, which does not support frame-ancestors.
META_CSP = CSP.replace("; frame-ancestors 'none'", "")

SECURITY_HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "geolocation=(), camera=(), microphone=()",
}

_UNMETERED_PREFIXES = ("/static/", "/photos/")
_UNMETERED_PATHS = {"/healthz"}


class RateLimiter:
    """At most *limit* requests per *window* seconds for each client key.

    Kept in memory, per process: enough to stop one address from hammering a
    small instance. It is not a defence against a distributed flood — that is
    the job of whatever sits in front (a CDN, the host's own protection).
    """

    def __init__(self, limit: int, window: float = 60.0, max_clients: int = 20_000):
        self.limit = limit
        self.window = window
        self.max_clients = max_clients
        self.hits: dict[str, deque[float]] = {}

    def check(self, key: str, now: float | None = None) -> float:
        """0 when the request may proceed, else the seconds to wait."""
        if self.limit <= 0:
            return 0.0
        now = time.monotonic() if now is None else now
        q = self.hits.get(key)
        if q is None:
            if len(self.hits) >= self.max_clients:
                self._prune(now)
            q = self.hits[key] = deque()
        while q and now - q[0] >= self.window:
            q.popleft()
        if len(q) >= self.limit:
            return max(0.1, self.window - (now - q[0]))
        q.append(now)
        return 0.0

    def _prune(self, now: float) -> None:
        stale = [k for k, q in self.hits.items() if not q or now - q[-1] >= self.window]
        for k in stale:
            del self.hits[k]
        if len(self.hits) >= self.max_clients:  # all active: drop the oldest half
            for k in sorted(self.hits, key=lambda k: self.hits[k][-1])[: self.max_clients // 2]:
                del self.hits[k]


def default_rate_limit() -> int:
    """Requests per minute per client; ``PV_RATE_LIMIT=0`` turns the limit off."""
    try:
        return int(os.environ.get("PV_RATE_LIMIT", "240"))
    except ValueError:
        return 240


def page_etag(db_path: Path, asset_version: str, url: str) -> str | None:
    try:
        stamp = db_path.stat().st_mtime_ns
    except OSError:
        return None
    digest = hashlib.sha1(f"{stamp}|{asset_version}|{url}".encode()).hexdigest()[:20]
    return f'W/"{digest}"'


def install(app, *, db_path: Callable[[], Path], asset_version: Callable[[], str]) -> RateLimiter:
    limiter = RateLimiter(default_rate_limit())

    @app.middleware("http")
    async def harden(request: Request, call_next):
        path = request.url.path
        metered = not (path.startswith(_UNMETERED_PREFIXES) or path in _UNMETERED_PATHS)

        if metered:
            client = request.client.host if request.client else "unknown"
            wait = limiter.check(client)
            if wait:
                return _finish(
                    PlainTextResponse(
                        "Trop de requêtes : réessayez dans un instant.\n",
                        status_code=429,
                        headers={"Retry-After": str(int(wait) + 1), "Cache-Control": "no-store"},
                    )
                )

        etag = None
        if metered and request.method in ("GET", "HEAD"):
            etag = page_etag(db_path(), asset_version(), str(request.url.path) + "?" + request.url.query)
            if etag and request.headers.get("if-none-match") == etag:
                return _finish(Response(status_code=304, headers={"ETag": etag, "Cache-Control": PAGE_CACHE}))

        response = await call_next(request)

        if path.startswith(("/static/", "/photos/")):
            versioned = "v=" in request.url.query
            response.headers["Cache-Control"] = VERSIONED_ASSET_CACHE if versioned else ASSET_CACHE
        elif response.status_code == 200 and etag:
            response.headers["ETag"] = etag
            response.headers["Cache-Control"] = PAGE_CACHE
        elif "cache-control" not in response.headers:
            response.headers["Cache-Control"] = "no-store"
        return _finish(response)

    return limiter


def _finish(response: Response) -> Response:
    for name, value in SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    return response
