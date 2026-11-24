"""Read-side queries. Every page's data comes from one function here, so the
routes stay thin and the SQL stays testable without a web server."""

from __future__ import annotations

import json
import math
import sqlite3
from collections import defaultdict

from pv import config, labels, sectors
from pv.pipeline.signals import LEVEL_FAIBLE, LEVEL_FORT, LEVEL_MOYEN
from pv.text import fold

SCOPES: dict[str, tuple[str, str]] = {
    "an": ("Assemblée nationale", "p.chamber = 'an' AND p.active = 1"),
    "senat": ("Sénat", "p.chamber = 'senat' AND p.active = 1"),
    "gouv": ("Gouvernement", "p.chamber = 'gouv' AND p.active = 1"),
    "tous": ("Tous les déclarants", "1 = 1"),
}
DEFAULT_SCOPE = "an"

VOTE_KINDS = (
    "mouvement_vote",
    "rapporteur",
    "vote_participation",
    "vote_activite",
    "vote_famille",
    "vote_collaborateur",
)

_PERSON_COLS = """p.id, p.slug AS pslug, p.search_key AS pkey, p.prenom, p.nom, p.civilite, p.chamber, p.active, p.role, p.photo_url, p.committee,
                  p.portfolio_eur, p.n_holdings, p.n_signals, p.top_score,
                  g.slug AS group_slug, g.code AS group_code, g.label AS group_label,
                  g.color AS group_color, g.color_dark AS group_color_dark"""


def scope_sql(scope: str) -> str:
    return SCOPES.get(scope, SCOPES[DEFAULT_SCOPE])[1]


def level_of(score: int | None) -> str | None:
    if not score:
        return None
    if score >= LEVEL_FORT:
        return "fort"
    if score >= LEVEL_MOYEN:
        return "moyen"
    if score >= LEVEL_FAIBLE:
        return "faible"
    return None


def _rows(conn: sqlite3.Connection, sql: str, params=()) -> list[dict]:
    return [dict(r) for r in conn.execute(sql, params)]


def _one(conn: sqlite3.Connection, sql: str, params=()) -> dict | None:
    r = conn.execute(sql, params).fetchone()
    return dict(r) if r else None


# ---------------------------------------------------------------------------
# Freshness
# ---------------------------------------------------------------------------


def freshness(conn: sqlite3.Connection) -> dict:
    meta = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM meta")}
    return {
        "synced_at": meta.get("synced_at"),
        "last_declaration": conn.execute("SELECT MAX(filed_at) FROM declaration").fetchone()[0],
        "last_vote": conn.execute("SELECT MAX(date) FROM scrutin").fetchone()[0],
        "sources": [
            {"name": name, "label": src.label, "url": src.url, "date": meta.get(f"source:{name}")}
            for name, src in config.SOURCES.items()
        ],
    }


# ---------------------------------------------------------------------------
# Signals
# ---------------------------------------------------------------------------

_SIGNAL_SQL = f"""
    SELECT s.id, s.kind, s.score, s.level, s.date, s.title, s.summary, s.evidence,
           s.person_id, s.company_id, s.bill_id,
           {_PERSON_COLS},
           c.slug AS company_slug, c.name AS company_name, c.search_key AS company_key,
           b.title AS bill_title, b.legislature AS bill_legislature, b.slug AS bill_slug
    FROM signal s
    LEFT JOIN person p ON p.id = s.person_id
    LEFT JOIN groupe g ON g.id = p.group_id
    LEFT JOIN company c ON c.id = s.company_id
    LEFT JOIN bill b ON b.id = s.bill_id
"""


def _hydrate(rows: list[dict]) -> list[dict]:
    for r in rows:
        r["evidence"] = json.loads(r["evidence"]) if r.get("evidence") else {}
        r["kind_label"] = labels.SIGNAL_KINDS.get(r["kind"], r["kind"])
        r["kind_short"] = labels.SIGNAL_SHORT.get(r["kind"], r["kind"])
        if r.get("bill_title"):
            r["bill_short"] = labels.short_bill(r["bill_title"])
        # What the list page filters on, in the browser.
        r["text"] = " ".join(x for x in (r.get("pkey"), fold(r["title"]), r.get("company_key")) if x)
        family = " votes" if r["kind"] in VOTE_KINDS else " liens" if r["kind"].startswith("lien_") else ""
        r["type_tokens"] = r["kind"] + family
    return rows


def signals(
    conn: sqlite3.Connection,
    *,
    scope: str = "tous",
    group: str | None = None,
    person_id: int | None = None,
    company_id: int | None = None,
    bill_id: int | None = None,
    limit: int | None = None,
) -> tuple[list[dict], int]:
    """Signals, strongest first, with the total before *limit*."""
    where, params = [scope_sql(scope)], []
    if group:
        where.append("g.slug = ?")
        params.append(group)
    if person_id is not None:
        where.append("s.id IN (SELECT signal_id FROM signal_person WHERE person_id = ?)")
        params.append(person_id)
    if company_id is not None:
        where.append("s.company_id = ?")
        params.append(company_id)
    if bill_id is not None:
        where.append("s.bill_id = ?")
        params.append(bill_id)
    clause = " AND ".join(where)
    total = conn.execute(
        f"""SELECT COUNT(*) FROM signal s LEFT JOIN person p ON p.id = s.person_id
            LEFT JOIN groupe g ON g.id = p.group_id WHERE {clause}""",
        params,
    ).fetchone()[0]
    sql = f"{_SIGNAL_SQL} WHERE {clause} ORDER BY s.score DESC, s.date DESC, s.id"
    if limit is not None:
        sql += " LIMIT ?"
        params = [*params, limit]
    return _hydrate(_rows(conn, sql, params)), total


def groups(conn: sqlite3.Connection, scope: str) -> list[dict]:
    """Political groups of a chamber, left to right; none outside the two chambers."""
    return _rows(
        conn, "SELECT slug, code, label, color, color_dark FROM groupe WHERE chamber = ? ORDER BY ord", (scope,)
    )


def signal_facets(conn: sqlite3.Connection, scope: str) -> dict:
    clause = scope_sql(scope)
    by_level = {
        r["level"]: r["n"]
        for r in conn.execute(
            f"SELECT s.level, COUNT(*) n FROM signal s JOIN person p ON p.id = s.person_id WHERE {clause} GROUP BY 1"
        )
    }
    by_kind = {
        r["kind"]: r["n"]
        for r in conn.execute(
            f"SELECT s.kind, COUNT(*) n FROM signal s JOIN person p ON p.id = s.person_id WHERE {clause} GROUP BY 1"
        )
    }
    return {"level": by_level, "kind": by_kind}


def top_signals(conn: sqlite3.Connection, scope: str, n: int = 8, per_person: int = 2) -> list[dict]:
    """The strongest signals, capped per person so one profile cannot fill the list."""
    rows, _ = signals(conn, scope=scope, limit=n * 6)
    out, seen = [], defaultdict(int)
    for r in rows:
        if r["level"] == "info" or seen[r["person_id"]] >= per_person:
            continue
        seen[r["person_id"]] += 1
        out.append(r)
        if len(out) == n:
            break
    return out


# ---------------------------------------------------------------------------
# Home
# ---------------------------------------------------------------------------


def home(conn: sqlite3.Connection, scope: str) -> dict:
    clause = scope_sql(scope)
    stats = _one(
        conn,
        f"""SELECT COUNT(*) AS people,
                   SUM(p.n_holdings > 0) AS with_holdings,
                   SUM(p.current_decl_id IS NOT NULL) AS with_declaration,
                   COALESCE(SUM(p.portfolio_eur), 0) AS total_eur,
                   SUM(p.n_signals > 0) AS with_signals
            FROM person p WHERE {clause}""",
    )
    facets = signal_facets(conn, scope)
    stats["signals"] = facets["level"]
    stats["moves"] = conn.execute(
        f"""SELECT COUNT(*) FROM position_change pc JOIN person p ON p.id = pc.person_id
            WHERE {clause} AND pc.window_end >= date((SELECT MAX(filed_at) FROM declaration), '-12 months')"""
    ).fetchone()[0]
    return {
        "stats": stats,
        "facets": facets,
        "top": top_signals(conn, scope, 5),
        "moves": moves(conn, scope=scope, notable=True, limit=9)[0],
        "companies": top_companies(conn, scope, 12),
        "heatmap": sector_heatmap(conn, scope),
        "bills": exposed_bills(conn, scope, 8),
        "deports": _hydrate(
            _rows(conn, f"{_SIGNAL_SQL} WHERE s.kind = 'deport' AND {clause} ORDER BY s.date DESC LIMIT 5")
        ),
        "hemicycle": hemicycle(conn, scope),
    }


def top_companies(conn: sqlite3.Connection, scope: str, n: int = 12) -> list[dict]:
    return _rows(
        conn,
        f"""SELECT c.id, c.slug, c.name, c.kind, COUNT(DISTINCT p.id) AS n, COALESCE(SUM(h.value_eur), 0) AS total,
                   EXISTS (SELECT 1 FROM lobby_org o WHERE o.company_id = c.id) AS lobby
            FROM holding h
            JOIN person p ON p.current_decl_id = h.declaration_id
            JOIN company c ON c.id = h.company_id
            WHERE h.kind = 'participation' AND c.kind <> 'fund' AND {scope_sql(scope)}
            GROUP BY c.id ORDER BY n DESC, total DESC LIMIT ?""",
        (n,),
    )


def exposed_bills(conn: sqlite3.Connection, scope: str, n: int = 8) -> list[dict]:
    rows = _rows(
        conn,
        f"""SELECT b.id, b.slug, b.title, b.legislature, b.n_scrutins, b.last_vote,
                   COUNT(DISTINCT s.person_id) AS n_people, MAX(s.score) AS top_score
            FROM signal s JOIN bill b ON b.id = s.bill_id JOIN person p ON p.id = s.person_id
            WHERE s.kind IN ({",".join("?" * len(VOTE_KINDS))}) AND b.budget = 0 AND {scope_sql(scope)}
            GROUP BY b.id ORDER BY n_people DESC, top_score DESC LIMIT ?""",
        (*VOTE_KINDS, n),
    )
    for r in rows:
        r["short"] = labels.short_bill(r["title"], 90)
        r["level"] = level_of(r["top_score"])
    return rows


def sector_heatmap(conn: sqlite3.Connection, scope: str) -> dict | None:
    """Share of each group's members holding a stake in each sector."""
    if scope not in ("an", "senat"):
        return None
    clause = scope_sql(scope)
    groups = _rows(
        conn,
        f"""SELECT g.id, g.slug, g.code, g.label, g.color, g.color_dark, COUNT(p.id) AS size
            FROM groupe g JOIN person p ON p.group_id = g.id WHERE {clause} GROUP BY g.id ORDER BY g.ord""",
    )
    if not groups:
        return None
    holders: dict[tuple[int, str], set[int]] = defaultdict(set)
    value: dict[tuple[int, str], int] = defaultdict(int)
    for r in conn.execute(
        f"""SELECT p.group_id gid, p.id pid, h.value_eur v, h.sectors,
                   (SELECT GROUP_CONCAT(cs.sector) FROM company_sector cs WHERE cs.company_id = h.company_id) AS co_sectors,
                   (SELECT c.kind FROM company c WHERE c.id = h.company_id) AS kind
            FROM holding h JOIN person p ON p.current_decl_id = h.declaration_id
            WHERE h.kind = 'participation' AND p.group_id IS NOT NULL AND {clause}"""
    ):
        if r["kind"] == "fund":
            continue
        for s in set((r["co_sectors"] or r["sectors"] or "").split(",")) - {""}:
            holders[(r["gid"], s)].add(r["pid"])
            value[(r["gid"], s)] += r["v"] or 0
    totals = defaultdict(int)
    for (_, s), pids in holders.items():
        totals[s] += len(pids)
    cols = [s for s in sectors.SECTORS if totals[s] > 0]
    cols.sort(key=lambda s: -totals[s])
    cells, peak = {}, 0.0
    for g in groups:
        for s in cols:
            n = len(holders.get((g["id"], s), ()))
            share = n / g["size"] if g["size"] else 0
            peak = max(peak, share)
            cells[(g["id"], s)] = {"n": n, "share": share, "value": value.get((g["id"], s), 0)}
    return {"groups": groups, "sectors": [sectors.SECTORS[s] for s in cols], "cells": cells, "peak": peak or 1}


# ---------------------------------------------------------------------------
# Hemicycle
# ---------------------------------------------------------------------------


def _hemicycle_positions(n: int) -> tuple[list[tuple[float, float]], float]:
    """Seat centres for *n* seats, left to right, plus the seat radius.

    Rows are concentric arcs. The row count is the one that makes seats as
    far apart along an arc as the arcs are from each other.
    """
    r0 = 0.42
    best = None
    for rows in range(1, min(n, 21) + 1):
        radii = [r0 + (1 - r0) * i / (rows - 1) for i in range(rows)] if rows > 1 else [0.8]
        total = sum(radii)
        counts = [max(1, int(n * r / total)) for r in radii]
        # Hand the seats lost to rounding to the outer rows, which have the room.
        i = rows - 1
        while sum(counts) < n:
            counts[i] += 1
            i = i - 1 if i > 0 else rows - 1
        while sum(counts) > n:
            j = max(range(rows), key=lambda k: counts[k])
            counts[j] -= 1
        arc = min(math.pi * r / c for r, c in zip(radii, counts, strict=True))
        # A single row has the whole depth of the band to itself.
        gap = (1 - r0) / (rows - 1) if rows > 1 else 1 - r0
        score = abs(arc - gap)
        if best is None or score < best[0]:
            best = (score, radii, counts, min(arc, gap))
    _, radii, counts, spacing = best
    seats = []
    for r, c in zip(radii, counts, strict=True):
        for j in range(c):
            theta = math.pi * (1 - (j + 0.5) / c)
            seats.append((theta, r))
    seats.sort(key=lambda s: (-s[0], s[1]))
    return [(r * math.cos(t), r * math.sin(t)) for t, r in seats], spacing * 0.44


def hemicycle(conn: sqlite3.Connection, scope: str) -> dict | None:
    if scope not in ("an", "senat"):
        return None
    people = _rows(
        conn,
        f"""SELECT {_PERSON_COLS}, COALESCE(g.ord, 99) AS ord
            FROM person p LEFT JOIN groupe g ON g.id = p.group_id
            WHERE {scope_sql(scope)} ORDER BY ord, p.nom, p.prenom""",
    )
    if not people:
        return None
    pos, radius = _hemicycle_positions(len(people))
    size = 500
    peak = max((p["portfolio_eur"] for p in people), default=0) or 1
    for p, (x, y) in zip(people, pos, strict=True):
        p["x"] = round(size + x * size, 1)
        p["y"] = round(size - y * size + 8, 1)
        p["level"] = level_of(p["top_score"])
        # 0 (nothing declared) … 5, on a log scale: portfolios span five orders of magnitude.
        v = p["portfolio_eur"]
        p["wealth"] = 0 if v <= 0 else min(5, max(1, int(math.log10(max(v, 1000)) - 2)))
    groups = _rows(
        conn,
        f"""SELECT g.slug, g.code, g.label, g.color, g.color_dark, COUNT(p.id) AS size,
                   SUM(p.n_holdings > 0) AS holders, COALESCE(SUM(p.portfolio_eur), 0) AS total,
                   SUM(p.top_score >= {LEVEL_MOYEN}) AS flagged
            FROM groupe g JOIN person p ON p.group_id = g.id
            WHERE {scope_sql(scope)} GROUP BY g.id ORDER BY g.ord""",
    )
    return {
        "seats": people,
        "groups": groups,
        "r": round(radius * size, 1),
        "w": size * 2,
        "h": size + 16,
        "peak": peak,
    }


# ---------------------------------------------------------------------------
# Movements
# ---------------------------------------------------------------------------


def moves(
    conn: sqlite3.Connection,
    *,
    scope: str = "tous",
    person_id: int | None = None,
    company_id: int | None = None,
    notable: bool = False,
    limit: int | None = None,
) -> tuple[list[dict], int]:
    """Inferred buys and sells, latest first, with the total before *limit*."""
    where, params = [scope_sql(scope)], []
    if person_id is not None:
        where.append("pc.person_id = ?")
        params.append(person_id)
    if company_id is not None:
        where.append("pc.company_id = ?")
        params.append(company_id)
    if notable:
        # Home page: named market companies, real amounts, one filing at a time.
        where.append("c.kind IN ('listed', 'unlisted') AND pc.same_mandate = 1")
        where.append("COALESCE(pc.value_after, pc.value_before, 0) >= 3000")
        where.append("(SELECT COUNT(*) FROM position_change x WHERE x.curr_decl_id = pc.curr_decl_id) < 5")
    clause = " AND ".join(where)
    base = f"""FROM position_change pc
               JOIN person p ON p.id = pc.person_id
               LEFT JOIN groupe g ON g.id = p.group_id
               JOIN company c ON c.id = pc.company_id
               WHERE {clause}"""
    total = conn.execute(f"SELECT COUNT(*) {base}", params).fetchone()[0]
    sql = f"""SELECT pc.*, {_PERSON_COLS}, c.slug AS company_slug, c.name AS company_name, c.kind AS company_kind,
                     c.search_key AS company_key,
                     (SELECT COUNT(*) FROM position_change x WHERE x.curr_decl_id = pc.curr_decl_id) AS bulk
              {base} ORDER BY pc.window_end DESC, COALESCE(pc.value_after, pc.value_before, 0) DESC, pc.id"""
    if limit is not None:
        sql += " LIMIT ?"
        params = [*params, limit]
    return _rows(conn, sql, params), total


# ---------------------------------------------------------------------------
# Person
# ---------------------------------------------------------------------------


def person(conn: sqlite3.Connection, slug: str) -> dict | None:
    p = _one(
        conn,
        f"""SELECT {_PERSON_COLS}, p.hatvp_slug, p.an_id, p.senat_id, p.birth_date, p.profession, p.departement,
                   p.current_decl_id
            FROM person p LEFT JOIN groupe g ON g.id = p.group_id WHERE p.slug = ?""",
        (slug,),
    )
    if p is None:
        return None
    pid = p["id"]
    p["level"] = level_of(p["top_score"])
    p["signals"], _ = signals(conn, scope="tous", person_id=pid, limit=200)

    decls = _rows(
        conn,
        """SELECT id, doc_type, is_modification, filed_at, published_at, mandate_kind, mandate_label,
                  source_file, no_holdings, note
           FROM declaration WHERE person_id = ? ORDER BY filed_at, id""",
        (pid,),
    )
    p["declarations"] = decls
    series = [d for d in decls if d["doc_type"] in ("DI", "DIA")]
    current = series[-1] if series else None
    p["current"] = current

    # Holdings line by line across declarations.
    changes = {
        (c["curr_decl_id"], c["company_id"]): c
        for c in _rows(conn, "SELECT * FROM position_change WHERE person_id = ?", (pid,))
    }
    lines: dict[object, dict] = {}
    totals: dict[int, int] = defaultdict(int)
    for d in series:
        for h in _rows(
            conn,
            """SELECT h.company_id, h.label, SUM(h.value_eur) AS value, SUM(h.shares) AS shares,
                      MAX(h.capital_pct) AS pct, COUNT(*) AS n, c.slug, c.name, c.kind, c.ticker
               FROM holding h LEFT JOIN company c ON c.id = h.company_id
               WHERE h.declaration_id = ? AND h.kind = 'participation'
               GROUP BY COALESCE(h.company_id, h.label)""",
            (d["id"],),
        ):
            key = h["company_id"] if h["company_id"] is not None else f"label:{h['label']}"
            line = lines.setdefault(
                key,
                {
                    "name": h["name"] or h["label"],
                    "slug": h["slug"],
                    "kind": h["kind"] or "unnamed",
                    "ticker": h["ticker"],
                    "cells": {},
                    "peak": 0,
                },
            )
            ch = changes.get((d["id"], h["company_id"])) if h["company_id"] is not None else None
            line["cells"][d["id"]] = {
                "value": h["value"],
                "shares": h["shares"],
                "pct": h["pct"],
                "n": h["n"],
                "change": ch["kind"] if ch else None,
                "basis": ch["basis"] if ch else None,
            }
            line["peak"] = max(line["peak"], h["value"] or 0)
            totals[d["id"]] += h["value"] or 0
    for (did, cid), ch in changes.items():
        if ch["kind"] == "sortie" and cid in lines:
            lines[cid]["cells"].setdefault(
                did, {"value": None, "shares": None, "pct": None, "n": 0, "change": "sortie", "basis": ch["basis"]}
            )
    p["series"] = series
    p["lines"] = sorted(lines.values(), key=lambda ln: -ln["peak"])
    p["totals"] = dict(totals)
    p["matrix_peak"] = max((ln["peak"] for ln in lines.values()), default=0)

    p["holdings"] = (
        _rows(
            conn,
            """SELECT h.label, h.raw_name, h.value_eur, h.shares, h.capital_pct, h.income, h.comment, h.sectors,
                  c.slug, c.name, c.kind, c.ticker,
                  (SELECT GROUP_CONCAT(cs.sector) FROM company_sector cs WHERE cs.company_id = c.id) AS co_sectors,
                  (SELECT COUNT(*) FROM lobby_org o WHERE o.company_id = c.id) AS lobby
           FROM holding h LEFT JOIN company c ON c.id = h.company_id
           WHERE h.declaration_id = ? AND h.kind = 'participation'
           ORDER BY COALESCE(h.value_eur, 0) DESC""",
            (current["id"],),
        )
        if current
        else []
    )

    interests: dict[str, list[dict]] = defaultdict(list)
    if current:
        seen = set()
        for it in _rows(
            conn,
            """SELECT i.kind, i.org, i.description, i.person_name, i.start_date, i.end_date, i.kept, i.pay_json,
                      i.pay_max, i.pay_basis, c.slug, c.name AS company_name,
                      (SELECT COUNT(*) FROM lobby_org o WHERE o.company_id = c.id) AS lobby
               FROM interest i LEFT JOIN company c ON c.id = i.company_id
               WHERE i.declaration_id = ? ORDER BY i.kind, COALESCE(i.end_date, '9999') DESC, i.start_date DESC""",
            (current["id"],),
        ):
            key = (it["kind"], it["org"], it["description"], it["person_name"], it["start_date"])
            if key in seen:
                continue
            seen.add(key)
            it["pay"] = json.loads(it["pay_json"]) if it["pay_json"] else []
            interests[it["kind"]].append(it)
    p["interests"] = interests

    dsp = [d for d in decls if d["doc_type"].startswith("DSP")]
    p["wealth"] = None
    if dsp:
        d = dsp[-1]
        assets = _rows(
            conn,
            "SELECT kind, label, value_eur, detail FROM asset WHERE declaration_id = ? ORDER BY kind, value_eur DESC",
            (d["id"],),
        )
        other = _rows(
            conn,
            """SELECT h.kind, h.label, h.value_eur, h.capital_pct, h.comment, c.slug
               FROM holding h LEFT JOIN company c ON c.id = h.company_id
               WHERE h.declaration_id = ? AND h.kind <> 'participation' ORDER BY h.value_eur DESC""",
            (d["id"],),
        )
        groups: dict[str, dict] = {}
        for a in assets:
            g = groups.setdefault(a["kind"], {"items": [], "total": 0})
            g["items"].append(a)
            g["total"] += a["value_eur"] or 0
        for h in other:
            g = groups.setdefault(h["kind"], {"items": [], "total": 0})
            g["items"].append(
                {"label": h["label"], "value_eur": h["value_eur"], "detail": h["comment"], "slug": h["slug"]}
            )
            g["total"] += h["value_eur"] or 0
        p["wealth"] = {"decl": d, "groups": groups, "net": sum(g["total"] for g in groups.values())}

    p["deports"] = _rows(
        conn,
        """SELECT d.*, b.title AS bill_title, b.slug AS bill_slug FROM deport d LEFT JOIN bill b ON b.id = d.bill_id
           WHERE d.person_id = ? ORDER BY d.date DESC""",
        (pid,),
    )
    p["pending"] = _rows(
        conn, "SELECT doc_code, filed, status FROM pending_filing WHERE person_id = ? ORDER BY filed DESC", (pid,)
    )
    p["memberships"] = _rows(
        conn,
        """SELECT o.kind, o.label, MIN(m.role) AS role, MIN(m.start_date) AS start_date
           FROM membership m JOIN organe o ON o.uid = m.organe_uid
           WHERE m.person_id = ? AND m.end_date IS NULL AND o.kind IN ('COMPER', 'COMNL', 'CNPE', 'CNPS', 'MISINFO', 'GE', 'DELEG', 'OFFPAR')
           GROUP BY o.label ORDER BY (o.kind = 'COMPER') DESC, o.kind, o.label""",
        (pid,),
    )
    p["vote_stats"] = _one(
        conn,
        """SELECT COUNT(*) AS n, SUM(v.position = 1) AS pour, SUM(v.position = 2) AS contre,
                  SUM(v.position = 3) AS abst, SUM(v.dissent) AS dissent, MIN(s.date) AS first, MAX(s.date) AS last
           FROM vote v JOIN scrutin s ON s.id = v.scrutin_id WHERE v.person_id = ? AND v.position <> 0""",
        (pid,),
    )
    p["bills"] = _rows(
        conn,
        """SELECT b.id, b.slug, b.title, b.legislature, pb.n_votes, pb.n_pour, pb.n_contre, pb.n_abst, pb.n_dissent,
                  pb.last_date, pb.final_position,
                  (SELECT GROUP_CONCAT(role) FROM bill_role br WHERE br.bill_id = b.id AND br.person_id = pb.person_id) AS roles,
                  (SELECT MAX(s.score) FROM signal s WHERE s.bill_id = b.id AND s.person_id = pb.person_id) AS score
           FROM person_bill pb JOIN bill b ON b.id = pb.bill_id
           WHERE pb.person_id = ? ORDER BY score DESC NULLS LAST, pb.n_votes DESC LIMIT 14""",
        (pid,),
    )
    for b in p["bills"]:
        b["short"] = labels.short_bill(b["title"], 86)
        b["level"] = level_of(b["score"])
    p["moves"], _ = moves(conn, person_id=pid, limit=100)
    return p


# ---------------------------------------------------------------------------
# Company
# ---------------------------------------------------------------------------


def company(conn: sqlite3.Connection, slug: str) -> dict | None:
    c = _one(conn, "SELECT * FROM company WHERE slug = ?", (slug,))
    if c is None:
        return None
    cid = c["id"]
    c["sectors"] = [
        sectors.SECTORS[r["sector"]]
        for r in conn.execute("SELECT sector FROM company_sector WHERE company_id = ?", (cid,))
        if r["sector"] in sectors.SECTORS
    ]
    c["holders"] = _rows(
        conn,
        f"""SELECT {_PERSON_COLS}, SUM(h.value_eur) AS value, SUM(h.shares) AS shares, MAX(h.capital_pct) AS pct,
                   d.filed_at,
                   (SELECT MIN(d2.filed_at) FROM declaration d2 JOIN holding h2 ON h2.declaration_id = d2.id
                    WHERE d2.person_id = p.id AND h2.company_id = h.company_id) AS since
            FROM holding h
            JOIN person p ON p.current_decl_id = h.declaration_id
            JOIN declaration d ON d.id = h.declaration_id
            LEFT JOIN groupe g ON g.id = p.group_id
            WHERE h.company_id = ? AND h.kind = 'participation'
            GROUP BY p.id ORDER BY (p.active = 1 AND p.chamber IN ('an', 'senat', 'gouv')) DESC, value DESC""",
        (cid,),
    )
    c["past_holders"] = conn.execute(
        """SELECT COUNT(DISTINCT d.person_id) FROM holding h JOIN declaration d ON d.id = h.declaration_id
           WHERE h.company_id = ?""",
        (cid,),
    ).fetchone()[0] - len(c["holders"])
    by_group: dict[str, dict] = {}
    for h in c["holders"]:
        key = h["group_code"] or labels.CHAMBERS.get(h["chamber"], "Autres")
        g = by_group.setdefault(
            key,
            {
                "label": key,
                "color": h["group_color"],
                "color_dark": h["group_color_dark"],
                "slug": h["group_slug"],
                "n": 0,
                "value": 0,
            },
        )
        g["n"] += 1
        g["value"] += h["value"] or 0
    c["by_group"] = sorted(by_group.values(), key=lambda g: (-g["n"], -g["value"]))

    c["interests"] = _rows(
        conn,
        f"""SELECT {_PERSON_COLS}, i.kind AS rel, i.org, i.description, i.person_name, i.kept, i.end_date, i.pay_max
            FROM interest i JOIN person p ON p.current_decl_id = i.declaration_id
            LEFT JOIN groupe g ON g.id = p.group_id
            WHERE i.company_id = ? GROUP BY p.id, i.kind ORDER BY (p.chamber IN ('an', 'senat', 'gouv')) DESC, p.nom""",
        (cid,),
    )
    c["lobby_orgs"] = _rows(conn, "SELECT * FROM lobby_org WHERE company_id = ? ORDER BY n_actions DESC", (cid,))
    c["lobby_years"] = _rows(
        conn,
        """SELECT y.year, SUM(y.spend_min) AS spend_min, SUM(y.spend_max) AS spend_max, SUM(y.n_actions) AS n_actions
           FROM lobby_year y JOIN lobby_org o ON o.id = y.org_id
           WHERE o.company_id = ? GROUP BY y.year ORDER BY y.year""",
        (cid,),
    )
    c["lobby_bills"] = _rows(
        conn,
        """SELECT b.id, b.slug, b.title, b.legislature, COUNT(*) AS n,
                  (SELECT a2.subject FROM bill_lobby bl2 JOIN lobby_action a2 ON a2.id = bl2.action_id
                   JOIN lobby_org o2 ON o2.id = a2.org_id
                   WHERE bl2.bill_id = b.id AND o2.company_id = ? ORDER BY a2.year DESC LIMIT 1) AS sample
           FROM bill_lobby bl JOIN lobby_action a ON a.id = bl.action_id JOIN lobby_org o ON o.id = a.org_id
           JOIN bill b ON b.id = bl.bill_id
           WHERE o.company_id = ? AND b.transversal = 0
           GROUP BY b.id ORDER BY b.last_vote DESC LIMIT 12""",
        (cid, cid),
    )
    for b in c["lobby_bills"]:
        b["short"] = labels.short_bill(b["title"], 90)
    c["lobby_actions"] = _rows(
        conn,
        """SELECT a.year, a.subject, a.domains, a.targets_parl, o.name AS org
           FROM lobby_action a JOIN lobby_org o ON o.id = a.org_id
           WHERE o.company_id = ? ORDER BY a.year DESC, a.id DESC LIMIT 10""",
        (cid,),
    )
    c["named_bills"] = _rows(
        conn,
        "SELECT b.id, b.slug, b.title, b.n_scrutins, b.last_vote FROM bill_company bc JOIN bill b ON b.id = bc.bill_id WHERE bc.company_id = ?",
        (cid,),
    )
    c["signals"], c["n_signals"] = signals(conn, scope="tous", company_id=cid, limit=12)
    c["moves"], _ = moves(conn, company_id=cid, limit=30)
    return c


# ---------------------------------------------------------------------------
# Bill
# ---------------------------------------------------------------------------


def bill(conn: sqlite3.Connection, slug: str) -> dict | None:
    b = _one(
        conn,
        "SELECT b.*, o.label AS committee FROM bill b LEFT JOIN organe o ON o.uid = b.committee_uid WHERE b.slug = ?",
        (slug,),
    )
    if b is None:
        return None
    bid = b["id"]
    b["short"] = labels.short_bill(b["title"], 110)
    b["sectors"] = [
        sectors.SECTORS[r["sector"]]
        for r in conn.execute("SELECT sector FROM bill_sector WHERE bill_id = ?", (bid,))
        if r["sector"] in sectors.SECTORS
    ]
    b["roles"] = _rows(
        conn,
        f"""SELECT GROUP_CONCAT(br.role, ' et ') AS bill_role, {_PERSON_COLS}
            FROM bill_role br JOIN person p ON p.id = br.person_id
            LEFT JOIN groupe g ON g.id = p.group_id WHERE br.bill_id = ?
            GROUP BY p.id ORDER BY MAX(br.role) DESC, p.nom""",
        (bid,),
    )
    b["companies"] = _rows(
        conn,
        "SELECT c.slug, c.name FROM bill_company bc JOIN company c ON c.id = bc.company_id WHERE bc.bill_id = ?",
        (bid,),
    )
    b["signals"], b["n_signals"] = signals(conn, scope="tous", bill_id=bid, limit=60)
    b["lobby"] = _rows(
        conn,
        """SELECT o.name AS org, o.category, a.year, a.subject, c.slug AS company_slug, c.name AS company_name,
                  (SELECT COUNT(DISTINCT p.id) FROM holding h JOIN person p ON p.current_decl_id = h.declaration_id
                   WHERE h.company_id = o.company_id AND p.chamber IN ('an', 'senat', 'gouv')) AS holders
           FROM bill_lobby bl JOIN lobby_action a ON a.id = bl.action_id JOIN lobby_org o ON o.id = a.org_id
           LEFT JOIN company c ON c.id = o.company_id
           WHERE bl.bill_id = ? ORDER BY holders DESC, o.name, a.year DESC LIMIT 60""",
        (bid,),
    )
    b["n_lobby"] = conn.execute("SELECT COUNT(*) FROM bill_lobby WHERE bill_id = ?", (bid,)).fetchone()[0]
    b["n_lobby_orgs"] = conn.execute(
        "SELECT COUNT(DISTINCT a.org_id) FROM bill_lobby bl JOIN lobby_action a ON a.id = bl.action_id WHERE bl.bill_id = ?",
        (bid,),
    ).fetchone()[0]
    b["scrutins"] = _rows(
        conn,
        """SELECT numero, legislature, date, title, kind, outcome, n_pour, n_contre, n_abstention, stage
           FROM scrutin WHERE bill_id = ? ORDER BY (kind = 'ensemble') DESC, date DESC, numero DESC LIMIT 25""",
        (bid,),
    )
    b["deports"] = _rows(
        conn,
        f"""SELECT d.date, d.target, d.reason, d.scope, {_PERSON_COLS} FROM deport d JOIN person p ON p.id = d.person_id
            LEFT JOIN groupe g ON g.id = p.group_id WHERE d.bill_id = ? ORDER BY d.date""",
        (bid,),
    )
    return b


def bills_list(conn: sqlite3.Connection) -> list[dict]:
    """Every bill with a roll call, most exposed first."""
    rows = _rows(
        conn,
        """SELECT b.id, b.slug, b.title, b.search_key, b.legislature, b.n_scrutins, b.first_vote, b.last_vote,
                  b.transversal, b.budget,
                  (SELECT GROUP_CONCAT(sector) FROM bill_sector bs WHERE bs.bill_id = b.id) AS sectors,
                  (SELECT COUNT(DISTINCT s.person_id) FROM signal s WHERE s.bill_id = b.id AND s.level <> 'info') AS n_people,
                  (SELECT MAX(s.score) FROM signal s WHERE s.bill_id = b.id AND s.level <> 'info') AS top_score,
                  (SELECT COUNT(DISTINCT a.org_id) FROM bill_lobby bl JOIN lobby_action a ON a.id = bl.action_id
                   WHERE bl.bill_id = b.id) AS n_orgs
           FROM bill b ORDER BY n_people DESC, b.last_vote DESC, b.id""",
    )
    for r in rows:
        r["short"] = labels.short_bill(r["title"], 110)
        r["level"] = level_of(r["top_score"])
        r["sector_list"] = [sectors.SECTORS[s] for s in (r["sectors"] or "").split(",") if s in sectors.SECTORS]
    return rows


# ---------------------------------------------------------------------------
# Lists
# ---------------------------------------------------------------------------


def people_list(conn: sqlite3.Connection, scope: str) -> list[dict]:
    """Everyone in scope, strongest signal first."""
    rows = _rows(
        conn,
        f"""SELECT {_PERSON_COLS} FROM person p LEFT JOIN groupe g ON g.id = p.group_id
            WHERE {scope_sql(scope)}
            ORDER BY p.top_score DESC, p.n_signals DESC, p.portfolio_eur DESC, p.nom, p.prenom""",
    )
    for r in rows:
        r["level"] = level_of(r["top_score"])
    return rows


def companies_list(conn: sqlite3.Connection, scope: str) -> list[dict]:
    """Companies held by someone in scope (latest declarations), most held first."""
    rows = _rows(
        conn,
        f"""SELECT c.id, c.slug, c.name, c.search_key, c.kind, c.ticker, COUNT(DISTINCT p.id) AS n,
                   COALESCE(SUM(h.value_eur), 0) AS total,
                   (SELECT GROUP_CONCAT(sector) FROM company_sector cs WHERE cs.company_id = c.id) AS sectors,
                   (SELECT COUNT(*) FROM lobby_org o WHERE o.company_id = c.id) AS lobby,
                   (SELECT COUNT(*) FROM signal s WHERE s.company_id = c.id AND s.level <> 'info') AS n_signals
            FROM holding h JOIN person p ON p.current_decl_id = h.declaration_id
            JOIN company c ON c.id = h.company_id
            WHERE h.kind = 'participation' AND c.kind <> 'fund' AND {scope_sql(scope)}
            GROUP BY c.id ORDER BY n DESC, total DESC, c.name""",
    )
    for r in rows:
        r["sector_list"] = [sectors.SECTORS[s] for s in (r["sectors"] or "").split(",") if s in sectors.SECTORS]
    return rows


def group(conn: sqlite3.Connection, slug: str) -> dict | None:
    g = _one(conn, "SELECT * FROM groupe WHERE slug = ?", (slug,))
    if g is None:
        return None
    g["members"] = _rows(
        conn,
        f"""SELECT {_PERSON_COLS} FROM person p LEFT JOIN groupe g ON g.id = p.group_id
            WHERE p.group_id = ? AND p.active = 1 ORDER BY p.top_score DESC, p.portfolio_eur DESC, p.nom""",
        (g["id"],),
    )
    for m in g["members"]:
        m["level"] = level_of(m["top_score"])
    g["stats"] = {
        "size": len(g["members"]),
        "holders": sum(m["n_holdings"] > 0 for m in g["members"]),
        "total": sum(m["portfolio_eur"] for m in g["members"]),
        "flagged": sum((m["top_score"] or 0) >= LEVEL_MOYEN for m in g["members"]),
    }
    g["companies"] = _rows(
        conn,
        """SELECT c.slug, c.name, c.kind, COUNT(DISTINCT p.id) AS n, COALESCE(SUM(h.value_eur), 0) AS total
           FROM holding h JOIN person p ON p.current_decl_id = h.declaration_id JOIN company c ON c.id = h.company_id
           WHERE p.group_id = ? AND p.active = 1 AND h.kind = 'participation' AND c.kind <> 'fund'
           GROUP BY c.id ORDER BY n DESC, total DESC LIMIT 12""",
        (g["id"],),
    )
    g["signals"], g["n_signals"] = signals(conn, scope="tous", group=slug, limit=10)
    return g


# ---------------------------------------------------------------------------
# Data the browser searches and filters itself
# ---------------------------------------------------------------------------


def search_index(conn: sqlite3.Connection) -> list[dict]:
    """Everything the search box can find, best matches first within each type.

    Keys are short because the whole list is shipped to the browser:
    t type, l label, s sub-label, u url, k folded text to match against.
    """
    out: list[dict] = []
    for r in conn.execute(
        """SELECT p.slug, p.prenom, p.nom, p.role, p.search_key, g.code FROM person p
           LEFT JOIN groupe g ON g.id = p.group_id
           ORDER BY p.active DESC, (p.chamber IN ('an', 'senat', 'gouv')) DESC, p.top_score DESC, p.nom, p.prenom"""
    ):
        sub = " · ".join(x for x in (r["code"], r["role"]) if x)
        out.append(
            {
                "t": "Élu",
                "l": f"{r['prenom']} {r['nom']}",
                "s": sub,
                "u": f"/personne/{r['slug']}/",
                "k": r["search_key"],
            }
        )
    for r in conn.execute(
        """SELECT slug, name, search_key, n_holders FROM company WHERE kind <> 'fund'
           ORDER BY n_holders DESC, name"""
    ):
        n = r["n_holders"]
        out.append(
            {
                "t": "Société",
                "l": r["name"],
                "s": f"{n} détenteur{'s' if n != 1 else ''}",
                "u": f"/societe/{r['slug']}/",
                "k": r["search_key"],
            }
        )
    for r in conn.execute("SELECT slug, title, search_key, legislature FROM bill ORDER BY n_scrutins DESC, id"):
        out.append(
            {
                "t": "Texte",
                "l": labels.short_bill(r["title"], 80),
                "s": f"{r['legislature']}e législature",
                "u": f"/texte/{r['slug']}/",
                "k": r["search_key"],
            }
        )
    return out


def network(conn: sqlite3.Connection, scope: str) -> dict:
    """Bipartite graph: people, and the companies at least two of them hold.

    The browser narrows it further (sector, minimum number of holders).
    """
    rows = _rows(
        conn,
        f"""SELECT p.id AS pid, p.slug AS pslug, p.prenom, p.nom, p.top_score, g.code, g.color, g.color_dark,
                   c.id AS cid, c.slug, c.name, SUM(h.value_eur) AS value,
                   (SELECT GROUP_CONCAT(sector) FROM company_sector cs WHERE cs.company_id = c.id) AS sectors
            FROM holding h JOIN person p ON p.current_decl_id = h.declaration_id
            JOIN company c ON c.id = h.company_id LEFT JOIN groupe g ON g.id = p.group_id
            WHERE {scope_sql(scope)} AND c.kind NOT IN ('fund', 'coop_bank') AND h.kind = 'participation'
            GROUP BY p.id, c.id ORDER BY c.id, p.id""",
    )
    counts: dict[int, int] = defaultdict(int)
    for r in rows:
        counts[r["cid"]] += 1
    nodes, links = {}, []
    for r in rows:
        if counts[r["cid"]] < 2:
            continue
        nodes.setdefault(
            f"p{r['pid']}",
            {
                "id": f"p{r['pid']}",
                "type": "person",
                "label": f"{r['prenom']} {r['nom']}",
                "url": f"/personne/{r['pslug']}/",
                "code": r["code"],
                "color": r["color"],
                "color_dark": r["color_dark"],
            },
        )
        nodes.setdefault(
            f"c{r['cid']}",
            {
                "id": f"c{r['cid']}",
                "type": "company",
                "label": r["name"],
                "url": f"/societe/{r['slug']}/",
                "n": counts[r["cid"]],
                "sectors": (r["sectors"] or "").split(",") if r["sectors"] else [],
            },
        )
        links.append({"source": f"p{r['pid']}", "target": f"c{r['cid']}"})
    return {"nodes": list(nodes.values()), "links": links}
