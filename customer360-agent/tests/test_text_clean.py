"""Model-written text must not contain em dashes (antislop R-02). One helper
does it for every agent."""

from use_cases.text_clean import strip_em_dashes

EM = chr(0x2014)


def test_spaced_em_dash_becomes_a_comma():
    assert strip_em_dashes(f"Beta led {EM} with 2,500 sent.") == "Beta led, with 2,500 sent."


def test_unspaced_em_dash_becomes_a_comma_and_space():
    assert strip_em_dashes(f"Open Settings{EM}then Security.") == "Open Settings, then Security."


def test_surrounding_whitespace_is_trimmed():
    assert strip_em_dashes("  hello \n") == "hello"


def test_text_without_dashes_is_unchanged():
    assert strip_em_dashes("Plain text, with a hyphen-ated word.") == "Plain text, with a hyphen-ated word."


def test_en_dash_ranges_are_left_alone():
    assert strip_em_dashes("Open 9–17 daily") == "Open 9–17 daily"
