"""The last check before publishing: scan what actually ships.

A personal number the owner ruled out on 2026-09-09 stayed live inside
detail.bin until 2026-11-04 because grep cannot see into a gzip.
"""
import gzip
import json

import pytest

from carhunt import publish_guard as g


@pytest.fixture()
def pats(test_config):
    return g.forbidden_patterns(test_config)


@pytest.mark.parametrize("text", [
    "اتصل 0123456789", "+249 123 456 789", "٠١٢٣٤٥٦٧٨٩", "012-345-6789", "249" + "123456789",
])
def test_every_spelling_is_caught(pats, text):
    assert g.contains(text, pats)
    assert "456" not in g.mask(text, pats).translate(g._DIGIT_FOLD)


@pytest.mark.parametrize("text", ["5" + "0123456789" + "55", "0912345678", "", None])
def test_other_numbers_are_left_alone(pats, text):
    assert not g.contains(text, pats)
    assert g.mask(text, pats) == text


def test_mask_keeps_surrounding_text(pats):
    assert g.mask("للتواصل ٠١٢٣٤٥٦٧٨٩ مساء", pats) == "للتواصل [رقم محذوف] مساء"


def test_payload_scan_sees_inside_gzip(pats):
    blob = gzip.compress(json.dumps({"t": {"contact": ["0123456789"]}}).encode())
    assert g.scan_payload("detail.bin", blob, pats)
    clean = gzip.compress(json.dumps({"t": {"contact": ["0912345678"]}}).encode())
    assert g.scan_payload("detail.bin", clean, pats) == []


def test_unreadable_payload_is_a_finding(pats):
    assert g.scan_payload("core.bin", b"not gzip", pats)


def test_leftover_template_token_is_a_finding(pats):
    assert g.scan_html("index.html", "<p>__STATS__</p>", pats)
    assert g.scan_html("index.html", "<p>ok</p>", pats) == []


def test_too_short_numbers_are_not_guessed():
    assert g.forbidden_patterns({"publish": {"forbidden_contacts": ["123"]}}) == []


def test_a_posters_own_number_stays_and_anyone_elses_is_masked():
    own = "0912345678"
    text = "للتواصل 0912345678 او صاحب الرخصة ٠٩٩٩١١١٢٢٢"
    out = g.mask_foreign_phones(text, own)
    assert "0912345678" in out
    assert "٠٩٩٩١١١٢٢٢" not in out and "[رقم محذوف]" in out


def test_no_contact_means_every_number_in_free_text_is_masked():
    assert g.mask_foreign_phones("call 0912 345 678", None) == "call [رقم محذوف]"
    assert g.mask_foreign_phones("chassis 912345678", None) == "chassis 912345678"  # no 0 prefix: not a phone


def test_guard_refuses_a_stray_number_in_shipped_text():
    detail = {"t": {"contact": ["0912345678"], "description": ["call 0912345678", "owner " + "0" + "999111222"],
                    "location": []}}
    assert g.scan_detail_privacy(detail)
    detail["t"]["description"] = ["call 0912345678"]
    assert g.scan_detail_privacy(detail) == []
