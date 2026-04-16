from parsers.normalize import display_name, _titlecase


def test_display_name_override():
    assert display_name("l-oreal") == "L'Oréal"
    assert display_name("credit-agricole") == "Crédit Agricole"
    assert display_name("total-energies") == "TotalEnergies"


def test_display_name_fallback_raw():
    assert display_name("random-company", "RANDOM COMPANY") == "Random Company"


def test_display_name_fallback_slug():
    assert display_name("some-thing") == "Some Thing"


def test_titlecase_acronyms():
    assert _titlecase("BNP PARIBAS") == "BNP Paribas"  # PARIBAS >4 chars → title-cased
    assert _titlecase("AXA FRANCE") == "AXA France"     # AXA ≤4 chars → stays upper


def test_titlecase_lowercase_words():
    assert _titlecase("Caisse de Dépôts") == "Caisse de Dépôts"
