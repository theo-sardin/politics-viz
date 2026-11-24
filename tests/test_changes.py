from pv.pipeline.changes import Line, classify


def test_a_line_present_on_one_side_only():
    assert classify(None, Line(100, 5000)) == ("entree", "presence")
    assert classify(Line(100, 5000), None) == ("sortie", "presence")
    assert classify(None, None) is None


def test_share_count_is_the_evidence():
    assert classify(Line(170, 26537), Line(214, 49455)) == ("renforcement", "shares")
    assert classify(Line(1949, 23388), Line(1600, 25760)) == ("allegement", "shares")


def test_a_price_move_at_constant_shares_is_not_a_trade():
    assert classify(Line(248, 23248), Line(248, 31000)) is None
    assert classify(Line(100, 5000), Line(104, 5200)) is None  # under the 10 % threshold


def test_a_split_is_not_a_purchase():
    # Ten times the shares for the same money.
    assert classify(Line(100, 50000), Line(1000, 51000)) is None


def test_value_is_a_fallback_and_needs_a_large_move():
    assert classify(Line(None, 10000), Line(None, 12000)) is None
    assert classify(Line(None, 10000), Line(None, 20000)) == ("renforcement", "value")
    assert classify(Line(None, 10000), Line(None, 3000)) == ("allegement", "value")
    assert classify(Line(None, 1000), Line(None, 1900)) is None  # big ratio, trivial amount


def test_a_share_count_equal_to_the_value_is_a_typo():
    # "1227 parts, 1227 €": the declarant typed the value in the wrong box.
    assert classify(Line(1227, 1227), Line(2500, 1300)) is None
