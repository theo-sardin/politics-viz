"""Load who sits where, the recusal register, and the roll-call record.

Bills are not first-class in the roll-call archive: each vote only carries a
sentence such as « l'amendement n° 12 de M. X à l'article 3 du projet de loi
relatif à … (première lecture) ». The bill is recovered from that sentence
and, where the archive provides one, tied to its legislative dossier.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

from pv import companies, config, sectors
from pv.pipeline.people import People
from pv.sources import assemblee, senat
from pv.text import fold

log = logging.getLogger(__name__)

# Group colours follow each political family's usual hue, stepped so that
# neighbours in the hemicycle stay distinguishable (checked with the dataviz
# palette validator, light and dark, adjacent pairs). Colour is never the only
# cue: groups are also contiguous wedges, labelled and listed in a legend.
#
# code → (light, dark, left-to-right order)
AN_GROUPS: dict[str, tuple[str, str, int]] = {
    "LFI-NFP": ("#e34948", "#e66767", 1),
    "GDR": ("#9c3a5c", "#ad4f93", 2),
    "ECOS": ("#008300", "#008300", 3),
    "SOC": ("#e87ba4", "#d55181", 4),
    "LIOT": ("#6250d6", "#9085e9", 5),
    "EPR": ("#eda100", "#c98500", 6),
    "HOR": ("#1baf7a", "#199e70", 7),
    "DEM": ("#eb6834", "#d95926", 8),
    "DR": ("#2a78d6", "#3987e5", 9),
    "UDR": ("#00917f", "#0fa392", 10),
    "RN": ("#2f4fa8", "#5b78d6", 11),
    "NI": ("#898781", "#898781", 12),
}

# raw label in the Sénat export → (code, label, light, dark, order)
SENAT_GROUPS: dict[str, tuple[str, str, str, str, int]] = {
    "CRCE-K": ("CRCE-K", "Communiste Républicain Citoyen et Écologiste – Kanaky", "#9c3a5c", "#ad4f93", 1),
    "GEST": ("GEST", "Écologiste – Solidarité et Territoires", "#008300", "#008300", 2),
    "SER": ("SER", "Socialiste, Écologiste et Républicain", "#e87ba4", "#d55181", 3),
    "RDSE": ("RDSE", "Rassemblement Démocratique et Social Européen", "#6250d6", "#9085e9", 4),
    "RDPI": ("RDPI", "Rassemblement des démocrates, progressistes et indépendants", "#eda100", "#c98500", 5),
    "Les Indépendants": ("LIRT", "Les Indépendants – République et Territoires", "#1baf7a", "#199e70", 6),
    "UC": ("UC", "Union Centriste", "#eb6834", "#d95926", 7),
    "UCI": ("UCI", "Union Centriste (UCI)", "#eb6834", "#d95926", 7),
    "Les Républicains": ("LR", "Les Républicains", "#2a78d6", "#3987e5", 8),
    "NI": ("NI", "Non inscrits", "#898781", "#898781", 12),
}
# A group the tables above do not know yet: seated right of centre, in the one
# hue the chamber's known groups leave free there.
_FALLBACK = ("#00917f", "#0fa392", 9)

_MEMBERSHIP_KINDS = {"GP", "COMPER", "COMNL", "CNPE", "CNPS", "MISINFO", "MISINFOCOM", "GE", "DELEG", "OFFPAR"}


def _ensure_group(conn, cache: dict, chamber: str, code: str, label: str, color: str, dark: str, order: int,
                  organe: str | None = None) -> int:
    slug = f"{chamber}-{fold(code).replace(' ', '-')}"
    if slug in cache:
        return cache[slug]
    cur = conn.execute(
        "INSERT INTO groupe(slug, chamber, code, label, color, color_dark, ord, organe_uid) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (slug, chamber, code, label, color, dark, order, organe),
    )
    cache[slug] = cur.lastrowid
    return cur.lastrowid


def load_assemblee_roles(conn: sqlite3.Connection, people: People, acteurs_path: Path, index=None) -> dict[str, int]:
    """Attach Assemblée / government roles and return stats."""
    stats = {"deputes": 0, "deputes_sans_hatvp": 0, "gouvernement": 0, "acteurs_lies": 0, "deports": 0}
    leg = config.CURRENT_LEGISLATURE

    organes: dict[str, assemblee.Organe] = {}
    for o in assemblee.read_organes(acteurs_path):
        organes[o.uid] = o
    conn.executemany(
        "INSERT OR REPLACE INTO organe(uid, kind, label, short, legislature) VALUES (?, ?, ?, ?, ?)",
        [(o.uid, o.kind, o.label, o.short, o.legislature) for o in organes.values()],
    )

    groups: dict[str, int] = {}
    group_by_organe: dict[str, int] = {}
    for o in organes.values():
        if o.kind == "GP" and o.legislature == leg and not o.ended:
            code = (o.short or o.label).upper()
            color, dark, order = AN_GROUPS.get(code, _FALLBACK)
            group_by_organe[o.uid] = _ensure_group(conn, groups, "an", code, o.label, color, dark, order, o.uid)

    acteurs: dict[str, assemblee.Acteur] = {}
    memberships = []
    for a in assemblee.read_acteurs(acteurs_path):
        acteurs[a.uid] = a
        seat = next((m for m in a.mandats if m.kind == "ASSEMBLEE" and m.legislature == leg and m.end is None), None)
        # A mandate left open in a government that has itself ended is stale data.
        gov = next(
            (m for m in a.mandats if m.kind == "GOUVERNEMENT" and m.end is None
             and m.organe_uid in organes and not organes[m.organe_uid].ended),
            None,
        )

        pid = people.by_an.get(a.uid)
        if pid is None and a.hatvp_slug:
            pid = people.by_slug.get(a.hatvp_slug)
        if pid is None:
            pid = people.find(a.nom, a.prenom, a.birth_date)
        if pid is None:
            if seat is None and gov is None:
                continue
            pid = people.create(a.nom, a.prenom, civilite=a.civilite, birth=a.birth_date, an_id=a.uid)
            if seat is not None:
                stats["deputes_sans_hatvp"] += 1
        people.set_ids(pid, an_id=a.uid)
        slug = people.slug_of.get(pid) or a.hatvp_slug or (index.by_origin.get(a.uid[2:]) if index is not None else None)
        ip = index.people.get(slug) if index is not None and slug else None
        people.set_slug(pid, slug, ip.photo() if ip else None)
        people.set_birth(pid, a.birth_date)
        stats["acteurs_lies"] += 1
        if a.profession:
            conn.execute("UPDATE person SET profession = COALESCE(profession, ?) WHERE id = ?", (a.profession, pid))

        if seat is not None:
            fem = (a.civilite or "").startswith("Mme")
            role = "Députée" if fem else "Député"
            if seat.departement:
                role += f" · {seat.departement}"
                if seat.circo:
                    role += f", {seat.circo}{'re' if seat.circo == '1' else 'e'} circ."
            gp = next((m for m in a.mandats if m.kind == "GP" and m.end is None and m.organe_uid in group_by_organe), None)
            com = next((m for m in a.mandats if m.kind == "COMPER" and m.end is None), None)
            conn.execute(
                """UPDATE person SET chamber = 'an', active = 1, role = ?, departement = ?, seat = ?,
                                     group_id = ?, committee = ?
                   WHERE id = ?""",
                (role, seat.departement, seat.seat, group_by_organe.get(gp.organe_uid) if gp else None,
                 organes[com.organe_uid].label if com and com.organe_uid in organes else None, pid),
            )
            stats["deputes"] += 1
        if gov is not None:
            titles = [r.role for r in ip.rows if r.mandate_kind == "gouvernement" and r.role] if ip else []
            conn.execute(
                "UPDATE person SET chamber = 'gouv', active = 1, role = ? WHERE id = ?",
                (titles[-1] if titles else "Membre du Gouvernement", pid),
            )
            stats["gouvernement"] += 1

        for m in a.mandats:
            if m.kind in _MEMBERSHIP_KINDS and (m.end is None or m.legislature == leg) and m.organe_uid in organes:
                memberships.append((pid, m.organe_uid, m.role, m.start, m.end))
    conn.executemany(
        "INSERT INTO membership(person_id, organe_uid, role, start_date, end_date) VALUES (?, ?, ?, ?, ?)",
        memberships,
    )

    for d in assemblee.read_deports(acteurs_path):
        pid = people.by_an.get(d.acteur_uid)
        if pid is None:
            a = acteurs.get(d.acteur_uid)
            if a is None:
                continue
            pid = people.create(a.nom, a.prenom, civilite=a.civilite, birth=a.birth_date, an_id=a.uid)
            conn.execute("UPDATE person SET chamber = 'an', active = 0, role = 'Ancien député' WHERE id = ?", (pid,))
        conn.execute(
            """INSERT OR IGNORE INTO deport(uid, person_id, legislature, date, scope, target_kind, target, reason)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (d.uid, pid, d.legislature, d.date, d.scope, d.target_kind, d.target, d.reason),
        )
        stats["deports"] += 1
    return stats


def load_senat_roles(conn: sqlite3.Connection, people: People, senateurs_path: Path, index=None) -> dict[str, int]:
    stats = {"senateurs": 0, "senateurs_sans_hatvp": 0}
    groups: dict[str, int] = {r["slug"]: r["id"] for r in conn.execute("SELECT id, slug FROM groupe")}
    for s in senat.read_senateurs(senateurs_path):
        pid = people.by_senat.get(s.matricule)
        if pid is None:
            pid = people.find(s.nom, s.prenom, s.birth_date)
        if pid is None:
            pid = people.create(s.nom, s.prenom, civilite=s.civilite, birth=s.birth_date, senat_id=s.matricule)
            stats["senateurs_sans_hatvp"] += 1
        people.set_ids(pid, senat_id=s.matricule)
        slug = index.by_origin.get(s.matricule) if index is not None else None
        ip = index.people.get(slug) if slug else None
        people.set_slug(pid, slug, ip.photo() if ip else None)
        people.set_birth(pid, s.birth_date)
        gid = None
        if s.group:
            code, label, color, dark, order = SENAT_GROUPS.get(s.group, (s.group, s.group, *_FALLBACK))
            gid = _ensure_group(conn, groups, "senat", code, label, color, dark, order)
        fem = (s.civilite or "").startswith("Mme")
        role = ("Sénatrice" if fem else "Sénateur") + (f" · {s.departement}" if s.departement else "")
        # A sitting minister keeps the government role.
        conn.execute(
            """UPDATE person SET chamber = 'senat', active = 1, role = ?, departement = ?, group_id = ?,
                                 committee = ?, profession = COALESCE(profession, ?)
               WHERE id = ? AND COALESCE(chamber, '') <> 'gouv'""",
            (role, s.departement, gid, s.committee, s.profession, pid),
        )
        stats["senateurs"] += 1
    return stats


# ---------------------------------------------------------------------------
# Roll calls and bills
# ---------------------------------------------------------------------------

# Company names that are also ordinary words: never matched inside a bill title.
_AMBIGUOUS_NAMES = {
    "orange", "casino", "legrand", "apple", "rubis", "accor", "elis", "spie", "puma", "seb", "bic",
    "shell", "meta", "amazon", "ing", "sap", "eni", "roche", "merck", "linde", "suez", "safran",
    "vinci", "atos", "rallye", "lcl", "scor", "adp", "gtt", "tesla", "michelin", "renault", "canal",
}


def companies_named_in(title: str) -> list[companies.Known]:
    """Registry companies a bill title names outright (« nationalisation d'ArcelorMittal »)."""
    t = fold(title)
    out = []
    for k in companies.REGISTRY:
        if k.slug in _AMBIGUOUS_NAMES or fold(k.name) in _AMBIGUOUS_NAMES:
            continue
        name = fold(k.name)
        if (len(name) >= 6 and re.search(rf"\b{re.escape(name)}\b", t)) or (
            "^" not in k.pattern.pattern and k.pattern.search(t)
        ):
            out.append(k)
    return out


def bill_slug(legislature: int, key: str, title: str) -> str:
    """'l17-ppl-droit-a-l-aide-a-mourir-3fa9c2': readable, and stable while the title is."""
    import hashlib

    from pv import labels

    words = fold(labels.short_bill(title, 60)).replace(" ", "-")[:48].strip("-")
    digest = hashlib.sha1(f"{legislature}|{key}".encode()).hexdigest()[:6]
    return f"l{legislature}-{words}-{digest}"


def _merge_typo_twins(conn: sqlite3.Connection) -> None:
    """Fold « ssimplification du droit de l'urbanisme » into its correctly spelt twin."""
    from difflib import SequenceMatcher

    rows = conn.execute(
        """SELECT b.id, b.legislature, b.key, COUNT(s.id) n FROM bill b
           LEFT JOIN scrutin s ON s.bill_id = b.id GROUP BY b.id ORDER BY n DESC"""
    ).fetchall()
    kept: dict[int, list[tuple[int, str]]] = defaultdict(list)
    for r in rows:
        twin = None
        if r["n"] <= 5:
            for bid, key in kept[r["legislature"]]:
                if abs(len(key) - len(r["key"])) <= 3:
                    m = SequenceMatcher(None, key, r["key"])
                    if m.quick_ratio() >= 0.97 and m.ratio() >= 0.97:
                        twin = bid
                        break
        if twin is None:
            kept[r["legislature"]].append((r["id"], r["key"]))
        else:
            conn.execute("UPDATE scrutin SET bill_id = ? WHERE bill_id = ?", (twin, r["id"]))
            conn.execute("DELETE FROM bill WHERE id = ?", (r["id"],))


def load_votes(
    conn: sqlite3.Connection,
    people: People,
    scrutin_paths: dict[int, Path],
    dossier_paths: dict[int, Path],
    company_ids,
) -> dict[str, int]:
    stats = {"scrutins": 0, "votes": 0, "bills": 0, "bills_with_dossier": 0, "roles": 0}
    bill_ids: dict[tuple[int, str], int] = {}
    bill_dossiers: dict[int, Counter] = defaultdict(Counter)
    bill_titles: dict[int, Counter] = defaultdict(Counter)

    for leg, path in sorted(scrutin_paths.items()):
        for sc in assemblee.read_scrutins(path):
            parsed = assemblee.parse_title(sc.title)
            bill_id = None
            if parsed.bill_key:
                bkey = (leg, parsed.bill_key)
                bill_id = bill_ids.get(bkey)
                if bill_id is None:
                    cur = conn.execute(
                        "INSERT INTO bill(legislature, key, title, search_key) VALUES (?, ?, ?, ?)",
                        (leg, parsed.bill_key, parsed.bill_title, parsed.bill_key),
                    )
                    bill_id = bill_ids[bkey] = cur.lastrowid
                bill_titles[bill_id][parsed.bill_title] += 1
                if sc.dossier_uid:
                    bill_dossiers[bill_id][sc.dossier_uid] += 1
            cur = conn.execute(
                """INSERT INTO scrutin(uid, legislature, numero, date, title, kind, outcome, n_pour, n_contre,
                                       n_abstention, bill_id, stage)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (sc.uid, leg, sc.numero, sc.date, sc.title, parsed.kind, sc.outcome, sc.n_pour, sc.n_contre,
                 sc.n_abstention, bill_id, parsed.stage),
            )
            sid = cur.lastrowid
            rows = {}
            for ref, pos, dissent in sc.votes:
                pid = people.by_an.get(ref)
                if pid is not None:
                    rows[pid] = (sid, pid, pos, dissent)
            conn.executemany("INSERT INTO vote(scrutin_id, person_id, position, dissent) VALUES (?, ?, ?, ?)", rows.values())
            stats["scrutins"] += 1
            stats["votes"] += len(rows)

    # Variants of one title ("… des membres du conseil de Paris", "… du Conseil de
    # Paris") point to the same dossier: fold them into a single bill.
    by_dossier: dict[tuple[int, str], list[int]] = defaultdict(list)
    leg_of = {bid: leg for (leg, _), bid in bill_ids.items()}
    for bid, counts in bill_dossiers.items():
        by_dossier[(leg_of[bid], counts.most_common(1)[0][0])].append(bid)
    for (_, dossier_uid), bids in by_dossier.items():
        keep = max(bids, key=lambda b: sum(bill_titles[b].values()))
        for other in bids:
            if other != keep:
                conn.execute("UPDATE scrutin SET bill_id = ? WHERE bill_id = ?", (keep, other))
                conn.execute("DELETE FROM bill WHERE id = ?", (other,))
        conn.execute("UPDATE bill SET dossier_uid = ? WHERE id = ?", (dossier_uid, keep))

    _merge_typo_twins(conn)
    conn.execute(
        """UPDATE bill SET
             n_scrutins = (SELECT COUNT(*) FROM scrutin s WHERE s.bill_id = bill.id),
             first_vote = (SELECT MIN(date) FROM scrutin s WHERE s.bill_id = bill.id),
             last_vote  = (SELECT MAX(date) FROM scrutin s WHERE s.bill_id = bill.id)"""
    )

    bills = conn.execute("SELECT id, legislature, key, title, dossier_uid FROM bill").fetchall()
    by_uid = {(b["legislature"], b["dossier_uid"]): b["id"] for b in bills if b["dossier_uid"]}
    by_key = {(b["legislature"], b["key"]): b["id"] for b in bills}
    defense_committees = {
        r["uid"] for r in conn.execute("SELECT uid, label FROM organe WHERE kind = 'COMPER'")
        if "defense" in fold(r["label"])
    }
    committee_of: dict[int, str | None] = {}
    for leg, path in sorted(dossier_paths.items()):
        if path is None:
            continue
        for d in assemblee.read_dossiers(path):
            bid = by_uid.get((leg, d.uid)) or by_key.get((leg, fold(d.title)))
            if bid is None:
                continue
            url = f"{config.AN_BASE}/dyn/{leg}/dossiers/{d.url_path}" if d.url_path else None
            conn.execute(
                "UPDATE bill SET dossier_uid = ?, url = ?, procedure = ?, committee_uid = ? WHERE id = ?",
                (d.uid, url, d.procedure, d.committee_uid, bid),
            )
            committee_of[bid] = d.committee_uid
            stats["bills_with_dossier"] += 1
            for role, refs in (("rapporteur", d.rapporteurs), ("auteur", d.auteurs)):
                for ref in refs:
                    pid = people.by_an.get(ref)
                    if pid is not None:
                        conn.execute(
                            "INSERT OR IGNORE INTO bill_role(bill_id, person_id, role) VALUES (?, ?, ?)",
                            (bid, pid, role),
                        )
                        stats["roles"] += 1

    for b in bills:
        cls = sectors.classify_bill(b["title"])
        secs = list(cls.sectors)
        if not secs and not cls.transversal and committee_of.get(b["id"]) in defense_committees:
            secs = ["defense"]
        conn.execute("UPDATE bill SET transversal = ?, budget = ? WHERE id = ?", (int(cls.transversal), int(cls.budget), b["id"]))
        conn.executemany("INSERT OR IGNORE INTO bill_sector(bill_id, sector) VALUES (?, ?)", [(b["id"], s) for s in secs])
        for k in companies_named_in(b["title"]):
            cid = company_ids(k)
            if cid is not None:
                conn.execute("INSERT OR IGNORE INTO bill_company(bill_id, company_id) VALUES (?, ?)", (b["id"], cid))

    conn.execute("DELETE FROM bill WHERE NOT EXISTS (SELECT 1 FROM scrutin s WHERE s.bill_id = bill.id)")
    conn.executemany(
        "UPDATE bill SET slug = ? WHERE id = ?",
        [(bill_slug(b["legislature"], b["key"], b["title"]), b["id"])
         for b in conn.execute("SELECT id, legislature, key, title FROM bill").fetchall()],
    )
    conn.execute(
        """INSERT INTO person_bill
           SELECT v.person_id, s.bill_id, COUNT(*),
                  SUM(v.position = 1), SUM(v.position = 2), SUM(v.position = 3), SUM(v.dissent),
                  MIN(s.date), MAX(s.date), NULL
           FROM vote v JOIN scrutin s ON s.id = v.scrutin_id
           WHERE s.bill_id IS NOT NULL AND v.position <> 0
           GROUP BY v.person_id, s.bill_id"""
    )
    conn.execute(
        """UPDATE person_bill SET final_position = (
               SELECT v.position FROM vote v JOIN scrutin s ON s.id = v.scrutin_id
               WHERE v.person_id = person_bill.person_id AND s.bill_id = person_bill.bill_id
                 AND s.kind = 'ensemble' AND v.position <> 0
               ORDER BY s.date DESC, s.numero DESC LIMIT 1)"""
    )
    stats["bills"] = conn.execute("SELECT COUNT(*) FROM bill").fetchone()[0]
    return stats
