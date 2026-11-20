"""Model-name matching. Each case is a label that was wrong in the registry
before 2026-11-20 (counts are stored rows measured that day), an owner ruling
of that day, or something that must keep working."""
import pytest

from carhunt.aliases import (ALIASES, GENERIC_KEYS, _ALIAS_EXACT, make_model_for,
                             model_key_for, model_keys_in, skeleton)


def first(text):
    return (model_keys_in(text) or [None])[0]


@pytest.mark.parametrize("text, key", [
    # --- owner rulings, 2026-11-20 -----------------------------------------
    ("مضلع ابيض", "hyundai accent"), ("المضلع", "hyundai accent"),
    ("دفار ميتسوبيشي كانتر", "light truck (دفار)"), ("دفار", "light truck (دفار)"),
    ("دفاررقم الشاسي", "light truck (دفار)"),
    ("امجاد بيضاء", "minivan (امجاد)"), ("امجاد داماس", "minivan (امجاد)"),
    ("جياد", "giad"), ("اكسنت جياد", "hyundai accent"),
    ("هونداي جياد ابيض اكسنت", "hyundai accent"), ("بي واي دي ابيض جياد", "byd"),
    ("حرجلي", None), ("اكسنت حرجلي", "hyundai accent"), ("كامري حرجلي", "toyota camry"),
    ("كريز سلفر", "hyundai grace"), ("كريس", "hyundai grace"), ("قريس ابيض", "hyundai grace"),
    # --- a model named beside a nickname or a body type wins ---------------
    ("اكسنت مضلع ابيض", "hyundai accent"), ("افانتي سلفر مضلعة", "hyundai avante"),
    ("سوناتا مضلع رصاصي", "hyundai sonata"), ("مضلع جياد", "hyundai accent"),
    ("بوكسي تايوتا مضلع", "pickup (بوكس)"), ("هايلوكس مضلع", "toyota hilux"),
    ("بوكس هايلوكس دبل", "toyota hilux"), ("شريحة هايس", "toyota hiace"),
    ("حافلة شريحة", "van/minibus (شريحة)"), ("صالون لاندكروزر", "toyota land cruiser"),
    ("صالون تايوتا", "sedan (صالون)"), ("لانسر قرش", "mitsubishi lancer"),
    ("قرش ابيض", "mitsubishi lancer"), ("توسان قرش", "hyundai tucson"),
    # --- an alias is a word, not a substring -------------------------------
    ("سيريون", None), ("كيا اوبتيما مكنه فل جربوكس فل", "kia optima"),
    ("هوندا اكورد مكنه فل جربوكس فل", None),             # gearbox is not a pickup
    ("صنعت في مصنع شركة ميتسوبيشي", None),               # "made in" is not a Sunny
    ("from dense roster (bushara khair recovery listing)", None),
    ("ركشه سوداء بأصفر", "rickshaw"),                    # "in yellow" is not a bus
    ("تيريوس", "daihatsu terios"), ("Daihatsu Terios", "daihatsu terios"),
    ("كريسيدا", "toyota cressida"),                      # not كريس + letters
    # --- colours and ordinary words are not models -------------------------
    ("رينو دستر فيراني", "renault duster"), ("جاك فيراني", None), ("فيرنا فيراني", "hyundai accent"),
    ("سيارات", None), ("كاميرا", None), ("كرين", None), ("ليست", None), ("سانتا", None),
    ("تكيف بارد", None), ("روفر", None), ("كروز", None),
    ("كراسي", None), ("تروس", None), ("بترول", None), ("قروش", None),
    ("سراتو", "kia cerato"), ("داستر", "renault duster"),
    # --- second round of owner rulings, 2026-11-20 --------------------------
    ("باجوري", "mitsubishi pajero"), ("برايد", "kia pride"), ("كيا برايد ابيض", "kia pride"),
    ("كراتو", "hyundai creta"), ("كريتو", "hyundai creta"),
    ("جكس", "toyota land cruiser"), ("جي اكس ار ابيض", "toyota land cruiser"), ("GXR", "toyota land cruiser"),
    ("دغري", "rickshaw"), ("دفار دغري", "light truck (دفار)"), ("دغري دفار", "light truck (دفار)"),
    # --- spellings the table did not know (rows that had no model) ---------
    ("مورنق ابيض", "kia picanto"), ("مورنغ", "kia picanto"), ("مورنيق", "kia picanto"),
    ("اتوس احمر", "hyundai atos"), ("جلي", "geely"), ("كروزر", "toyota land cruiser"),
    ("برادو", "toyota land cruiser"), ("اي تين", "hyundai i10"),
    ("اي توينتي", "hyundai i20"), ("ايتونتي", "hyundai i20"), ("سوني 060749", "nissan sunny"),
    ("استايركس اسعاف", "hyundai starex"), ("اسبورتاج", "kia sportage"), ("بورتر", "hyundai porter"),
    ("باجيرو", "mitsubishi pajero"), ("نوبيرا", "daewoo nubira"), ("اتش ون", "hyundai h1"),
    ("اکسنت", "hyundai accent"),                         # Persian kaf
    # --- still found: prefixes, digits, glued text, a change of script -----
    ("الكورولا", "toyota corolla"), ("والكوريلا", "toyota corolla"),
    ("كورلا2007", "toyota corolla"), ("كريلا", "toyota corolla"), ("النتراء", "hyundai avante"),
    ("i10ابيض", "hyundai i10"), ("عربه i20رقم الشاسي", "hyundai i20"),
    ("دقو وراء", "rickshaw"), ("Kia Rio 2012", "kia rio"), ("ريو ابيض", "kia rio"),
])
def test_first_key(text, key):
    assert first(text) == key


def test_make_and_model_fields_still_come_first():
    assert model_key_for("Toyota", "Corolla", "اكسنت") == "toyota corolla"
    assert model_key_for("Suzuki", "Celerio", None) is None      # not a Rio


@pytest.mark.parametrize("key, pair", [
    ("toyota corolla", ("Toyota", "Corolla")), ("hyundai santa fe", ("Hyundai", "Santa Fe")),
    ("hyundai i10", ("Hyundai", "i10")), ("toyota rav4", ("Toyota", "RAV4")),
    ("byd", ("BYD", None)), ("giad", ("GIAD", None)),
    # a body type is never a make: 41,101 rows said make "Pickup"
    ("pickup (بوكس)", (None, "pickup (بوكس)")), ("rickshaw", (None, "rickshaw")),
    ("light truck (دفار)", (None, "light truck (دفار)")), (None, (None, None)),
])
def test_make_model_for(key, pair):
    assert make_model_for(key) == pair


def test_every_key_round_trips_through_its_own_fields():
    """What the parsers store must read back as the same key at ingest."""
    for key in ALIASES:
        make, model = make_model_for(key)
        assert model_key_for(make, model, None) == key, key


def test_no_two_keys_share_a_skeleton():
    """The i20 alias used to collide with the i10's: first in the table won."""
    # Known and accepted: برايد (Kia Pride) reduces to برد, the Prado's
    # skeleton. The Prado keeps it (first in the table); Pride matches as the
    # exact word only, and بارد / بريد / براد are in SKELETON_STOP.
    accepted = {"برايد"}
    seen = {}
    for alias, key in _ALIAS_EXACT.items():
        s = skeleton(alias)
        if len(s) >= 3 and alias not in accepted:
            assert seen.setdefault(s, key) == key, (alias, key, seen[s])


def test_generic_keys_exist():
    assert all(k in ALIASES for k in GENERIC_KEYS)
