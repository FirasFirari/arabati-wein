"""Identifier normalisation: the shared vocabulary of every matcher."""
import pytest

from carhunt.normalize import (digits_only, has_prose, is_plate_shaped, norm_chassis,
                               norm_plate, plate_variants, strip_prose)


@pytest.mark.parametrize("raw, want", [
    ("٠١٢٣٤٥٦٧٨٩", "0123456789"),        # Arabic-Indic
    ("۰۱۲۳۴۵۶۷۸۹", "0123456789"),        # Extended (Persian) Arabic-Indic
    ("شاسي ٧٦١-٤٣٣٧", "7614337"),
    ("", ""),
    (None, ""),
])
def test_digits_only(raw, want):
    assert digits_only(raw) == want


@pytest.mark.parametrize("raw, want", [
    ("jtd br32e 72-001", "JTDBR32E72001"),
    ("٧٦١٤٣٣٧", "7614337"),
    ("شاسي: AB-12/34", "AB1234"),
    (None, ""),
])
def test_norm_chassis(raw, want):
    assert norm_chassis(raw) == want


def test_plate_serial_survives_region_marker():
    """A Sudanese plate is SERIAL + region marker. Gluing the region digit on
    ("123451") matches nothing anyone types; the serial is the identity."""
    assert norm_plate("28461 / خ7") == "28461"
    assert "28461" in plate_variants("28461 خ7")


def test_glued_region_plate_splits():
    # region letter + region digit glued to a 5+ digit serial
    assert norm_plate("خ٧٢٨٤٦١") == "28461"


@pytest.mark.parametrize("value, want", [
    ("28461 خ 7", True), ("لوحة 28461", True), ("70352خ6", True),
    ("JTTEST01J3L410078", False), ("كورولا 2015", False), ("613572", False),
])
def test_is_plate_shaped(value, want):
    assert is_plate_shaped(value) is want


def test_prose_in_identifier_field():
    assert has_prose("no match for 7654321 here")
    assert not has_prose("JTDBR32E72001")
    assert strip_prose("JTDBR32E72001") == "JTDBR32E72001"


def test_vin_letters_that_cannot_exist_fold_only_at_17_characters():
    assert norm_chassis("MROAW12GX00002178") == "MR0AW12GX00002178"
    assert norm_chassis("mro aw12gx 0000 2178") == "MR0AW12GX00002178"
    assert norm_chassis("MRO12345") == "MRO12345"          # a fragment: left alone
