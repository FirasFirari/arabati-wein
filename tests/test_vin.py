"""VIN rules: flag and prioritise, never rewrite. Synthetic/textbook VINs only."""
import pytest

from carhunt import vin

VALID = "1M8GDM9AXKP042788"     # the textbook ISO 3779 example (check char X)


def test_textbook_check_digit():
    assert vin.check_digit(VALID) == "X"
    assert vin.check_ok(VALID) is True
    assert vin.check_ok("1M8GDM9A1KP042788") is False


def test_letter_at_position_9_means_no_check_digit():
    v = "KMHTEST0AGU100284"          # 'A' at position 9
    assert vin.has_check_position(v) is False
    assert vin.check_ok(v) is None
    assert vin.flags(v) == []        # never called wrong


@pytest.mark.parametrize("raw, key", [
    ("MRO" + "AW12GX0000217".ljust(14, "8"), "MR0" + "AW12GX0000217".ljust(14, "8")),
    ("JTDBR32E7O0012345", "JTDBR32E700012345"),
    ("ABC12345", "ABC12345"),        # not 17 chars: untouched
])
def test_search_key_folds_only_illegal_letters(raw, key):
    assert vin.search_key(raw) == key


def test_strong_family_failure_is_flagged_and_first_in_line():
    cd = vin.check_digit("JTD" + "BR32E" + "0" + "72001234")
    good = "JTDBR32E" + cd + "72001234"
    bad = "JTDBR32E" + ("1" if cd != "1" else "2") + "72001234"
    assert vin.check_ok(good) is True and vin.family(good) == "strong"
    assert vin.flags(bad) == ["check digit fails: likely misread"]
    assert vin.second_read_risk(f"VIN {bad}") == 0
    assert vin.second_read_risk(f"VIN {good}") == 4


def test_no_check_family_failure_is_not_flagged():
    v = "WDB" + "2030461A" + "123456"   # Mercedes: no check digit outside NA
    assert vin.family(v) == "none"
    assert vin.flags(v) == []


def test_illegal_letters_are_flagged_and_prioritised():
    v = "MRO" + "AW12GX" + "00002178"
    assert "contains I/O/Q" in vin.flags(v)[0]
    assert vin.second_read_risk(v) <= 1


def test_all_digits_is_not_a_vin():
    assert vin.flags("12345678901234567") == ["17 digits and no letters: probably not a VIN"]
