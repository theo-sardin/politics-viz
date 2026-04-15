from parsers.normalize import company_slug, person_key, strip_accents


def test_strip_accents():
    assert strip_accents("Crédit Agricole") == "Credit Agricole"
    assert strip_accents("Société générale") == "Societe generale"
    assert strip_accents("") == ""


def test_company_slug_basic():
    assert company_slug("Crédit Agricole SA") == "credit-agricole"
    assert company_slug("EDF") == "edf"
    assert company_slug("TotalEnergies") == "totalenergies"


def test_company_slug_drops_suffixes():
    assert company_slug("Martin SARL") == "martin"
    assert company_slug("Dupont SCI") == "dupont"
    assert company_slug("Ma Boite SAS") == "ma-boite"


def test_company_slug_keeps_sa_inside_word():
    # 'Saint' must not be gobbled by the SA suffix rule
    assert company_slug("Saint-Gobain") == "saint-gobain"


def test_company_slug_empty_or_none():
    assert company_slug(None) == ""
    assert company_slug("") == ""
    assert company_slug("   ") == ""


def test_person_key_accent_and_case_insensitive():
    assert person_key("DUPONT", "Jean") == person_key("Dupont", "jean")
    assert person_key("Héloïse", "Léa") == "heloise|lea"


def test_person_key_trims_whitespace():
    assert person_key("  Dupont  ", " Jean ") == "dupont|jean"
