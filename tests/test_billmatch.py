from pv.pipeline.billmatch import Corpus, bill_pattern, content_tokens, text_refers_to

ENERGY = "Projet de loi relatif à l'accélération de la production d'énergies renouvelables"


def test_content_tokens_drop_legislative_boilerplate():
    assert content_tokens("Proposition de loi visant à renforcer la lutte contre les fraudes") == ["lutte", "fraudes"]


def _corpus(*texts, year=2025):
    return Corpus([(i, year, t) for i, t in enumerate(texts)])


def test_three_title_words_in_a_row_identify_the_bill():
    pat = bill_pattern(1, ENERGY, "2025-01-10", "2025-06-01")
    corpus = _corpus(
        "PJL accélération de la production d'énergies renouvelables : simplifier les procédures",
        "Présenter la stratégie de production du groupe",
        "Soutenir les énergies renouvelables en mer",
    )
    assert corpus.match(pat) == [0]


def test_matches_outside_the_bills_years_are_ignored():
    pat = bill_pattern(1, ENERGY, "2025-01-10", "2025-06-01")
    text = "PJL accélération de la production d'énergies renouvelables"
    assert _corpus(text, year=2021).match(pat) == []
    assert _corpus(text, year=2024).match(pat) == [0]  # the year before the first vote counts


def test_budget_bills_match_on_acronym_and_year():
    pat = bill_pattern(7, "Projet de loi de financement de la sécurité sociale pour 2026", "2025-10-20", "2025-12-09")
    corpus = _corpus(
        "PLFSS 2026 : défendre le remboursement des dispositifs",
        "PLFSS 2025 : même sujet, autre année",
        "Loi de financement de la sécurité sociale pour 2026 - article 19",
    )
    assert corpus.match(pat) == [0, 2]


def test_a_year_in_the_bills_name_must_be_the_year_in_the_text():
    pat = bill_pattern(3, "Projet de loi relatif à l'organisation des jeux Olympiques et Paralympiques de 2030",
                       "2025-05-01", "2026-01-10")
    assert _corpus("Jeux Olympiques et Paralympiques de Paris 2024 : hydrogène").match(pat) == []
    assert _corpus("Loi sur l'organisation des jeux Olympiques et Paralympiques de 2030").match(pat) == [0]


def test_a_fixed_two_word_expression_needs_a_legislative_cue():
    pat = bill_pattern(4, "Proposition de loi relative au droit à l'aide à mourir", "2025-05-01", "2025-06-01")
    texts = ["PPL aide à mourir : garantir la clause de conscience"] + ["Débat sur l'aide à mourir"] * 6
    hits = _corpus(*texts).match(pat)
    assert hits == [0]  # the six others say « aide à mourir » without naming a bill


def test_single_text_check_used_for_recusals():
    pat = bill_pattern(1, ENERGY, "2025-01-10", "2025-06-01")
    assert text_refers_to("Projet de loi relatif à l'accélération de la production d'énergies renouvelables", pat)
    assert not text_refers_to("Article 19 du PLFSS", pat)
