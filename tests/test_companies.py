import pytest

from pv.companies import REGISTRY, canonicalize, prettify


@pytest.mark.parametrize(
    "raw, slug",
    [
        ("PEA TOTAL", "totalenergies"),
        ("Total énergies SE", "totalenergies"),
        ("TOTALENERGIE", "totalenergies"),
        ("L'OREAL", "l-oreal"),
        ("Caisse locale du Crédit Agricole Dieppe", "credit-agricole"),
        ("CA", "credit-agricole"),
        ("Dassault Systèmes", "dassault-systemes"),
        ("DASSAULT AVIATION", "dassault-aviation"),
        ("Française des jeux", "fdj"),
        ("Aéroports de Paris", "adp"),
        ("Cap Gemini", "capgemini"),
        ("ROCHE HOLDING BJ", "roche"),
    ],
)
def test_spelling_variants_reach_one_registry_company(raw, slug):
    assert canonicalize(raw).slug == slug


def test_the_bank_holding_the_account_is_not_the_holding():
    assert canonicalize("AMAZON CREDIT MUTUEL COMPTE TITRE").slug == "amazon"
    assert canonicalize("APPLE COMPTE TITRE CREDIT MUTUEL").slug == "apple"


@pytest.mark.parametrize("raw", ["néant", "Aucune", "RAS", "0", "je n'ai pas de participation", None, ""])
def test_placeholders_are_not_holdings(raw):
    assert canonicalize(raw) is None


def test_redacted_names_are_kept_but_never_identifiable():
    ref = canonicalize("SCI [Données non publiées]")
    assert not ref.identifiable
    assert ref.legal_form == "sci"
    assert ref.sectors == ("immobilier",)
    assert canonicalize("[Données non publiées]").identifiable is False


def test_generic_and_portfolio_lines_are_not_identifiable():
    assert not canonicalize("Parts sociales PEA").identifiable
    assert not canonicalize("Groupement Forestier").identifiable
    assert not canonicalize("12 lignes de PEA AXA, Sanofi, société générale, total").identifiable


def test_a_one_word_name_keeps_its_legal_form_to_avoid_namesakes():
    assert canonicalize("Legrand").slug == "legrand"  # the listed company
    assert canonicalize("SCI LEGRAND").slug == "sci-legrand"
    assert canonicalize("SCI Orange Bleue").slug == "orange-bleue"
    assert canonicalize("Orange").slug == "orange"


def test_funds_are_their_own_kind():
    ref = canonicalize("AMUNDI STRAT.ACT.EURO C FCP 3D")
    assert ref.kind == "fund"
    assert canonicalize("Amundi").kind == "listed"
    assert canonicalize("SCPI Primovie").sectors == ("immobilier",)


def test_unknown_companies_get_keyword_sectors():
    assert canonicalize("Pharmacie Bataille").sectors == ("sante",)
    assert "agriculture" in canonicalize("GFA du Château").sectors


def test_registry_slugs_are_unique_and_sectors_exist():
    from pv.sectors import SECTORS

    slugs = [k.slug for k in REGISTRY]
    assert len(slugs) == len(set(slugs))
    for k in REGISTRY:
        assert k.sectors, k.slug
        assert set(k.sectors) <= set(SECTORS), k.slug


def test_prettify():
    assert prettify("LES AMIS DE LA TERRE") == "Les Amis de la Terre"
    assert prettify("SNCF") == "SNCF"
    assert prettify("air liquide") == "Air Liquide"
    assert prettify("Mixed Case SA") == "Mixed Case SA"
