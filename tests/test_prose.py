"""The prose reader: every case here is a defect the benchmark or the audit
actually found, with invented numbers in place of the real ones."""
import pytest

from carhunt import prose
from carhunt.gates import grounded, source_runs


def ids(text):
    r = prose.extract(text)
    return r, [(v.get("chassis"), v.get("plate")) for v in r["vehicles"]]


def chassis(text):
    return [c for c, _ in ids(text)[1] if c]


# --- labelled chassis ---------------------------------------------------------
def test_labelled_chassis_with_alef_maqsura():
    r, got = ids("موجود بوكس 2010 الشاسى 0031234 من منهوبات الحرب\n0912345678")
    assert r["settled"] and got == [("0031234", None)]
    v = r["vehicles"][0]
    assert v["year"] == 2010 and v["rule"] == "label"
    assert v["contact"] == "0912345678" and v["status"] == "seeking_owner"


def test_invisible_mark_between_label_and_number():
    assert chassis("موجوده للتواصل 0912345678.  رقم الشاسي‎20701234567") == ["20701234567"]


def test_hashtag_glued_to_label():
    assert chassis("#شاسي188123") == ["188123"]


def test_label_on_one_line_number_on_the_next():
    assert chassis("كوريلا داخل منزل\nرقم الشاسي\n0006111") == ["0006111"]


def test_one_word_between_label_and_number():
    assert chassis("رقم شاسي الداف 142111 من مفقودات الحرب") == ["142111"]


def test_engine_number_is_an_identifier_engine_size_is_not():
    assert chassis("ركشه بياجو رقم الشاسي 055111 رقم المكنة 3031111") == ["055111", "3031111"]
    r, got = ids("توسان للبيع مكينه 2400cc العداد 97000")
    assert r["settled"] and not any(c for c, _ in got)


def test_year_before_the_label_word_is_not_a_chassis():
    r, got = ids("مفقود بوكسي 2014 شاسي للتواصل 0912345678")
    assert not any(c for c, _ in got)


def test_year_shaped_labelled_value_is_kept_with_the_doubt():
    r = prose.extract("هايلوكس غمارتين شاسي 1965")
    assert r["vehicles"][0]["chassis"] == "1965"
    assert "model year" in r["vehicles"][0]["low_confidence"]


# --- plates -------------------------------------------------------------------
def test_labelled_plate_with_region():
    r, got = ids("موجود رينو رقم الشاسي 0727111 رقم اللوحة خ4/ 72111 من مفقودات الحرب")
    assert got == [("0727111", "خ4/ 72111")]


def test_plate_does_not_swallow_the_next_word():
    _, got = ids("مفقود بكسي رقم شاسي 1356111 رقم لوحة 34111 من امدرمان")
    assert got == [("1356111", "34111")]


def test_plate_glued_to_the_following_word():
    _, got = ids("موجود استاركس رقم اللوحة 60111ربنا يجمع بي صاحبها للتواصل 0912345678")
    assert got == [(None, "60111")]


def test_two_letter_region_marker_after_the_serial():
    _, got = ids("كلك 2005 تحمل اللوحة بالرقم 17111ش ك شاسي 290111")
    assert got == [("290111", "17111ش ك")]


def test_tel_abbreviation_is_not_a_region_marker():
    _, got = ids("مفقود بوكسي شاسي 179111 لوحه ج2 8411  ت0912345678")
    assert got == [("179111", "ج2 8411")]


def test_square_metres_are_not_a_plate():
    r, got = ids("مساحة الأرض: 600م2 في الخرطوم")
    assert got == []


def test_price_after_the_word_lawha_is_not_a_plate():
    _, got = ids("مكيف نسمة ريموت + لوحة = 1200 الف")
    assert got == []


# --- numbers that are not identifiers ----------------------------------------
@pytest.mark.parametrize("text", [
    "للتواصل 0912345678",
    "واتساب 249912345678",
    "اتصال 0123-456-789",
    "وأتساب 09123456789",
    "https://whatsapp.com/channel/0029VaE7L5T11ulPOTfEGA1s",
    "رابط https://wa.me/201055730111",
    "السعر 3500000 قابل للتفاوض",
    "حصرنا اكثر من 3000 سيارة",
    "تاريخ 12/08/2024 الساعة 6:49",
])
def test_not_identifiers(text):
    r, got = ids(text)
    assert got == [] and r["settled"]


def test_two_phones_after_one_label_are_not_glued():
    r = prose.extract("بوكس تايوتا رقم الشاسي 390111 اتواصل معاي في 0912345678.  0123456789")
    assert r["vehicles"][0]["contact"] == "0912345678"
    assert prose.extract("شاسي 390111 اتصال 0123-456-789")["vehicles"][0]["contact"] == "0123456789"


def test_for_sale_ad_without_identifier_gets_a_plain_row():
    r = prose.extract("سوناتا 2008 للبيع\nقير اتوماتيك\nالسعر 53\nواتساب 0912345678")
    assert r["settled"] and len(r["vehicles"]) == 1
    v = r["vehicles"][0]
    assert v["rule"] == "ad" and v["model_key"] == "hyundai sonata"
    assert v["year"] == 2008 and v["status"] == "for_sale"


# --- the reader says "I do not know" -----------------------------------------
@pytest.mark.parametrize("text", [
    "عندنا البكسي دا ربنا يجمع بي سيدو.. 233111",
    "موجود\nاكسن جياد 9004111\nربنا يجمع لسيدا",
    "May be an image of text that says 'امجاد شاسي 066111 کوريلا شاسي 8886888'",
    "BrilliantPeapod38591 كتب تعليق",
])
def test_unexplained_number_leaves_the_post_for_a_model(text):
    assert not prose.extract(text)["settled"]


def test_caption_numbers_are_never_emitted():
    r = prose.extract("موجوده من مفقودات الحرب\n"
                      "May be an image of text that says 'شاسي 8886888'")
    assert r["vehicles"] == [] and not r["settled"]


# --- lists written model-first ------------------------------------------------
LIST = "مجموعه رقم (586)\nواتس اب 0912345678\n\nدبدوب 334111\nسحليه 953111\nكريتا 150111\n"


def test_model_first_list():
    r, got = ids(LIST)
    assert r["settled"] and r["mode"] == "list"
    assert [c for c, _ in got] == ["334111", "953111", "150111"]
    assert all(v["rule"] == "list" and v["status"] == "found" for v in r["vehicles"])


def test_number_written_twice_is_one_vehicle():
    assert chassis(LIST + "جيلي دهبي 000411 - 000411\nاكسنت 001511--001511\n")[-2:] == ["000411", "001511"]


def test_placeholder_zeros_are_dropped():
    assert "00000000000" not in chassis(LIST + "00000000000 0000000\n")


def test_labelled_plate_on_a_list_line_that_looks_like_a_year():
    r, got = ids(LIST + "اكسنت لوحه خ4/ 1909\n")
    assert (None, "خ4/ 1909") in got


def test_foreign_phone_on_a_list_line():
    assert "00249912345678" not in chassis(LIST + "00249912345678\n")


def test_number_under_its_description_line():
    text = "الحصر (2571)\nبكس مستبيشي\n214111\nتوسان سلفر\n051111\nفيستو اسود\n145111\n"
    r = prose.extract(text)
    assert [v["chassis"] for v in r["vehicles"]] == ["214111", "051111", "145111"]
    assert r["vehicles"][1]["model_key"] == "hyundai tucson"


def test_number_under_a_chat_header_is_not_lost():
    text = LIST + "[4/8 6:49 م] ابو مهند: كشف جديد\n523111\n002111\n"
    got = chassis(text)
    assert "523111" in got and "002111" in got


# --- found by reading the first batch's spot check (2026-11-20) ---------------
def test_or_between_two_readings_is_not_a_plate():
    r, got = ids(LIST + "نيسان 418111 او 413111\n")
    assert ("418111", None) in got and ("413111", None) in got
    assert not any(p for _, p in got)
    assert chassis("موجوده اكسنت رقم الشاسي 10006111 او 01611") == ["10006111", "01611"]


def test_plate_keyed_list():
    text = "حصر\nخ2 - 66411 - توسان\nخ2 - 68011 - هايلوكس\nخ3 - 105 - فلوكسواجن\nخ2 - 931011 - هايلوكس\n"
    r, got = ids(text)
    assert got == [(None, "66411 خ2"), (None, "68011 خ2"), (None, "105 خ3"), (None, "931011 خ2")]
    assert r["vehicles"][0]["model_key"] == "hyundai tucson"


def test_one_car_over_several_lines_is_one_vehicle():
    r, got = ids("عربية كلك\nاللون سلفر\nرقم الشاسي 011211\nرقم اللوحة 22811ش د\nت 0912345678")
    assert got == [("011211", "22811ش د")]
    v = r["vehicles"][0]
    assert v["model_key"] == "hyundai getz" and v["color"] == "silver"


def test_comment_under_a_found_list_is_not_marked_found():
    text = LIST + "Shared post\nمحمد حسن\nاللهم اردد علي ضالتي\nلوحه 90511\nشاسي 149111\nReply\n"
    r = prose.extract(text)
    by = {v.get("chassis") or v.get("plate"): v for v in r["vehicles"]}
    assert by["334111"]["status"] == "found"
    assert by["149111"]["status"] == "unknown" and by["90511"]["status"] == "unknown"


def test_comment_is_not_merged_into_the_post_above():
    text = "مفقود اكسنت اللون ابيض رقم الشاسي 920111\nShared post\nمأمون\nعندي دفار رقم اللوحة 43111 شندي\nReply"
    _, got = ids(text)
    assert got == [("920111", None), (None, "43111")]


def test_short_chassis_typed_with_a_space():
    assert chassis("عندي دفار رقم الشاسي 33 151 شندي") == ["33 151"]
    assert chassis("بوكس شاسي 123456 2015") == ["123456"]


def test_number_under_a_description_that_holds_a_year():
    r = prose.extract(LIST + "بكس 2008\n390111098\n")
    v = r["vehicles"][-1]
    assert v["chassis"] == "390111098" and v["model_key"] == "pickup (بوكس)"


def test_post_saying_the_number_is_incomplete():
    r = prose.extract(LIST + "هوندا شاسي 41711 ناقص رقم بعد الاربعه\n")
    assert "incomplete" in r["vehicles"][-1]["low_confidence"]


# --- invariant: nothing is ever invented -------------------------------------
@pytest.mark.parametrize("text", [
    LIST,
    "موجود رينو رقم الشاسي 0727111 رقم اللوحة خ4/ 72111",
    "فاقد ركشة رقم الشاسي ٨٦١١١ تاريخ الصنع 2020م رقم اللوحة خ. ر 6511 / الخرطوم",
    "كشف رقم 10 1/ركشة لبنية شاسي 237111/سنتافي كبدي لوحة ش1 68111/سيراتو ابيض شاسي 5657111",
])
def test_every_emitted_identifier_is_in_the_text(text):
    runs = source_runs(prose.clean(text))
    for v in prose.extract(text)["vehicles"]:
        for field in ("chassis", "plate"):
            if v.get(field):
                assert grounded(v[field], runs), (field, v[field])
