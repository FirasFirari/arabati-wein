"""The deterministic كشف list parser.

Design rule: identifiers come from the parser, judgement stays with the LLM.
0 fabrications is structural -- it only ever emits digits present in the line.
Every case is synthetic. The last three were defects found by the
2026-11-04 audit (recorded as strict xfail until fixed the same week).
"""
import pytest

from carhunt import kashf


def chassis(text):
    return [v["chassis"] for v in kashf.parse(text)["vehicles"]]


def test_multi_vehicle_line_reads_every_number():
    """Reading only the first number per line cost ~350 of 365 misses."""
    got = chassis("سانتافي 421376 . i10 احمر 790978. فيرنا 024486 ابيض")
    assert got == ["421376", "790978", "024486"]


def test_plate_shapes_are_load_bearing():
    rev = kashf.parse("هايلوكس 1خ63922 شاسي 774512")["vehicles"]
    lead = kashf.parse("خ3 28444 اكسنت 559013")["vehicles"]
    assert [v["chassis"] for v in rev] == ["774512"] and rev[0]["plate"]
    assert [v["chassis"] for v in lead] == ["559013"] and lead[0]["plate"]


def test_a_lone_plate_is_filed_as_the_rows_chassis():
    """Known behaviour the watch rule relies on (a plate serial also matches a
    كشف row whose chassis is that serial, exactly). Not confident."""
    rows = kashf.parse("كورولا ابيض 52817خ4")["vehicles"]
    assert [v["chassis"] for v in rows] == ["52817"]
    assert rows[0]["confident"] is False


@pytest.mark.parametrize("line", ["بوكسي هايلوكس 2021ابيض", "حصر رقم 877",
                                  "للتواصل 0912345678", "للتواصل 0912 345 678",
                                  "للتواصل +249912345678"])
def test_years_list_serials_and_phones_are_not_chassis(line):
    assert chassis(line) == []


def test_nine_digit_chassis_is_not_mistaken_for_a_phone():
    """The phone filter must be narrower than what it protects: exactly
    0 + 9|1 + 8 digits. A wider one ate the real chassis 982000920."""
    assert chassis("كورولا 982000920 ابيض") == ["982000920"]


def test_alphanumeric_vin():
    assert chassis("لاندكروزر JTMHU01J3L4100780") == ["JTMHU01J3L4100780"]


def test_one_car_listed_twice_is_one_row():
    assert chassis("كورولا 663201\nكورولا 663201") == ["663201"]


def test_parser_never_emits_digits_absent_from_the_text():
    text = "حصر\nكورولا 663201 ابيض\nاكسنت 559013\nهايلوكس 1خ63922 شاسي 774512"
    digits = "".join(c for c in text if c.isdigit())
    for v in kashf.parse(text)["vehicles"]:
        assert "".join(c for c in v["chassis"] if c.isdigit()) in digits


def test_year_range_is_not_a_chassis():
    assert "2015-2016" not in chassis("كورولا 2015-2016 ابيض 331245")


def test_dash_joined_chassis_are_two_rows():
    assert chassis("اكسنت 313267-680869") == ["313267", "680869"]


def test_list_index_is_not_part_of_the_chassis():
    assert chassis("1-292063 كورولا") == ["292063"]
