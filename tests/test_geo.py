"""Free-text location -> state P-code. Every string is synthetic or a place name.

The rule under test: never guess. A string that names two states, a region
shared by several states, or no known place must say so.
"""
import pytest

from carhunt import geo


@pytest.mark.parametrize("text, pcode", [
    ("الخرطوم", "SD01"),
    ("Khartoum, Sudan", "SD01"),
    ("ولاية الخرطوم", "SD01"),
    ("امدرمان الثورة", "SD01"),
    ("أم درمان", "SD01"),
    ("حي العامرية بامدرمان", "SD01"),
    ("Khartoum - Omdurman - Bahri", "SD01"),
    ("بحري الحلفايا", "SD01"),
    ("الكلاكلة اللفة", "SD01"),
    ("جبرة مربع 19", "SD01"),
    ("شرق النيل الهريواب", "SD01"),
    ("شمال كردفان", "SD13"),
    ("North Kordofan (شمال كردفان)", "SD13"),
    ("كردفان -- جبرة الشيخ", "SD13"),
    ("Kordofan, Jabra Al-Sheikh", "SD13"),
    ("محلية بارا", "SD13"),
    ("غرب كردفان", "SD18"),
    ("كادقلي", "SD07"),
    ("الجزيرة", "SD15"),
    ("Al-Jazirah", "SD15"),
    ("ود مدني", "SD15"),
    ("شرق الجزيرة قرية", "SD15"),
    ("النيل الابيض ـ ربك", "SD09"),
    ("كوستي", "SD09"),
    ("سنجة", "SD14"),
    ("عطبرة", "SD16"),
    ("نهر النيل العبيدية", "SD16"),
    ("الولاية الشمالية، مروي", "SD17"),
    ("Kassala", "SD11"),
    ("بورسودان", "SD10"),
    ("الضعين", "SD05"),
])
def test_named_place_maps_to_its_state(text, pcode):
    assert geo.locate(text).kind == pcode


def test_two_states_in_one_string_is_multiple_not_the_first():
    loc = geo.locate("كردفان جبرة الشيخ الخرطوم وامدرمان")
    assert loc.kind == geo.MULTIPLE and loc.pcodes == ("SD01", "SD13")
    assert geo.locate("تمبول ومدني والخرطوم").kind == geo.MULTIPLE


def test_region_shared_by_several_states_is_not_assigned_to_one():
    loc = geo.locate("Kordofan")
    assert loc.kind == geo.REGION_ONLY and loc.region == "Kordofan" and loc.pcodes == ()
    assert geo.locate("داخل كردفان").kind == geo.REGION_ONLY
    assert geo.locate("دارفور").region == "Darfur"


def test_a_region_beside_a_state_outside_it_is_two_places():
    assert geo.locate("كردفان والخرطوم").kind == geo.MULTIPLE


def test_longest_phrase_wins():
    """جبرة is a Khartoum neighbourhood; جبرة الشيخ is a North Kordofan locality.
    الابيض is El Obeid; النيل الابيض is White Nile state."""
    assert geo.locate("جبرة الشيخ").kind == "SD13"
    assert geo.locate("النيل الأبيض").kind == "SD09"
    assert geo.locate("الأبيض").kind == "SD13"


def test_whole_words_only():
    assert geo.locate("بربري").kind == geo.UNMAPPED          # not بربر (River Nile)
    assert geo.locate("امدرمان الريف الشمالي").kind == "SD01"  # not الشمالية (Northern)
    assert geo.locate("بيت المال شمال").kind == geo.UNMAPPED


def test_an_island_called_al_jazirah_is_not_the_state():
    assert geo.locate("امدرمان الجزيرة اسلانج").kind == "SD01"


@pytest.mark.parametrize("text", ["داخل منزل", "inside a house", "مستودع جديد", "unknown", "", None])
def test_no_known_place_is_unmapped(text):
    assert geo.locate(text).kind == geo.UNMAPPED


def test_country_alone_is_not_a_location():
    assert geo.locate("Sudan").kind == geo.COUNTRY_ONLY
    assert geo.locate("السودان").kind == geo.COUNTRY_ONLY


def test_every_pcode_in_the_gazetteer_is_a_real_state():
    assert set(geo._GAZETTEER) <= set(geo.ADMIN1)
    assert len(geo.ADMIN1) == 19
