from pathlib import Path

from parsers.hatvp import parse

FIXTURE = Path(__file__).parent / "fixtures" / "hatvp_sample.xml"


def _records():
    return list(parse(str(FIXTURE)))


def test_parses_two_declarations():
    recs = _records()
    assert [r.uuid for r in recs] == ["decl-0001", "decl-0002"]


def test_declarant_fields():
    r1, r2 = _records()
    assert (r1.nom, r1.prenom, r1.date_naissance) == ("DUPONT", "Jean", "1970-12-26")
    assert (r2.nom, r2.prenom, r2.date_naissance) == ("MARTIN", "Héloïse", "1985-03-05")


def test_mandate_and_organe():
    r1, r2 = _records()
    assert (r1.mandate_cat, r1.mandate_type, r1.organe_label) == (
        "PAR", "Député", "Assemblée nationale"
    )
    assert (r2.mandate_cat, r2.mandate_type, r2.organe_label) == (
        "LOC", "Maire ou adjoint municipal", "Mairie de Dupontville"
    )


def test_date_depot_iso():
    r1, _ = _records()
    assert r1.date_depot == "2024-12-20T20:09:38"


def test_holdings_of_first_declaration():
    r1, _ = _records()
    kinds = [h.kind for h in r1.holdings]
    assert kinds == [
        "participation_financiere",  # Crédit Agricole
        "participation_financiere",  # EDF
        # '0' / 'neant' placeholder dropped
        "valeur_bourse",  # BNP Paribas
        "participation_dirigeant",  # ADAC65
    ]


def test_participation_financiere_fields():
    r1, _ = _records()
    ca = r1.holdings[0]
    assert ca.company_name == "Crédit Agricole SA"
    assert ca.company_slug == "credit-agricole"
    assert ca.evaluation_eur == 3190  # space as thousands separator handled
    assert ca.capital_pct == 0.01
    assert ca.nb_parts == 270

    edf = r1.holdings[1]
    assert edf.commentaire == "bloc familial"
    assert edf.evaluation_eur == 1658


def test_valeur_bourse_uses_etablissement():
    r1, _ = _records()
    v = r1.holdings[2]
    assert v.kind == "valeur_bourse"
    assert v.company_name == "BNP Paribas"
    assert v.evaluation_eur == 2000
    assert v.commentaire == "PEA"


def test_second_declaration_has_no_holdings():
    _, r2 = _records()
    assert r2.holdings == []
