"""The grounding gate: an identifier whose digits are not in the post's own
text (plus its photo OCR) was not read, it was invented. 106 invented
identifiers were caught this way; none was self-reported by the model.

These cases must keep FAILING CLOSED. A missing field costs a family one
lookup; a wrong one sends them to a stranger.
"""
from ingest_local_verdicts import _grounded, strip_ungrounded

POST = "كورولا بيضاء شاسي 7614337 لوحة خ4/ 13297 للتواصل 0912345678"


def test_fabricated_chassis_is_nulled():
    v = {"vehicles": [{"chassis": "5559911"}]}
    dropped = strip_ungrounded(v, POST)
    assert v["vehicles"][0]["chassis"] is None
    assert dropped


def test_real_chassis_survives():
    v = {"vehicles": [{"chassis": "7614337"}]}
    assert strip_ungrounded(v, POST) == []
    assert v["vehicles"][0]["chassis"] == "7614337"


def test_reordered_plate_survives():
    """The model writes the standard order; the post wrote the region first."""
    assert _grounded("13297 خ4", "".join(c for c in POST if c.isdigit()))


def test_arabic_indic_post_grounds_ascii_value():
    v = {"vehicles": [{"chassis": "7614337"}]}
    assert strip_ungrounded(v, "شاسي ٧٦١٤٣٣٧") == []


def test_invented_phone_is_nulled():
    v = {"vehicles": [{"chassis": "7614337", "contact": "0" + "999000111"}]}
    strip_ungrounded(v, POST)
    assert v["vehicles"][0]["contact"] is None
    assert v["vehicles"][0]["chassis"] == "7614337"


def test_extracted_block_is_gated_too():
    v = {"extracted": {"vin": "5559911", "plate": "13297"}}
    strip_ungrounded(v, POST)
    assert v["extracted"]["vin"] is None
    assert v["extracted"]["plate"] == "13297"


def test_ocr_text_grounds_a_photo_read():
    """The gate grounds against post text + media.ocr_text. Remove that join
    and an ingest deletes every identifier the OCR pass recovered."""
    ocr = "VIN XXXBB9HE5F7654321"
    v = {"vehicles": [{"chassis": "7654321"}]}
    assert strip_ungrounded(v, "كورولا للبيع\n" + ocr) == []
