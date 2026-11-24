"""The fixture sources, run through every stage. See conftest for the cast."""

import json

from pv.web import queries


def _person(conn, nom):
    return conn.execute("SELECT * FROM person WHERE nom = ?", (nom,)).fetchone()


def _signals(conn, nom, kind=None):
    sql = """SELECT s.* FROM signal s JOIN signal_person sp ON sp.signal_id = s.id
             JOIN person p ON p.id = sp.person_id WHERE p.nom = ?"""
    rows = conn.execute(sql + (" AND s.kind = ?" if kind else ""), (nom, kind) if kind else (nom,)).fetchall()
    return [dict(r, evidence=json.loads(r["evidence"])) for r in rows]


# -- identity ------------------------------------------------------------


def test_each_person_exists_once_with_the_ids_of_every_source(conn):
    assert conn.execute("SELECT COUNT(*) FROM person").fetchone()[0] == 4
    alice = _person(conn, "Martin")
    assert (alice["hatvp_slug"], alice["an_id"], alice["slug"]) == ("martin-alice-1", "PA1001", "martin-alice-1")
    assert alice["photo_url"] == "https://example.org/1001.jpg"
    assert (alice["chamber"], alice["active"]) == ("an", 1)
    assert alice["role"] == "Députée · Rhône (69), 1re circ."
    assert alice["committee"] == "Commission des affaires sociales"
    assert alice["profession"] == "Médecin"


def test_groups_roles_and_fallbacks(conn):
    groups = {r["slug"]: r for r in conn.execute("SELECT * FROM groupe")}
    assert set(groups) == {"an-epr", "senat-lr"}
    assert _person(conn, "Petit")["group_id"] == groups["an-epr"]["id"]
    senator = _person(conn, "Leroy")
    assert (senator["chamber"], senator["senat_id"], senator["role"]) == ("senat", "19999X", "Sénateur · Ain")
    assert senator["slug"] == "leroy-denis-s19999x"  # no HATVP page: falls back to the chamber id
    local = _person(conn, "Durand")
    assert (local["chamber"], local["active"]) == ("local", 1)


# -- declarations --------------------------------------------------------


def test_declarations_carry_their_source_file_and_amendment_flag(conn):
    rows = conn.execute(
        """SELECT d.uuid, d.source_file, d.is_modification, d.status FROM declaration d
           JOIN person p ON p.id = d.person_id WHERE p.nom = 'Martin' ORDER BY d.filed_at"""
    ).fetchall()
    assert [tuple(r) for r in rows] == [
        ("a-1", "martin-alice-dia1", 0, "published"),
        # The XML says "not an amendment"; the index, which is right, says it is.
        ("a-2", "martin-alice-diam2", 1, "published"),
    ]


def test_holdings_are_canonicalised_and_placeholders_dropped(conn):
    rows = conn.execute(
        """SELECT h.label, c.slug, h.value_eur, h.shares FROM holding h
           JOIN declaration d ON d.id = h.declaration_id LEFT JOIN company c ON c.id = h.company_id
           WHERE d.uuid = 'a-2' ORDER BY h.value_eur DESC"""
    ).fetchall()
    assert [tuple(r) for r in rows] == [("TotalEnergies", "totalenergies", 33000, 500), ("Sanofi", "sanofi", 15000, 150)]
    sci = conn.execute("SELECT label, company_id, sectors, capital_pct FROM holding WHERE raw_name LIKE 'SCI%'").fetchone()
    assert tuple(sci) == ("SCI (nom non publié)", None, "immobilier", 50.0)


def test_pending_filings_are_kept(conn):
    row = conn.execute(
        "SELECT f.doc_code, f.filed FROM pending_filing f JOIN person p ON p.id = f.person_id WHERE p.nom = 'Petit'"
    ).fetchone()
    assert tuple(row) == ("diam", "2026-01-15")


def test_summary_columns(conn):
    alice = _person(conn, "Martin")
    assert (alice["portfolio_eur"], alice["n_holdings"]) == (48000, 2)
    total = conn.execute("SELECT n_holders, total_eur FROM company WHERE slug = 'totalenergies'").fetchone()
    assert tuple(total) == (1, 33000)


# -- movements -----------------------------------------------------------


def test_movements_between_two_declarations(conn):
    rows = conn.execute(
        """SELECT c.slug, pc.kind, pc.basis, pc.shares_before, pc.shares_after, pc.window_start, pc.window_end
           FROM position_change pc JOIN company c ON c.id = pc.company_id ORDER BY c.slug"""
    ).fetchall()
    assert [tuple(r) for r in rows] == [
        ("sanofi", "entree", "presence", None, 150, "2024-09-01", "2025-03-01"),
        ("totalenergies", "renforcement", "shares", 300, 500, "2024-09-01", "2025-03-01"),
    ]


# -- parliament ----------------------------------------------------------


def test_bills_are_recovered_from_vote_titles(conn):
    bills = {r["title"]: r for r in conn.execute("SELECT * FROM bill")}
    assert len(bills) == 2  # the censure motion is not a bill
    energy = next(b for t, b in bills.items() if "renouvelables" in t)
    assert (energy["n_scrutins"], energy["dossier_uid"], energy["transversal"]) == (2, "DLR1", 0)
    assert energy["url"].endswith("/dyn/17/dossiers/energies_renouvelables")
    assert energy["slug"].startswith("l17-pjl-")
    sectors = {r["sector"] for r in conn.execute("SELECT sector FROM bill_sector WHERE bill_id = ?", (energy["id"],))}
    assert sectors == {"energie"}
    plfss = next(b for t, b in bills.items() if "sécurité sociale" in t)
    assert plfss["budget"] == 1


def test_votes_roles_and_recusals(conn):
    alice = _person(conn, "Martin")
    pb = conn.execute(
        """SELECT pb.* FROM person_bill pb JOIN bill b ON b.id = pb.bill_id
           WHERE pb.person_id = ? AND b.title LIKE '%renouvelables%'""",
        (alice["id"],),
    ).fetchone()
    assert (pb["n_votes"], pb["n_pour"], pb["final_position"]) == (2, 2, 1)
    assert conn.execute("SELECT role FROM bill_role WHERE person_id = ?", (alice["id"],)).fetchone()[0] == "rapporteur"
    deport = conn.execute("SELECT d.reason, b.title FROM deport d LEFT JOIN bill b ON b.id = d.bill_id").fetchone()
    assert deport["reason"] == "Conjoint salarié du secteur."
    assert "renouvelables" in deport["title"]  # the notice is tied back to the bill it names


# -- lobbying ------------------------------------------------------------


def test_lobbying_is_tied_to_the_company_and_the_bill(conn):
    org = conn.execute(
        """SELECT o.name, c.slug, o.spend_min, o.n_actions, o.n_actions_parl FROM lobby_org o
           JOIN company c ON c.id = o.company_id"""
    ).fetchone()
    assert tuple(org) == ("TOTALENERGIES SE", "totalenergies", 1_000_000, 2, 1)
    linked = conn.execute(
        """SELECT a.subject, b.title FROM bill_lobby bl JOIN lobby_action a ON a.id = bl.action_id
           JOIN bill b ON b.id = bl.bill_id"""
    ).fetchall()
    assert len(linked) == 1 and "renouvelables" in linked[0]["title"]


# -- signals -------------------------------------------------------------


def test_trade_then_vote_is_the_headline_signal(conn):
    (s,) = [x for x in _signals(conn, "Martin") if x["bill_id"] and "TotalEnergies" in x["title"]]
    assert s["kind"] == "mouvement_vote" and s["level"] == "fort"
    ev = s["evidence"]
    assert ev["links"][0]["strength"] == "lobby" and ev["links"][0]["value"] == 33000
    assert ev["roles"] == ["rapporteur"]
    assert ev["votes"] == {"n": 2, "pour": 2, "contre": 0, "abst": 0, "dissent": 0,
                           "first": "2025-04-09", "last": "2025-04-10", "final": "pour"}
    assert ev["trades"][0]["kind"] == "renforcement" and ev["trades"][0]["basis"] == "shares"
    assert "simplifier les procédures" in ev["lobby"][0]["subject"]
    assert ev["decl"]["file"] == "martin-alice-diam2"  # the declaration in force on the day of the vote
    assert "rapporteur du texte" in s["summary"]


def test_budget_bills_do_not_flag_small_sector_holdings(conn):
    assert not [x for x in _signals(conn, "Martin") if x["bill_id"] and "PLFSS" in x["title"]]


def test_spouse_employer_and_recusal_signals(conn):
    (family,) = _signals(conn, "Martin", "lien_famille")
    assert family["title"] == "Conjoint·e · Société Générale" and family["level"] == "faible"
    (deport,) = _signals(conn, "Petit", "deport")
    assert deport["level"] == "info" and deport["bill_id"] is not None


def test_a_redacted_sci_never_becomes_a_market_signal(conn):
    assert not [x for x in _signals(conn, "Petit") if x["kind"] != "deport"]


# -- read side -----------------------------------------------------------


def test_hemicycle_seats_do_not_overlap(conn):
    h = queries.hemicycle(conn, "an")
    assert len(h["seats"]) == 2 and h["groups"][0]["code"] == "EPR"


def test_hemicycle_layout_fills_the_band_at_any_size():
    pos, radius = queries._hemicycle_positions(577)
    assert len(pos) == 577 and len(set(pos)) == 577
    assert pos[0][0] < 0 < pos[-1][0]  # seated left to right
    rows = {round((x * x + y * y) ** 0.5, 3) for x, y in pos}
    assert 9 <= len(rows) <= 14  # several concentric arcs, not one crowded line
    assert radius > 0.012  # seats big enough to see and to hover
    nearest = min(((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5
                  for i, (ax, ay) in enumerate(pos) for bx, by in pos[i + 1:i + 40])
    assert nearest > 2 * radius  # and they do not overlap
    for n in (1, 2, 5, 40, 348):
        small, r = queries._hemicycle_positions(n)
        assert len(small) == n and r > 0


def test_person_page_data(conn):
    p = queries.person(conn, "martin-alice-1")
    assert [d["filed_at"][:10] for d in p["series"]] == ["2024-09-01", "2025-03-01"]
    total = next(l for l in p["lines"] if l["slug"] == "totalenergies")
    cells = [total["cells"][d["id"]] for d in p["series"]]
    assert [(c["value"], c["change"]) for c in cells] == [(20000, None), (33000, "renforcement")]
    assert p["interests"]["conjoint"][0]["company_name"] == "Société Générale"
    assert p["vote_stats"]["n"] == 4
    assert queries.person(conn, "nobody") is None


def test_search_spans_people_companies_and_bills(conn):
    assert [r["label"] for r in queries.search(conn, "martin")] == ["Alice Martin"]
    assert queries.search(conn, "totalen")[0]["url"] == "/societe/totalenergies"
    assert queries.search(conn, "energies renouvelables")[0]["type"] == "Texte"
    assert queries.search(conn, "x") == []
