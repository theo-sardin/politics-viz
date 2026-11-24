import json

from pv.pipeline.people import names_compatible
from pv.sources import agora, hatvp, senat


def test_parse_spend_bands():
    assert agora.parse_spend("> = 1 250 000 euros et < 1 500 000 euros") == (1_250_000, 1_500_000)
    assert agora.parse_spend("< 10 000 euros") == (0, 10_000)
    assert agora.parse_spend("> = 10 000 000 euros") == (10_000_000, None)
    assert agora.parse_spend(None) == (None, None)


def test_publications_are_streamed_one_object_at_a_time(tmp_path):
    path = tmp_path / "agora.json"
    orgs = [{"identifiantNational": str(i), "denomination": f"ORG {i}", "pad": "x" * 5000} for i in range(40)]
    path.write_text(json.dumps({"meta": 1, "publications": orgs}), encoding="utf-8")
    assert [p["identifiantNational"] for p in agora.iter_publications(path)] == [str(i) for i in range(40)]


def test_lobby_org_actions(sources):
    orgs = list(agora.read_orgs(sources["agora"]))
    total = orgs[0]
    assert total.name == "TOTALENERGIES SE"
    assert total.years[0].spend_min == 1_000_000 and total.years[0].n_actions == 2
    assert total.actions[0].targets_parl and total.actions[0].on_law
    assert not total.actions[1].targets_parl


def test_hatvp_declaration_sections(sources):
    decls = {d.uuid: d for d in hatvp.parse(sources["hatvp_declarations"])}
    a2 = decls["a-2"]
    assert (a2.nom, a2.prenom, a2.birth_date) == ("MARTIN", "Alice", "1980-01-01")
    assert a2.doc_type == "DIA" and a2.mandate_kind == "depute"
    assert a2.filed_at == "2025-03-01T09:30:00"
    assert [(h.raw_name, h.value_eur, h.shares) for h in a2.holdings][:2] == [
        ("PEA TOTAL", 33000, 500), ("Sanofi", 15000, 150)]
    spouse = [i for i in a2.interests if i.kind == "conjoint"]
    assert spouse[0].org == "Société Générale" and spouse[0].description == "Analyste"
    # A collaborator with no other employer is still listed, without an organisation.
    staff = [i for i in decls["a-1"].interests if i.kind == "collaborateur"]
    assert staff[0].person_name == "DUPUIS Marc" and staff[0].org is None
    assert decls["b-1"].holdings[0].raw_name == "SCI [Données non publiées]"


def test_hatvp_index(sources):
    rows = hatvp.parse_index(sources["hatvp_index"])
    assert rows[0].hatvp_slug == "martin-alice-1"
    assert rows[0].source_file == "martin-alice-dia1"
    assert rows[0].origin_id == "1001" and rows[1].doc_code == "diam"
    assert rows[3].status.startswith("Déclaration déposée") and rows[3].source_file is None


def test_only_sitting_senators_are_read(sources):
    rows = senat.read_senateurs(sources["senat_senateurs"])
    assert [(s.nom, s.group, s.committee) for s in rows] == [("Leroy", "Les Républicains", "Commission des finances")]


def test_names_compatible_needs_the_same_first_name():
    assert names_compatible("K/BIDI", "Émeline", "KBIDI", "Emeline")
    assert names_compatible("Thiébault", "Céline", "THIÉBAULT-MARTINEZ", "Céline")
    assert not names_compatible("Martin", "Alice", "Martin", "Alain")
    assert not names_compatible("Petit", "Bruno", "Durand", "Bruno")
