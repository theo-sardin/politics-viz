"""Infer buys and sells from successive declarations.

France has no transaction reporting for elected officials: HATVP only holds
snapshots. A declaration lists every stake, so comparing two consecutive
ones dates a movement to the window between them. Share counts are the
evidence; a change in declared *value* alone is usually the market moving,
and is only used — and labelled as weaker — when share counts are missing.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

# Declarations for two mandates filed together describe the same day twice;
# their differences are form-filling noise, not trades.
MIN_GAP_DAYS = 4

SHARE_UP, SHARE_DOWN = 1.10, 0.90
VALUE_UP, VALUE_DOWN = 1.6, 0.6
VALUE_MIN_DELTA = 2000


@dataclass
class Line:
    shares: int | None
    value: int | None


def classify(before: Line | None, after: Line | None) -> tuple[str, str] | None:
    """(kind, basis) for the move between two snapshots of one company, or None."""
    if before is None and after is None:
        return None
    if before is None:
        return "entree", "presence"
    if after is None:
        return "sortie", "presence"

    sb, sa = _reliable(before), _reliable(after)
    vb, va = before.value or 0, after.value or 0
    if sb and sa:
        ratio = sa / sb
        # More shares for the same money is a split or a corrected typo.
        if vb and va and (ratio >= 1.8 or ratio <= 0.55) and 0.7 <= va / vb <= 1.3:
            return None
        if ratio >= SHARE_UP:
            return "renforcement", "shares"
        if ratio <= SHARE_DOWN:
            return "allegement", "shares"
        return None
    if vb > 0 and va > 0:
        ratio = va / vb
        if ratio >= VALUE_UP and va - vb >= VALUE_MIN_DELTA:
            return "renforcement", "value"
        if ratio <= VALUE_DOWN and vb - va >= VALUE_MIN_DELTA:
            return "allegement", "value"
    return None


def _reliable(line: Line) -> int | None:
    """Share count, unless it is obviously the value typed in the wrong box."""
    if not line.shares or line.shares <= 0:
        return None
    if line.value and line.shares == line.value and line.value > 50:
        return None
    return line.shares


def _days_between(a: str, b: str) -> int:
    from datetime import date

    return (date.fromisoformat(b[:10]) - date.fromisoformat(a[:10])).days


def compute_changes(conn: sqlite3.Connection) -> dict[str, int]:
    conn.execute("DELETE FROM position_change")
    stats = {"entree": 0, "sortie": 0, "renforcement": 0, "allegement": 0}

    decls: dict[int, list[sqlite3.Row]] = {}
    for d in conn.execute(
        """SELECT id, person_id, filed_at, mandate_kind FROM declaration
           WHERE doc_type IN ('DI', 'DIA') ORDER BY person_id, filed_at, id"""
    ):
        decls.setdefault(d["person_id"], []).append(d)

    snapshots: dict[int, dict[int, Line]] = {}
    for h in conn.execute(
        """SELECT declaration_id, company_id, SUM(shares) AS shares, SUM(value_eur) AS value
           FROM holding WHERE kind = 'participation' AND company_id IS NOT NULL
           GROUP BY declaration_id, company_id"""
    ):
        snapshots.setdefault(h["declaration_id"], {})[h["company_id"]] = Line(h["shares"], h["value"])

    rows = []
    for pid, series in decls.items():
        prev = series[0]
        for curr in series[1:]:
            if _days_between(prev["filed_at"], curr["filed_at"]) < MIN_GAP_DAYS:
                # Keep comparing against the earlier filing of the pair.
                if snapshots.get(curr["id"]):
                    prev = curr
                continue
            before, after = snapshots.get(prev["id"], {}), snapshots.get(curr["id"], {})
            for cid in before.keys() | after.keys():
                res = classify(before.get(cid), after.get(cid))
                if res is None:
                    continue
                kind, basis = res
                b, a = before.get(cid), after.get(cid)
                rows.append(
                    (pid, cid, prev["id"], curr["id"], prev["filed_at"][:10], curr["filed_at"][:10], kind, basis,
                     b.shares if b else None, a.shares if a else None,
                     b.value if b else None, a.value if a else None,
                     int(prev["mandate_kind"] == curr["mandate_kind"]))
                )
                stats[kind] += 1
            prev = curr
    conn.executemany(
        """INSERT INTO position_change(person_id, company_id, prev_decl_id, curr_decl_id, window_start,
                                       window_end, kind, basis, shares_before, shares_after, value_before,
                                       value_after, same_mandate)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )
    return stats
