"""Load people and their declarations.

Identity is the hard part. The same person shows up in four sources with
four spellings; a wrong merge would attach one person's holdings to
another. Matching therefore goes, in order of trust:

1. the HATVP index, which gives each declarant a stable page slug and, for
   parliamentarians, their Assemblée / Sénat identifier;
2. exact name + birth date;
3. exact name alone, only when a single candidate exists.

Anything weaker creates a separate person rather than guessing.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

from pv import companies, config
from pv.sources import hatvp, legacy
from pv.text import fold, name_key, titlecase_name

log = logging.getLogger(__name__)

# What a mandate kind says about where someone sits when no roster lists them.
KIND_CHAMBER = {
    "depute": "an",
    "senateur": "senat",
    "gouvernement": "gouv",
    "europe": "europe",
}


def names_compatible(nom_a: str, prenom_a: str, nom_b: str, prenom_b: str) -> bool:
    """Same person under two spellings: 'K/BIDI' vs 'KBIDI', 'Thiébault' vs 'Thiébault-Martinez'.

    Only ever used next to a second, independent key (same birth date, or
    same filing date and document type), never on its own.
    """
    pa, pb = fold(prenom_a).split(), fold(prenom_b).split()
    if not pa or not pb or pa[0] != pb[0]:
        return False
    ta, tb = fold(nom_a).split(), fold(nom_b).split()
    if not ta or not tb:
        return False
    ja, jb = "".join(ta), "".join(tb)
    if ja == jb or set(ta) <= set(tb) or set(tb) <= set(ta):
        return True
    return SequenceMatcher(None, ja, jb).ratio() >= 0.88


def _one_edit_apart(a: str, b: str) -> bool:
    """Two ISO dates that differ in a single component: a typo, not a namesake."""
    if len(a) != 10 or len(b) != 10:
        return False
    pa, pb = a.split("-"), b.split("-")
    return sum(x != y for x, y in zip(pa, pb)) == 1


class People:
    """In-memory person index backing identity resolution during a sync."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.by_slug: dict[str, int] = {}
        self.by_an: dict[str, int] = {}
        self.by_senat: dict[str, int] = {}
        self.by_key: dict[str, list[int]] = defaultdict(list)
        self.by_birth: dict[str, list[int]] = defaultdict(list)
        self.birth: dict[int, str | None] = {}
        self.names: dict[int, tuple[str, str]] = {}
        self.slug_of: dict[int, str] = {}

    def create(
        self,
        nom: str,
        prenom: str,
        *,
        civilite: str | None = None,
        birth: str | None = None,
        hatvp_slug: str | None = None,
        an_id: str | None = None,
        senat_id: str | None = None,
        photo_url: str | None = None,
    ) -> int:
        nom_d, prenom_d = titlecase_name(nom), titlecase_name(prenom)
        key = name_key(nom, prenom)
        cur = self.conn.execute(
            """INSERT INTO person(hatvp_slug, an_id, senat_id, civilite, prenom, nom, name_key,
                                  search_key, birth_date, photo_url)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (hatvp_slug, an_id, senat_id, _civ(civilite), prenom_d, nom_d, key,
             fold(f"{prenom} {nom}"), birth, photo_url),
        )
        pid = cur.lastrowid
        if hatvp_slug:
            self.by_slug[hatvp_slug] = pid
            self.slug_of[pid] = hatvp_slug
        if an_id:
            self.by_an[an_id] = pid
        if senat_id:
            self.by_senat[senat_id] = pid
        self.by_key[key].append(pid)
        self.birth[pid] = birth
        self.names[pid] = (nom, prenom)
        if birth:
            self.by_birth[birth].append(pid)
        return pid

    def find(self, nom: str, prenom: str, birth: str | None) -> int | None:
        """Exact name (+ birth date), then same birth date with a compatible spelling."""
        pid = self.find_by_name(name_key(nom, prenom), birth)
        if pid is None and birth:
            close = [c for c in self.by_birth.get(birth, []) if names_compatible(nom, prenom, *self.names[c])]
            if len(close) == 1:
                pid = close[0]
        return pid

    def find_by_name(self, key: str, birth: str | None) -> int | None:
        cands = self.by_key.get(key, [])
        if not cands:
            return None
        if birth:
            exact = [c for c in cands if self.birth[c] == birth]
            if exact:
                return exact[0]
            unknown = [c for c in cands if self.birth[c] is None]
            if len(unknown) == 1 and len(cands) == 1:
                self.set_birth(unknown[0], birth)
                return unknown[0]
            close = [c for c in cands if self.birth[c] and _one_edit_apart(self.birth[c], birth)]
            if len(close) == 1 and len(cands) == 1:
                return close[0]
            return None
        return cands[0] if len(cands) == 1 else None

    def set_birth(self, pid: int, birth: str | None) -> None:
        if birth and not self.birth.get(pid):
            self.birth[pid] = birth
            self.by_birth[birth].append(pid)
            self.conn.execute("UPDATE person SET birth_date = ? WHERE id = ?", (birth, pid))

    def set_slug(self, pid: int, slug: str | None, photo_url: str | None = None) -> None:
        if slug and slug not in self.by_slug and pid not in self.slug_of:
            self.by_slug[slug] = pid
            self.slug_of[pid] = slug
            self.conn.execute(
                "UPDATE person SET hatvp_slug = ?, photo_url = COALESCE(photo_url, ?) WHERE id = ? AND hatvp_slug IS NULL",
                (slug, photo_url, pid),
            )

    def set_ids(self, pid: int, *, an_id: str | None = None, senat_id: str | None = None) -> None:
        if an_id and an_id not in self.by_an:
            self.by_an[an_id] = pid
            self.conn.execute("UPDATE person SET an_id = ? WHERE id = ? AND an_id IS NULL", (an_id, pid))
        if senat_id and senat_id not in self.by_senat:
            self.by_senat[senat_id] = pid
            self.conn.execute("UPDATE person SET senat_id = ? WHERE id = ? AND senat_id IS NULL", (senat_id, pid))


def _civ(c: str | None) -> str | None:
    if not c:
        return None
    c = c.strip().rstrip(".").lower()
    return {"m": "M.", "mme": "Mme", "mlle": "Mme"}.get(c)


class CompanyStore:
    """Get-or-create for company rows, keyed by canonical slug."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.ids: dict[str, int] = {r["slug"]: r["id"] for r in conn.execute("SELECT id, slug FROM company")}

    def get(self, ref: companies.CompanyRef | None, *, kind: str | None = None) -> int | None:
        if ref is None or not ref.identifiable:
            return None
        cid = self.ids.get(ref.slug)
        if cid is not None:
            return cid
        cur = self.conn.execute(
            "INSERT INTO company(slug, name, search_key, kind, ticker, legal_form) VALUES (?, ?, ?, ?, ?, ?)",
            (ref.slug, ref.name, fold(ref.name), kind or ref.kind, ref.ticker, ref.legal_form),
        )
        cid = cur.lastrowid
        self.ids[ref.slug] = cid
        self.conn.executemany(
            "INSERT OR IGNORE INTO company_sector(company_id, sector) VALUES (?, ?)",
            [(cid, s) for s in ref.sectors],
        )
        return cid


@dataclass
class IndexedPerson:
    slug: str
    rows: list[hatvp.IndexRow]

    @property
    def first(self) -> hatvp.IndexRow:
        return self.rows[0]

    def origin(self, kind: str) -> str | None:
        for r in self.rows:
            if r.mandate_kind == kind and r.origin_id:
                return r.origin_id
        return None

    def photo(self) -> str | None:
        for r in self.rows:
            if r.photo_url:
                return r.photo_url
        return None


class HatvpIndex:
    """The declarations index, arranged for the lookups the loader needs."""

    def __init__(self, rows: list[hatvp.IndexRow]):
        self.people: dict[str, IndexedPerson] = {}
        self.by_filing: dict[tuple[str, str, str], list[hatvp.IndexRow]] = defaultdict(list)
        self.by_date: dict[tuple[str, str], list[hatvp.IndexRow]] = defaultdict(list)
        self.by_key: dict[str, set[str]] = defaultdict(set)
        self.by_origin: dict[str, str] = {}
        for r in rows:
            self.people.setdefault(r.hatvp_slug, IndexedPerson(r.hatvp_slug, [])).rows.append(r)
            self.by_key[r.key].add(r.hatvp_slug)
            if r.origin_id:
                self.by_origin.setdefault(r.origin_id, r.hatvp_slug)
            if r.filed:
                self.by_filing[(r.key, r.filed, _family(r.doc_code))].append(r)
                self.by_date[(r.filed, _family(r.doc_code))].append(r)

    def match(self, rec: hatvp.Declaration) -> tuple[str | None, hatvp.IndexRow | None]:
        """Index slug (and row, when the exact filing is found) for a declaration."""
        key = name_key(rec.nom, rec.prenom)
        day, family = rec.filed_at[:10], _family(rec.doc_code)
        hits = self.by_filing.get((key, day, family), [])
        if not hits:
            hits = [r for r in self.by_date.get((day, family), [])
                    if names_compatible(rec.nom, rec.prenom, r.nom, r.prenom)]
        slugs = {h.hatvp_slug for h in hits}
        if len(slugs) == 1:
            same_kind = [h for h in hits if h.mandate_kind == rec.mandate_kind]
            return hits[0].hatvp_slug, (same_kind or hits)[0]
        candidates = self.by_key.get(key, set())
        if len(candidates) == 1:
            return next(iter(candidates)), None
        return None, None


def _family(doc_code: str) -> str:
    """'diam' → 'dia'. The XML and the index disagree on which filings are amendments."""
    return doc_code[:-1] if doc_code.endswith("m") and len(doc_code) > 2 else doc_code


def load_declarations(
    conn: sqlite3.Connection,
    people: People,
    store: CompanyStore,
    xml_path: Path,
    index: HatvpIndex,
    legacy_path: Path | None = None,
) -> dict[str, int]:
    stats = {"declarations": 0, "archived": 0, "holdings": 0, "interests": 0, "people": 0, "unmatched_index": 0}
    seen: set[str] = set()

    def person_for(rec: hatvp.Declaration) -> tuple[int, hatvp.IndexRow | None]:
        slug, row = index.match(rec)
        if slug:
            pid = people.by_slug.get(slug)
            # Same name, incompatible birth dates: a namesake without an index entry.
            if pid is not None and rec.birth_date and people.birth.get(pid):
                known = people.birth[pid]
                if known != rec.birth_date and not _one_edit_apart(known, rec.birth_date) and row is None:
                    slug = None
            if slug:
                if pid is None:
                    ip = index.people[slug]
                    an = ip.origin("depute")
                    pid = people.create(
                        ip.first.nom,
                        ip.first.prenom,
                        civilite=ip.first.civilite or rec.civilite,
                        birth=rec.birth_date,
                        hatvp_slug=slug,
                        an_id=f"PA{an}" if an and an.isdigit() else None,
                        senat_id=ip.origin("senateur"),
                        photo_url=ip.photo(),
                    )
                    stats["people"] += 1
                else:
                    people.set_birth(pid, rec.birth_date)
                return pid, row
        stats["unmatched_index"] += 1
        pid = people.find(rec.nom, rec.prenom, rec.birth_date)
        if pid is None:
            pid = people.create(rec.nom, rec.prenom, civilite=rec.civilite, birth=rec.birth_date)
            stats["people"] += 1
        return pid, None

    def insert(rec: hatvp.Declaration, status: str) -> None:
        pid, row = person_for(rec)
        cur = conn.execute(
            """INSERT INTO declaration(person_id, uuid, doc_type, is_modification, filed_at, published_at,
                                       mandate_kind, mandate_label, mandate_start, source_file, status,
                                       no_holdings, note)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (pid, rec.uuid, rec.doc_type,
             int(row.doc_code.endswith("m") if row else rec.is_modification), rec.filed_at,
             row.published if row else None, rec.mandate_kind, rec.mandate_label, rec.mandate_start,
             row.source_file if row else None, status, int(rec.no_holdings), rec.note),
        )
        did = cur.lastrowid
        for h in rec.holdings:
            if h.kind == "compte_titres":
                # A brokerage account at a bank: a balance, not a stake in the bank.
                conn.execute(
                    "INSERT INTO holding(declaration_id, kind, raw_name, label, value_eur, comment) VALUES (?,?,?,?,?,?)",
                    (did, h.kind, h.raw_name, h.raw_name or "Compte-titres", h.value_eur, h.comment),
                )
                stats["holdings"] += 1
                continue
            ref = companies.canonicalize(h.raw_name)
            if ref is None:
                continue
            conn.execute(
                """INSERT INTO holding(declaration_id, kind, raw_name, label, company_id, value_eur, shares,
                                       capital_pct, income, comment, sectors)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (did, h.kind, h.raw_name, ref.name, store.get(ref), h.value_eur, h.shares, h.capital_pct,
                 h.income, h.comment, ",".join(ref.sectors) or None),
            )
            stats["holdings"] += 1
        for it in rec.interests:
            ref = companies.canonicalize(it.org) if it.org and it.kind != "mandat" else None
            known = ref is not None and ref.identifiable and companies.known(ref.slug) is not None
            org_key = ref.slug if ref is not None and ref.identifiable else (fold(it.org).replace(" ", "-") or None)
            conn.execute(
                """INSERT INTO interest(declaration_id, kind, org, org_key, company_id, description, person_name,
                                        start_date, end_date, kept, pay_json, pay_max, pay_basis)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (did, it.kind, it.org, org_key, store.get(ref) if known else None, it.description,
                 it.person_name, it.start, it.end, None if it.kept is None else int(it.kept),
                 json.dumps(it.pay) if it.pay else None, max((a for _, a in it.pay), default=None), it.pay_basis),
            )
            stats["interests"] += 1
        conn.executemany(
            "INSERT INTO asset(declaration_id, kind, label, value_eur, detail) VALUES (?, ?, ?, ?, ?)",
            [(did, a.kind, a.label, a.value_eur, a.detail) for a in rec.assets],
        )

    # Declarations the index identifies go first: they create people under a
    # stable slug, so the looser name matches that follow have something to hit.
    deferred: list[tuple[hatvp.Declaration, str]] = []

    def feed(rec: hatvp.Declaration, status: str) -> None:
        if rec.uuid in seen:
            return
        seen.add(rec.uuid)
        stats["declarations" if status == "published" else "archived"] += 1
        if index.match(rec)[0]:
            insert(rec, status)
        else:
            deferred.append((rec, status))

    for rec in hatvp.parse(xml_path):
        feed(rec, "published")
    if legacy_path is not None:
        for rec in legacy.read_declarations(legacy_path, set(seen)):
            feed(rec, "archived")
    for rec, status in deferred:
        insert(rec, status)

    _load_pending(conn, people, index)
    return stats


def _load_pending(conn: sqlite3.Connection, people: People, index: HatvpIndex) -> None:
    """Filings HATVP has received but not published yet, for people we track."""
    rows = []
    for slug, ip in index.people.items():
        pid = people.by_slug.get(slug)
        if pid is None:
            continue
        for r in ip.rows:
            if r.status.startswith("Déclaration déposée") and r.doc_code.startswith("di"):
                rows.append((pid, r.doc_code, r.filed, r.status))
    conn.executemany("INSERT INTO pending_filing(person_id, doc_code, filed, status) VALUES (?, ?, ?, ?)", rows)


def assign_fallback_roles(conn: sqlite3.Connection, index: HatvpIndex) -> None:
    """Give a role to everyone no parliamentary or government roster covers.

    Their latest declaration says what mandate they held; they count as
    active while HATVP still publishes a declaration for it.
    """
    rows = conn.execute(
        """SELECT p.id, p.hatvp_slug,
                  (SELECT d.mandate_kind FROM declaration d WHERE d.person_id = p.id
                   ORDER BY d.filed_at DESC LIMIT 1) AS kind,
                  (SELECT d.mandate_label FROM declaration d WHERE d.person_id = p.id
                   ORDER BY d.filed_at DESC LIMIT 1) AS label,
                  EXISTS (SELECT 1 FROM declaration d WHERE d.person_id = p.id AND d.status = 'published') AS live
           FROM person p WHERE p.chamber IS NULL"""
    ).fetchall()
    updates = []
    for r in rows:
        kind = r["kind"]
        if kind is None:
            continue
        chamber = KIND_CHAMBER.get(kind, "local")
        role = r["label"]
        ip = index.people.get(r["hatvp_slug"]) if r["hatvp_slug"] else None
        if ip:
            same = [x for x in ip.rows if x.mandate_kind == kind and x.role]
            if same:
                role = same[-1].role
        # Former deputies, senators and ministers are not sitting any more.
        active = int(bool(r["live"]) and chamber in ("europe", "local"))
        updates.append((chamber, active, role, r["id"]))
    conn.executemany("UPDATE person SET chamber = ?, active = ?, role = ? WHERE id = ?", updates)


def assign_slugs(conn: sqlite3.Connection) -> None:
    """URL keys that survive a rebuild: the HATVP page slug, else a chamber id, else name + birth year."""
    taken: set[str] = set()
    updates = []
    for r in conn.execute(
        "SELECT id, hatvp_slug, an_id, senat_id, prenom, nom, birth_date FROM person ORDER BY (hatvp_slug IS NULL), id"
    ):
        if r["hatvp_slug"]:
            slug = r["hatvp_slug"]
        else:
            slug = fold(f"{r['nom']} {r['prenom']}").replace(" ", "-") or "personne"
            if r["an_id"]:
                slug += f"-{r['an_id'].lower()}"
            elif r["senat_id"]:
                slug += f"-s{r['senat_id'].lower()}"
            elif r["birth_date"]:
                slug += f"-{r['birth_date'][:4]}"
        base, n = slug, 2
        while slug in taken:
            slug = f"{base}-{n}"
            n += 1
        taken.add(slug)
        updates.append((slug, r["id"]))
    conn.executemany("UPDATE person SET slug = ? WHERE id = ?", updates)


def load_index(path: Path) -> HatvpIndex:
    return HatvpIndex(hatvp.parse_index(path))


def default_legacy_path() -> Path | None:
    return config.LEGACY_DB_PATH if config.LEGACY_DB_PATH.exists() else None
