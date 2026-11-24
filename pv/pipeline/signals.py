"""Signal engine: cross declarations with votes, roles and lobbying.

A signal is a documented coincidence — never a finding of wrongdoing. Each
one states which declared interest meets which public act, carries the
evidence needed to check it at the source, and gets a score so the ones
worth a journalist's hour float to the top.

What links an interest to a bill, strongest first:

* ``named``  — the bill's title names the company;
* ``lobby``  — the company (or an organisation tied to it) declared lobbying
  that mentions the bill;
* ``sector`` — the company and the bill belong to the same sector.

Budget and catch-all bills only ever produce ``named`` links: everybody
lobbies on the finance bill, so a match there singles nobody out.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

from pv import labels, sectors
from pv.text import fold

HALF_YEAR = timedelta(days=183)

LEVEL_FORT, LEVEL_MOYEN, LEVEL_FAIBLE = 52, 40, 28

BASE: dict[tuple[str, str], int] = {
    ("holding", "named"): 42, ("holding", "lobby"): 32, ("holding", "sector"): 18,
    ("activite", "named"): 50, ("activite", "lobby"): 34, ("activite", "sector"): 18,
    ("conseil", "named"): 50, ("conseil", "lobby"): 34, ("conseil", "sector"): 18,
    ("dirigeant", "named"): 50, ("dirigeant", "lobby"): 34, ("dirigeant", "sector"): 18,
    ("conjoint", "named"): 46, ("conjoint", "lobby"): 30, ("conjoint", "sector"): 16,
    ("collaborateur", "named"): 36, ("collaborateur", "lobby"): 24,
    ("benevole", "named"): 30, ("benevole", "lobby"): 18,
}  # fmt: skip

# Minimum declared value for a stake to count, by link strength and company kind.
# Cooperative-bank member shares and personal vehicles (SCI, GFA…) are held by
# a large share of officials for reasons unrelated to any bill.
_KNOWN_KINDS = {"listed", "unlisted", "coop_bank", "org"}
MIN_VALUE = {
    "lobby": {"default": 2_000, "coop_bank": 20_000, "other": 20_000},
    "sector": {"default": 10_000, "coop_bank": 30_000, "other": 50_000},
}

# How much the declared amount adds on top of the base, per link strength.
VALUE_WEIGHT = {"named": 1.3, "lobby": 1.0, "sector": 1.0}
# A family SCI or farm vehicle shares a "sector" with many bills without being
# a market bet on any of them: such links start lower.
PERSONAL_SECTOR_BASE = 12

KIND_OF_REL = {
    "holding": "vote_participation",
    "activite": "vote_activite",
    "conseil": "vote_activite",
    "dirigeant": "vote_activite",
    "benevole": "vote_activite",
    "conjoint": "vote_famille",
    "collaborateur": "vote_collaborateur",
}

MINISTRY_RULES: list[tuple[re.Pattern[str], tuple[str, ...]]] = [
    (re.compile(r"\bsante\b|\bsolidarites\b|\bautonomie\b"), ("sante", "assurance")),
    (re.compile(r"\bagricultur|\balimenta|\bpeche\b|\bforet"), ("agriculture",)),
    (re.compile(r"\benergie\b"), ("energie",)),
    (re.compile(r"\bindustrie\b"), ("industrie", "automobile", "energie")),
    (re.compile(r"\beconomie\b|\bfinances\b|\bcomptes publics\b|\bbudget\b"), ("banque", "assurance")),
    (re.compile(r"\btransports?\b"), ("transport", "automobile")),
    (re.compile(r"\barmees\b|\bdefense\b"), ("defense",)),
    (re.compile(r"\bnumerique\b|\bintelligence artificielle\b"), ("numerique",)),
    (re.compile(r"\blogement\b|\bville\b"), ("immobilier", "btp")),
    (re.compile(r"\bculture\b"), ("medias",)),
    (re.compile(r"\btransition ecologique\b|\bbiodiversite\b"), ("environnement", "energie")),
    (re.compile(r"\bcommerce\b|\bartisanat\b|\bconsommation\b|\btourisme\b"), ("conso",)),
]


def ministry_sectors(role: str | None) -> set[str]:
    t = fold(role)
    out: set[str] = set()
    for rule, secs in MINISTRY_RULES:
        if rule.search(t):
            out.update(secs)
    return out


def materiality(value: float | None) -> float:
    """0 at 1 k€, 8 at 10 k€, 16 at 100 k€, capped at 24 from 1 M€."""
    if not value or value <= 1000:
        return 0.0
    return min(24.0, 8.0 * math.log10(value / 1000))


def level_for(score: int) -> str | None:
    if score >= LEVEL_FORT:
        return "fort"
    if score >= LEVEL_MOYEN:
        return "moyen"
    if score >= LEVEL_FAIBLE:
        return "faible"
    return None


def _d(iso: str) -> date:
    return date.fromisoformat(iso[:10])


@dataclass
class Link:
    rel: str
    strength: str
    company_id: int | None
    name: str
    value: int | None = None
    shares: int | None = None
    pct: float | None = None
    detail: str | None = None
    pay: int | None = None
    kind: str = "other"

    @property
    def base(self) -> int:
        return BASE.get((self.rel, self.strength), 0)


@dataclass
class Signal:
    kind: str
    score: int
    person_id: int | None
    title: str
    summary: str
    evidence: dict
    company_id: int | None = None
    bill_id: int | None = None
    date: str | None = None
    people: list[int] = field(default_factory=list)
    level: str | None = None


class Engine:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.signals: list[Signal] = []
        self._load()

    # -- loading -------------------------------------------------------------

    def _load(self) -> None:
        c = self.conn
        self.persons = {
            r["id"]: dict(r)
            for r in c.execute(
                "SELECT id, slug, chamber, active, role, committee, group_id, civilite, prenom, nom FROM person"
            )
        }
        self.companies = {
            r["id"]: {**dict(r), "sectors": set()} for r in c.execute("SELECT id, slug, name, kind FROM company")
        }
        self.legal_form = {r["id"]: r["legal_form"] for r in c.execute("SELECT id, legal_form FROM company")}
        for r in c.execute("SELECT company_id, sector FROM company_sector"):
            if r["company_id"] in self.companies:
                self.companies[r["company_id"]]["sectors"].add(r["sector"])

        self.lobby: dict[int, dict] = {}
        for r in c.execute(
            """SELECT company_id, COUNT(*) n_orgs, SUM(n_actions_parl) n_parl, SUM(n_actions) n_actions,
                      MAX(spend_max) spend_max, MAX(spend_min) spend_min
               FROM lobby_org WHERE company_id IS NOT NULL GROUP BY company_id"""
        ):
            self.lobby[r["company_id"]] = dict(r)

        self.series: dict[int, list[dict]] = defaultdict(list)
        self.decl: dict[int, dict] = {}
        for r in c.execute(
            """SELECT id, person_id, filed_at, doc_type, mandate_kind, source_file
               FROM declaration ORDER BY person_id, filed_at, id"""
        ):
            d = dict(r)
            self.decl[d["id"]] = d
            if d["doc_type"] in ("DI", "DIA"):
                self.series[d["person_id"]].append(d)

        self.holdings: dict[int, list[dict]] = defaultdict(list)
        for r in c.execute(
            """SELECT declaration_id, company_id, MIN(label) label, SUM(value_eur) value, SUM(shares) shares,
                      MAX(capital_pct) pct, MIN(sectors) sectors, MIN(kind) kind
               FROM holding WHERE kind IN ('participation', 'non_cotee', 'sci')
               GROUP BY declaration_id, COALESCE(company_id, -id)"""
        ):
            self.holdings[r["declaration_id"]].append(dict(r))

        self.interests: dict[int, list[dict]] = defaultdict(list)
        for r in c.execute(
            """SELECT declaration_id, kind, company_id, org, description, person_name, kept, end_date,
                      start_date, pay_max
               FROM interest WHERE company_id IS NOT NULL AND kind <> 'mandat'"""
        ):
            self.interests[r["declaration_id"]].append(dict(r))

        self.bills = {
            r["id"]: {**dict(r), "sectors": set(), "short": labels.short_bill(r["title"])}
            for r in c.execute(
                "SELECT id, title, legislature, transversal, budget, first_vote, last_vote, n_scrutins, url FROM bill"
            )
        }
        for r in c.execute("SELECT bill_id, sector FROM bill_sector"):
            if r["bill_id"] in self.bills:
                self.bills[r["bill_id"]]["sectors"].add(r["sector"])
        self.named: dict[int, set[int]] = defaultdict(set)
        for r in c.execute("SELECT bill_id, company_id FROM bill_company"):
            self.named[r["bill_id"]].add(r["company_id"])
        self.bill_lobby: dict[int, dict[int, list[dict]]] = defaultdict(lambda: defaultdict(list))
        for r in c.execute(
            """SELECT bl.bill_id, o.company_id, o.name org, a.year, a.subject
               FROM bill_lobby bl JOIN lobby_action a ON a.id = bl.action_id
               JOIN lobby_org o ON o.id = a.org_id
               WHERE o.company_id IS NOT NULL ORDER BY a.year DESC"""
        ):
            self.bill_lobby[r["bill_id"]][r["company_id"]].append(
                {"org": r["org"], "year": r["year"], "subject": r["subject"]}
            )

        self.votes: dict[int, dict[int, dict]] = defaultdict(dict)
        for r in c.execute("SELECT * FROM person_bill"):
            self.votes[r["person_id"]][r["bill_id"]] = dict(r)
        self.roles: dict[int, dict[int, list[str]]] = defaultdict(lambda: defaultdict(list))
        for r in c.execute("SELECT bill_id, person_id, role FROM bill_role"):
            self.roles[r["person_id"]][r["bill_id"]].append(r["role"])

        self.changes: dict[int, list[dict]] = defaultdict(list)
        bulk: dict[tuple[int, int], int] = defaultdict(int)
        for r in c.execute("SELECT * FROM position_change ORDER BY window_end"):
            ch = dict(r)
            self.changes[ch["person_id"]].append(ch)
            bulk[(ch["person_id"], ch["curr_decl_id"])] += 1
        for chs in self.changes.values():
            for ch in chs:
                ch["bulk"] = bulk[(ch["person_id"], ch["curr_decl_id"])]

    # -- helpers -------------------------------------------------------------

    def _decl_in_force(self, pid: int, first: str, last: str) -> dict | None:
        """The declaration that describes *pid*'s interests around [first, last]."""
        series = self.series.get(pid)
        if not series:
            return None
        before = [d for d in series if d["filed_at"][:10] <= last]
        if before:
            return before[-1]
        # Elected, voted, then filed: the first declaration still describes the period.
        earliest = series[0]
        if _d(earliest["filed_at"]) <= _d(first) + HALF_YEAR:
            return earliest
        return None

    def _strength(self, bill: dict, cid: int | None, sector_csv: str | None = None) -> str | None:
        if cid is not None and cid in self.named.get(bill["id"], ()):
            return "named"
        if bill["transversal"]:
            return None
        if cid is not None and cid in self.bill_lobby.get(bill["id"], {}):
            return "lobby"
        secs = self.companies[cid]["sectors"] if cid is not None else set((sector_csv or "").split(","))
        if secs & bill["sectors"]:
            return "sector"
        return None

    def _material(self, strength: str, kind: str, value: int | None, pct: float | None) -> bool:
        if strength == "named":
            return True
        table = MIN_VALUE[strength]
        floor = table.get(kind if kind in ("coop_bank",) else ("default" if kind in _KNOWN_KINDS else "other"))
        v = value or 0
        if strength == "sector" and pct and pct >= 10 and v >= 2_000 and kind in _KNOWN_KINDS:
            return True
        return v >= floor

    def _holding_links(self, decl_id: int, bill: dict) -> list[Link]:
        out = []
        for h in self.holdings.get(decl_id, ()):
            cid = h["company_id"]
            co = self.companies.get(cid) if cid is not None else None
            kind = co["kind"] if co else "unnamed"
            if kind == "fund":
                continue
            s = self._strength(bill, cid, h["sectors"])
            if s is None or not self._material(s, kind, h["value"], h["pct"]):
                continue
            out.append(
                Link("holding", s, cid, co["name"] if co else h["label"], h["value"], h["shares"], h["pct"], kind=kind)
            )
        return out

    def _interest_links(self, decl_id: int, bill: dict) -> list[Link]:
        out, seen = [], set()
        horizon = str(int(bill["first_vote"][:4]) - 3)
        for it in self.interests.get(decl_id, ()):
            cid = it["company_id"]
            co = self.companies.get(cid)
            if co is None:
                continue
            rel = it["kind"]
            if rel in ("activite", "conseil", "dirigeant", "benevole"):
                ended = it["end_date"] and it["end_date"][:4] < horizon
                if ended and not it["kept"]:
                    continue
            s = self._strength(bill, cid)
            if s is None or (rel, s) not in BASE:
                continue
            if s == "sector" and co["kind"] not in _KNOWN_KINDS:
                continue
            if (rel, cid) in seen:
                continue
            seen.add((rel, cid))
            out.append(Link(rel, s, cid, co["name"], detail=_interest_detail(it), pay=it["pay_max"], kind=co["kind"]))
        return out

    def _trades_near(self, pid: int, cids: set[int], first: str, last: str) -> list[dict]:
        lo, hi = _d(first) - HALF_YEAR, _d(last) + HALF_YEAR
        return [
            ch
            for ch in self.changes.get(pid, ())
            if ch["company_id"] in cids and _d(ch["window_start"]) <= hi and _d(ch["window_end"]) >= lo
        ]

    def _overlap_sectors(self, pid: int) -> set[str]:
        """Sectors the person has a say on: permanent committee or ministry."""
        p = self.persons[pid]
        if not p["active"]:
            return set()
        if p["chamber"] == "gouv":
            return ministry_sectors(p["role"])
        return set(sectors.committee_sectors(p["committee"] or ""))

    # -- vote-linked signals ---------------------------------------------------

    def vote_signals(self) -> None:
        for pid in set(self.votes) | set(self.roles):
            if pid not in self.series:
                continue
            bill_ids = set(self.votes.get(pid, {})) | set(self.roles.get(pid, {}))
            for bid in bill_ids:
                bill = self.bills.get(bid)
                if bill is None or not bill["first_vote"]:
                    continue
                pb = self.votes.get(pid, {}).get(bid)
                first = pb["first_date"] if pb else bill["first_vote"]
                last = pb["last_date"] if pb else bill["last_vote"]
                decl = self._decl_in_force(pid, first, last)
                if decl is None:
                    continue
                links = self._holding_links(decl["id"], bill) + self._interest_links(decl["id"], bill)
                if not links:
                    continue
                self._emit_vote_signal(pid, bill, pb, decl, links, first, last)

    def _emit_vote_signal(self, pid, bill, pb, decl, links: list[Link], first: str, last: str) -> None:
        links.sort(key=lambda ln: (-ln.base, ln.kind not in _KNOWN_KINDS, -(ln.value or 0)))
        # One company reached through two relations (employer and board seat) is one link.
        seen_co: set[int] = set()
        unique = []
        for ln in links:
            if ln.company_id is not None:
                if ln.company_id in seen_co:
                    continue
                seen_co.add(ln.company_id)
            unique.append(ln)
        links = unique
        best = links[0]
        roles = self.roles.get(pid, {}).get(bill["id"], [])
        if pb is None and not roles:
            return

        personal = best.rel == "holding" and best.strength == "sector" and best.kind not in _KNOWN_KINDS
        score = float(PERSONAL_SECTOR_BASE if personal else best.base)
        if bill["budget"]:
            score *= 0.7
        top_holdings = [ln for ln in links if ln.rel == "holding" and ln.strength == best.strength]
        exposure = sum(
            (ln.value or 0) * (0.25 if ln.kind == "coop_bank" else 1.0 if ln.kind in _KNOWN_KINDS else 0.5)
            for ln in top_holdings
        )
        if best.rel == "holding":
            score += materiality(exposure) * VALUE_WEIGHT[best.strength]
            if any(ln.pct and ln.pct >= 10 and ln.kind in _KNOWN_KINDS for ln in top_holdings):
                score += 5
        else:
            if best.rel in ("dirigeant", "activite") and not best.pay:
                score -= 10  # unpaid seat (often held ex officio) or unpaid past post
            elif best.pay:
                score += 8 if best.pay >= 20_000 else 4

        cids = {ln.company_id for ln in links if ln.rel == "holding" and ln.company_id is not None}
        trades = self._trades_near(pid, cids, first, last)
        if trades:
            bonus = max(
                {"shares": 18, "presence": 14, "value": 8}[t["basis"]]
                * (0.5 if t["bulk"] >= 5 else 1.0)
                * (1.0 if t["same_mandate"] else 0.5)
                * (1.0 if self.companies[t["company_id"]]["kind"] in _KNOWN_KINDS else 0.5)
                for t in trades
            )
            score += bonus
        if "rapporteur" in roles:
            score += 15
        elif "auteur" in roles:
            score += 8
        if pb:
            if pb["n_dissent"]:
                score += min(10, 4 + 2 * pb["n_dissent"])
            if pb["n_votes"] >= 10:
                score += 3
            if pb["final_position"]:
                score += 3
        if len({ln.rel for ln in links}) > 1:
            score += 5
        score_i = int(min(100, round(score)))
        if level_for(score_i) is None:
            return

        if trades:
            kind = "mouvement_vote"
        elif roles:
            kind = "rapporteur"
        else:
            kind = KIND_OF_REL[best.rel]

        quotes = []
        for ln in links:
            if ln.strength == "lobby" and ln.company_id is not None:
                quotes += self.bill_lobby[bill["id"]][ln.company_id][:2]
        evidence = {
            "links": [_link_json(ln, self.companies) for ln in links[:8]],
            "n_links": len(links),
            "votes": _votes_json(pb),
            "trades": [_trade_json(t, self.companies) for t in trades[:6]],
            "roles": roles,
            "lobby": quotes[:3],
            "decl": _decl_json(decl),
            "bill": {"id": bill["id"], "short": bill["short"], "budget": bool(bill["budget"])},
            "sample_votes": self._sample_votes(pid, bill["id"]) if pb else [],
        }
        names = _join_names([ln.name for ln in links[:3]], len(links))
        self.signals.append(
            Signal(
                kind=kind,
                score=score_i,
                person_id=pid,
                company_id=best.company_id,
                bill_id=bill["id"],
                date=last,
                title=f"{names} · {bill['short']}",
                summary=_vote_summary(best, links, exposure, pb, roles, trades, bill, self.companies),
                evidence=evidence,
            )
        )

    def _sample_votes(self, pid: int, bid: int) -> list[dict]:
        rows = self.conn.execute(
            """SELECT s.date, s.title, s.numero, s.legislature, s.kind, s.outcome, v.position, v.dissent
               FROM vote v JOIN scrutin s ON s.id = v.scrutin_id
               WHERE v.person_id = ? AND s.bill_id = ? AND v.position <> 0
               ORDER BY v.dissent DESC, (s.kind = 'ensemble') DESC, s.date DESC LIMIT 5""",
            (pid, bid),
        ).fetchall()
        return [dict(r) for r in rows]

    # -- standing signals: committee / ministry --------------------------------

    def position_signals(self) -> None:
        for pid, p in self.persons.items():
            if not p["active"] or pid not in self.series and p["chamber"] != "gouv":
                continue
            secs = self._overlap_sectors(pid)
            if not secs:
                continue
            decl_ids = []
            if self.series.get(pid):
                decl_ids.append(self.series[pid][-1]["id"])
            if p["chamber"] == "gouv":
                dsp = [d for d in self.decl.values() if d["person_id"] == pid and d["doc_type"].startswith("DSP")]
                if dsp:
                    decl_ids.append(max(dsp, key=lambda d: d["filed_at"])["id"])
            lines, seen, effective, big = [], set(), 0.0, False
            for did in decl_ids:
                for h in self.holdings.get(did, ()):
                    cid = h["company_id"]
                    co = self.companies.get(cid) if cid is not None else None
                    kind = co["kind"] if co else "unnamed"
                    if kind == "fund":
                        continue
                    hs = (co["sectors"] if co else set((h["sectors"] or "").split(","))) & secs
                    key = cid if cid is not None else (did, h["label"], h["value"])
                    if not hs or not h["value"] or key in seen:
                        continue
                    seen.add(key)
                    weight = 1.0 if kind in ("listed", "unlisted", "org") else 0.25 if kind == "coop_bank" else 0.3
                    effective += h["value"] * weight
                    big = big or bool(h["pct"] and h["pct"] >= 10 and kind in _KNOWN_KINDS)
                    lines.append(
                        {
                            "company": _co_json(co),
                            "name": co["name"] if co else h["label"],
                            "value": h["value"],
                            "shares": h["shares"],
                            "pct": h["pct"],
                            "sectors": sorted(hs),
                        }
                    )
            minister = p["chamber"] == "gouv"
            if effective < (5_000 if minister else 20_000):
                continue
            score = int(min(100, round((38 if minister else 30) + materiality(effective) + (5 if big else 0))))
            if level_for(score) is None:
                continue
            lines.sort(key=lambda ln: -(ln["value"] or 0))
            total = sum(ln["value"] for ln in lines)
            sec_labels = ", ".join(sectors.label(s).lower() for s in sorted({s for ln in lines for s in ln["sectors"]}))
            where = "Ministère" if minister else p["committee"]
            self.signals.append(
                Signal(
                    kind="ministere" if minister else "commission",
                    score=score,
                    person_id=pid,
                    company_id=lines[0]["company"]["id"] if lines[0]["company"] else None,
                    date=self.decl[decl_ids[0]]["filed_at"][:10],
                    title=f"{where} · {labels.fmt_eur(total)} en {sec_labels}",
                    summary=(
                        f"{'Ministre' if minister else 'Membre de la ' + (p['committee'] or 'commission')} "
                        f"et détient {labels.fmt_eur(total)} dans {len(lines)} ligne{'s' if len(lines) > 1 else ''} "
                        f"du secteur ({_join_names([ln['name'] for ln in lines[:3]], len(lines))})."
                    ),
                    evidence={
                        "lines": lines[:10],
                        "n_lines": len(lines),
                        "total": total,
                        "where": p["role"] if minister else p["committee"],
                        "sectors": sorted(secs),
                        "decl": _decl_json(self.decl[decl_ids[0]]),
                    },
                )
            )

    # -- standing signals: paid by / married to / staffed by a lobby ------------

    def link_signals(self) -> None:
        for pid, series in self.series.items():
            p = self.persons[pid]
            decl = series[-1]
            overlap = self._overlap_sectors(pid)
            recent = str(int(decl["filed_at"][:4]) - 3)
            seen: set[tuple[str, int]] = set()
            for it in self.interests.get(decl["id"], ()):
                cid = it["company_id"]
                co = self.companies.get(cid)
                if co is None or co["kind"] not in _KNOWN_KINDS:
                    continue
                rel = it["kind"]
                lob = self.lobby.get(cid)
                current = bool(it["kept"]) or not it["end_date"]
                if rel == "conjoint":
                    kind, score = "lien_famille", 26.0
                elif rel == "collaborateur":
                    kind, score = "lien_collaborateur", 24.0
                elif rel == "benevole":
                    if not current or lob is None:
                        continue
                    kind, score = "lien_activite", 16.0
                else:
                    kind = "lien_activite"
                    pay = it["pay_max"] or 0
                    if rel == "conseil":
                        score = 30.0 if current else 20.0
                    elif rel == "dirigeant":
                        if current:
                            score = 26.0 if pay >= 1_000 else 16.0
                        elif pay >= 1_000 and (it["end_date"] or "")[:4] >= recent:
                            score = 14.0  # a paid seat left in the last three years
                        else:
                            continue
                    else:  # salaried employment
                        score = 18.0 if current else 12.0
                    score += 10 if pay >= 30_000 else 5 if pay >= 5_000 else 0
                if (kind, cid) in seen:
                    continue
                seen.add((kind, cid))
                if lob:
                    score += min(10.0, 2.0 * math.log2(1 + (lob["n_parl"] or 0)))
                if co["kind"] == "listed":
                    score += 4
                shared = co["sectors"] & overlap
                if shared:
                    score += 10
                if p["chamber"] not in ("an", "senat", "gouv", "europe"):
                    score -= 6
                score_i = int(min(100, round(score)))
                if level_for(score_i) is None:
                    continue
                who = {"lien_famille": "Conjoint·e", "lien_collaborateur": "Collaborateur·rice"}.get(
                    kind, labels.RELATIONS[rel]
                )
                bits = [f"{who} : {co['name']}"]
                if it["description"]:
                    bits.append(it["description"])
                if not current and it["end_date"] and rel not in ("conjoint", "collaborateur"):
                    bits.append(f"jusqu'en {labels.fmt_date(it['end_date'])}")
                if lob and lob["n_actions"]:
                    bits.append(
                        f"organisation inscrite au répertoire des représentants d'intérêts "
                        f"({lob['n_actions']} actions déclarées, dont {lob['n_parl'] or 0} visant des parlementaires)"
                    )
                if shared:
                    bits.append(
                        "secteur couvert par " + ("son ministère" if p["chamber"] == "gouv" else "sa commission")
                    )
                self.signals.append(
                    Signal(
                        kind=kind,
                        score=score_i,
                        person_id=pid,
                        company_id=cid,
                        date=decl["filed_at"][:10],
                        title=f"{who} · {co['name']}",
                        summary=" — ".join(bits) + ".",
                        evidence={
                            "rel": rel,
                            "company": _co_json(co),
                            "org": it["org"],
                            "description": it["description"],
                            "person_name": it["person_name"],
                            "current": current,
                            "start": it["start_date"],
                            "end": it["end_date"],
                            "pay": it["pay_max"],
                            "lobby": lob,
                            "shared_sectors": sorted(shared),
                            "decl": _decl_json(decl),
                        },
                    )
                )

    # -- movements on their own ------------------------------------------------

    def trade_signals(self) -> None:
        for pid, chs in self.changes.items():
            overlap = self._overlap_sectors(pid)
            p = self.persons[pid]
            parl = p["chamber"] in ("an", "senat", "gouv", "europe")
            bulks: dict[int, list[dict]] = defaultdict(list)
            for ch in chs:
                if not ch["same_mandate"]:
                    continue
                co = self.companies[ch["company_id"]]
                if co["kind"] == "fund":
                    continue
                if ch["bulk"] >= 5:
                    bulks[ch["curr_decl_id"]].append(ch)
                    continue
                value = ch["value_after"] if ch["kind"] in ("entree", "renforcement") else ch["value_before"]
                value = value or 0
                floor = 10_000 if co["kind"] in _KNOWN_KINDS else 50_000
                if value < floor:
                    continue
                score = 26 + 0.8 * materiality(value) + (4 if ch["basis"] == "shares" else 0)
                shared = co["sectors"] & overlap
                if shared:
                    score += 10
                if not parl:
                    score -= 6
                score_i = int(min(100, round(score)))
                if level_for(score_i) is None:
                    continue
                self.signals.append(
                    Signal(
                        kind="mouvement",
                        score=score_i,
                        person_id=pid,
                        company_id=co["id"],
                        date=ch["window_end"],
                        title=f"{labels.CHANGES[ch['kind']]} · {co['name']}",
                        summary=_trade_sentence(ch, co["name"])
                        + (
                            " Secteur couvert par " + ("son ministère." if p["chamber"] == "gouv" else "sa commission.")
                            if shared
                            else ""
                        ),
                        evidence={
                            "trades": [_trade_json(ch, self.companies)],
                            "shared_sectors": sorted(shared),
                            "decl": _decl_json(self.decl[ch["curr_decl_id"]]),
                            "prev_decl": _decl_json(self.decl[ch["prev_decl_id"]]),
                        },
                    )
                )
            for did, group in bulks.items():
                total = sum((c["value_after"] or c["value_before"] or 0) for c in group)
                if total < 20_000:
                    continue
                score_i = int(round(24 + 0.5 * materiality(total) - (0 if parl else 6)))
                if level_for(score_i) is None:
                    continue
                n_in = sum(c["kind"] == "entree" for c in group)
                n_out = sum(c["kind"] == "sortie" for c in group)
                self.signals.append(
                    Signal(
                        kind="mouvement",
                        score=score_i,
                        person_id=pid,
                        date=group[0]["window_end"],
                        title=f"{len(group)} lignes modifiées d'un coup · {labels.fmt_eur(total)}",
                        summary=(
                            f"{n_in} ligne{'s' if n_in > 1 else ''} apparue{'s' if n_in > 1 else ''} et {n_out} disparue"
                            f"{'s' if n_out > 1 else ''} entre les déclarations du {labels.fmt_date(group[0]['window_start'])} "
                            f"et du {labels.fmt_date(group[0]['window_end'])}. Un changement en bloc correspond souvent "
                            "à une déclaration complétée ou à un portefeuille sous mandat de gestion."
                        ),
                        evidence={
                            "trades": [
                                _trade_json(c, self.companies)
                                for c in sorted(group, key=lambda c: -(c["value_after"] or c["value_before"] or 0))[:12]
                            ],
                            "n_trades": len(group),
                            "bulk": True,
                            "decl": _decl_json(self.decl[did]),
                            "prev_decl": _decl_json(self.decl[group[0]["prev_decl_id"]]),
                        },
                    )
                )

    # -- the recusal register ----------------------------------------------------

    def deport_signals(self) -> None:
        for r in self.conn.execute("SELECT * FROM deport ORDER BY date DESC").fetchall():
            bill = self.bills.get(r["bill_id"]) if r["bill_id"] else None
            target = (r["target"] or "").strip()
            self.signals.append(
                Signal(
                    kind="deport",
                    score=15,
                    level="info",
                    person_id=r["person_id"],
                    bill_id=r["bill_id"],
                    date=r["date"],
                    title="Déport · " + (bill["short"] if bill else (target[:80] + ("…" if len(target) > 80 else ""))),
                    summary=(r["reason"] or "Motif non précisé.").strip(),
                    evidence={
                        "target": target,
                        "target_kind": r["target_kind"],
                        "scope": r["scope"],
                        "reason": r["reason"],
                        "legislature": r["legislature"],
                        "bill": {"id": bill["id"], "short": bill["short"]} if bill else None,
                    },
                )
            )

    # -- shared unlisted companies -------------------------------------------------

    def co_holding_signals(self) -> None:
        holders: dict[int, list[tuple[int, dict]]] = defaultdict(list)
        for pid, series in self.series.items():
            for h in self.holdings.get(series[-1]["id"], ()):
                cid = h["company_id"]
                if cid is not None and self.companies[cid]["kind"] == "other" and h["kind"] == "participation":
                    holders[cid].append((pid, h))
        for cid, hs in holders.items():
            pids = [pid for pid, _ in hs]
            if len(set(pids)) < 2:
                continue
            co = self.companies[cid]
            if len(co["slug"]) < 6:
                continue
            # A listed foreign stock missing from the registry looks like this too;
            # a legal form or a real share of the capital marks a private company.
            if not self.legal_form.get(cid) and not any((h["pct"] or 0) >= 1 for _, h in hs):
                continue
            total = sum((h["value"] or 0) for _, h in hs)
            n = len(set(pids))
            nat = sum(self.persons[p]["chamber"] in ("an", "senat", "gouv", "europe") for p in set(pids))
            groups = {self.persons[p]["group_id"] for p in set(pids) if self.persons[p]["group_id"]}
            score = 22 + min(15, 5 * (n - 2)) + 0.5 * materiality(total) + (8 if nat >= 2 else 3 if nat else 0)
            score += 6 if len(groups) == 1 and nat >= 2 else 0
            score_i = int(min(100, round(score)))
            if level_for(score_i) is None:
                continue
            hs.sort(key=lambda x: -(x[1]["value"] or 0))
            people = [
                {
                    "slug": self.persons[pid]["slug"],
                    "name": f"{self.persons[pid]['prenom']} {self.persons[pid]['nom']}",
                    "role": self.persons[pid]["role"],
                    "value": h["value"],
                    "shares": h["shares"],
                    "pct": h["pct"],
                }
                for pid, h in hs
            ]
            self.signals.append(
                Signal(
                    kind="co_detention",
                    score=score_i,
                    person_id=hs[0][0],
                    company_id=cid,
                    date=max(self.series[p][-1]["filed_at"][:10] for p in set(pids)),
                    title=f"{co['name']} · {n} détenteurs",
                    summary=(
                        f"{_join_names([p['name'] for p in people[:4]], len(people))} déclarent tous une participation "
                        f"dans {co['name']} ({labels.fmt_eur(total)} au total). Rapprochement fait sur le nom de la "
                        "société : à vérifier en cas d'homonymie."
                    ),
                    evidence={"company": _co_json(co), "holders": people, "total": total},
                    people=sorted(set(pids)),
                )
            )

    # -- output ----------------------------------------------------------------

    def store(self) -> dict[str, int]:
        c = self.conn
        c.execute("DELETE FROM signal_person")
        c.execute("DELETE FROM signal")
        stats: dict[str, int] = defaultdict(int)
        for s in self.signals:
            level = s.level or level_for(s.score)
            if level is None:
                continue
            cur = c.execute(
                """INSERT INTO signal(kind, score, level, person_id, company_id, bill_id, date, title, summary, evidence)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    s.kind,
                    s.score,
                    level,
                    s.person_id,
                    s.company_id,
                    s.bill_id,
                    s.date,
                    s.title,
                    s.summary,
                    json.dumps(s.evidence, ensure_ascii=False, separators=(",", ":")),
                ),
            )
            people = s.people or ([s.person_id] if s.person_id else [])
            c.executemany(
                "INSERT OR IGNORE INTO signal_person(signal_id, person_id) VALUES (?, ?)",
                [(cur.lastrowid, p) for p in people],
            )
            stats[level] += 1
            stats["total"] += 1
        return dict(stats)


# ---------------------------------------------------------------------------
# Evidence and wording
# ---------------------------------------------------------------------------


def _co_json(co: dict | None) -> dict | None:
    if co is None:
        return None
    return {"id": co["id"], "slug": co["slug"], "name": co["name"], "kind": co["kind"]}


def _decl_json(d: dict) -> dict:
    return {"id": d["id"], "filed": d["filed_at"][:10], "file": d["source_file"]}


def _link_json(ln: Link, companies: dict) -> dict:
    return {
        "rel": ln.rel,
        "strength": ln.strength,
        "name": ln.name,
        "company": _co_json(companies.get(ln.company_id)) if ln.company_id is not None else None,
        "value": ln.value,
        "shares": ln.shares,
        "pct": ln.pct,
        "detail": ln.detail,
        "pay": ln.pay,
    }


def _votes_json(pb: dict | None) -> dict | None:
    if pb is None:
        return None
    return {
        "n": pb["n_votes"],
        "pour": pb["n_pour"],
        "contre": pb["n_contre"],
        "abst": pb["n_abst"],
        "dissent": pb["n_dissent"],
        "first": pb["first_date"],
        "last": pb["last_date"],
        "final": labels.POSITIONS.get(pb["final_position"]) if pb["final_position"] else None,
    }


def _trade_json(ch: dict, companies: dict) -> dict:
    co = companies[ch["company_id"]]
    return {
        "company": _co_json(co),
        "kind": ch["kind"],
        "basis": ch["basis"],
        "from": ch["window_start"],
        "to": ch["window_end"],
        "shares_before": ch["shares_before"],
        "shares_after": ch["shares_after"],
        "value_before": ch["value_before"],
        "value_after": ch["value_after"],
        "bulk": ch.get("bulk", 1),
        "same_mandate": bool(ch["same_mandate"]),
    }


def _interest_detail(it: dict) -> str | None:
    parts = []
    if it["person_name"]:
        parts.append(it["person_name"])
    if it["description"]:
        parts.append(it["description"])
    if it["org"]:
        parts.append(it["org"])
    return " — ".join(parts) or None


def _join_names(names: list[str], total: int) -> str:
    names = list(dict.fromkeys(names))
    extra = total - len(names)
    if extra > 0:
        return ", ".join(names) + f" et {extra} autre{'s' if extra > 1 else ''}"
    if len(names) > 1:
        return ", ".join(names[:-1]) + " et " + names[-1]
    return names[0] if names else ""


def _trade_sentence(ch: dict, name: str) -> str:
    span = f"entre les déclarations du {labels.fmt_date(ch['window_start'])} et du {labels.fmt_date(ch['window_end'])}"
    sb, sa, vb, va = ch["shares_before"], ch["shares_after"], ch["value_before"], ch["value_after"]
    if ch["kind"] == "entree":
        qty = f"{labels.fmt_int(sa)} titres, " if sa else ""
        return f"{name} apparaît ({qty}{labels.fmt_eur(va)}) {span}."
    if ch["kind"] == "sortie":
        qty = f"{labels.fmt_int(sb)} titres, " if sb else ""
        return f"{name} disparaît ({qty}{labels.fmt_eur(vb)}) {span}."
    verb = "renforcée" if ch["kind"] == "renforcement" else "allégée"
    if ch["basis"] == "shares":
        return f"Ligne {name} {verb} de {labels.fmt_int(sb)} à {labels.fmt_int(sa)} titres {span}."
    return (
        f"Ligne {name} {verb} de {labels.fmt_eur(vb)} à {labels.fmt_eur(va)} {span} "
        "(nombre de titres non renseigné : peut refléter une variation de cours)."
    )


_REL_PHRASE = {
    "activite": "A travaillé pour",
    "conseil": "A conseillé",
    "dirigeant": "Exerce ou a exercé une fonction dirigeante chez",
    "benevole": "Exerce une fonction bénévole chez",
    "conjoint": "Conjoint·e employé·e par",
    "collaborateur": "Collaborateur·rice également employé·e par",
}


def _vote_summary(best: Link, links, exposure, pb, roles, trades, bill, companies) -> str:
    parts = []
    if best.rel == "holding":
        hs = [ln for ln in links if ln.rel == "holding" and ln.strength == best.strength]
        total = sum(ln.value or 0 for ln in hs)
        parts.append(
            f"Détient {labels.fmt_eur(total)} de {_join_names([ln.name for ln in hs[:3]], len(hs))}"
            f" ({labels.STRENGTHS[best.strength]})"
        )
    else:
        parts.append(f"{_REL_PHRASE[best.rel]} {best.name} ({labels.STRENGTHS[best.strength]})")
    if "rapporteur" in roles:
        parts.append("rapporteur du texte")
    elif "auteur" in roles:
        parts.append("auteur du texte")
    if pb:
        tally = ", ".join(
            f"{n} {w}"
            for n, w in ((pb["n_pour"], "pour"), (pb["n_contre"], "contre"), (pb["n_abst"], "abstention"))
            if n
        )
        s = f"a pris part à {pb['n_votes']} scrutin{'s' if pb['n_votes'] > 1 else ''} sur ce texte ({tally})"
        if pb["n_dissent"]:
            s += f", dont {pb['n_dissent']} à rebours de son groupe"
        parts.append(s)
    text = " ; ".join(parts) + "."
    if trades:
        t = trades[0]
        text += " " + _trade_sentence(t, companies[t["company_id"]]["name"])
    if bill["budget"]:
        text += " Texte budgétaire large : le lien est sectoriel, pas nominatif."
    return text


def compute_signals(conn: sqlite3.Connection) -> dict[str, int]:
    engine = Engine(conn)
    engine.vote_signals()
    engine.position_signals()
    engine.link_signals()
    engine.trade_signals()
    engine.deport_signals()
    engine.co_holding_signals()
    stats = engine.store()
    by_kind = defaultdict(int)
    for row in conn.execute("SELECT kind, COUNT(*) n FROM signal GROUP BY kind"):
        by_kind[row["kind"]] = row["n"]
    stats.update(by_kind)
    return stats
