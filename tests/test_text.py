from pv import labels
from pv.text import clean, fold, is_redacted, name_key, parse_date, parse_float, parse_int, titlecase_name


def test_fold_strips_accents_case_and_punctuation():
    assert fold("L'Oréal — S.A.") == "l oreal s a"
    assert fold("Société d’Économie") == "societe d economie"
    assert fold(None) == ""


def test_name_key_is_order_and_accent_insensitive():
    assert name_key("THIÉBAULT-MARTINEZ", "Céline") == name_key("Thiebault Martinez", "celine")


def test_clean_drops_the_redaction_marker():
    assert clean("SCI  [Données non publiées]  ") == "SCI"
    assert clean("[Données non publiées]") is None
    assert is_redacted("SCI [Données non publiées]")


def test_parse_int_handles_french_number_formats():
    assert parse_int("10 312") == 10312
    assert parse_int("1 234,50 €") == 1234
    assert parse_int("12.5") == 12
    assert parse_int("néant") is None


def test_parse_float_and_dates():
    assert parse_float("0,01") == 0.01
    assert parse_date("05/04/1980") == "1980-04-05"
    assert parse_date("20/12/2024 20:09:38") == "2024-12-20T20:09:38"
    assert parse_date("07/2021") == "2021-07"
    assert parse_date("n'importe quoi") is None


def test_titlecase_name_only_touches_all_caps_or_all_lower():
    assert titlecase_name("DEJEAN DE LA BATIE") == "Dejean de la Batie"
    assert titlecase_name("JEAN-RENÉ") == "Jean-René"
    assert titlecase_name("Le Feur") == "Le Feur"


def test_fmt_eur_is_compact():
    assert labels.fmt_eur(533) == "533 €"
    assert labels.fmt_eur(23248) == "23 k€"
    assert labels.fmt_eur(1_590_000) == "1,59 M€"
    assert labels.fmt_eur(186_000_000) == "186 M€"
    assert labels.fmt_eur(None) == "—"


def test_fmt_date_in_french():
    assert labels.fmt_date("2025-03-23") == "23 mars 2025"
    assert labels.fmt_date("2025-03-01T09:00:00") == "1er mars 2025"
    assert labels.fmt_date("2025-03-23", short=True) == "23 mars"
    assert labels.fmt_date(None) == "—"


def test_short_bill_names_budget_bills_by_acronym():
    assert labels.short_bill("Projet de loi de financement de la sécurité sociale pour 2026") == "PLFSS 2026"
    assert labels.short_bill("Projet de loi de finances pour 2025") == "PLF 2025"


def test_short_bill_drops_boilerplate_and_stated_purpose():
    title = (
        "Proposition de loi visant à la nationalisation d'ArcelorMittal France "
        "afin de préserver la souveraineté industrielle de la France"
    )
    assert labels.short_bill(title) == "PPL nationalisation d'ArcelorMittal France"
    assert labels.short_bill("Proposition de loi relative au droit à l'aide à mourir") == "PPL droit à l'aide à mourir"
