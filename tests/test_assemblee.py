import pytest

from pv.sources.assemblee import parse_title


def test_amendment_on_a_bill():
    r = parse_title("l'amendement n° 2067 de Mme Blin après l'article 19 du projet de loi de "
                    "simplification de la vie économique (première lecture).")
    assert r.kind == "amendement"
    assert r.bill_title == "Projet de loi de simplification de la vie économique"
    assert r.stage == "première lecture"


def test_final_vote_and_senate_wording():
    r = parse_title("l'ensemble de la proposition de loi, adoptée par le Sénat, relative à l'instauration "
                    "d'un nombre minimum de soignants par patient hospitalisé (première lecture).")
    assert r.kind == "ensemble"
    assert r.bill_title == ("Proposition de loi relative à l'instauration d'un nombre minimum de soignants "
                            "par patient hospitalisé")


@pytest.mark.parametrize(
    "title",
    [
        "l'article premier de la proposition de loi visant à encourager l'exercice du mandat d'élu local "
        "(première lecture)(seconde délibération).",
        "l'article premier de la proposition de loi visant à encourager l'exercice du mandat d'élu local "
        "( première lecture).",
    ],
)
def test_stage_markers_never_leak_into_the_bill_name(title):
    assert parse_title(title).bill_title == "Proposition de loi visant à encourager l'exercice du mandat d'élu local"


def test_budget_mission_suffix_is_dropped():
    r = parse_title("les crédits de la mission Défense du projet de loi de finances pour 2024 "
                    "(première lecture) - Mission Défense")
    assert r.bill_title == "Projet de loi de finances pour 2024"


def test_same_bill_same_key_whatever_the_apostrophe():
    a = parse_title("l'article 2 de la proposition de loi relative au droit à l'aide à mourir (première lecture).")
    b = parse_title("l'article 3 de la proposition de loi relative au droit à l’aide à mourir (deuxième lecture).")
    assert a.bill_key == b.bill_key


def test_votes_that_are_not_about_a_bill():
    r = parse_title("la motion de censure, déposée en application de l'article 49, alinéa 2, par M. X et 65 députés.")
    assert r.kind == "motion" and r.bill_title is None
