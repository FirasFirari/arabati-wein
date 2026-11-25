"""Several identifiers in one field become several rows.

`norm_chassis` strips separators, so "A, B" was stored as the number AB:
neither A nor B could be found, and a fragment straddling the join gave a
false watch hit (2026-11-25). Synthetic numbers only: this file is public.
"""
import sqlite3

import pytest

from carhunt import db
from carhunt.normalize import split_chassis, split_plates


@pytest.mark.parametrize("value, parts", [
    ("71200918, 71100765, 71000574", ["71200918", "71100765", "71000574"]),
    ("0738224 او 38224 او 900506690", ["0738224", "900506690"]),       # tail of another = same car
    ("944536, 660944536, 11XBR22E660944536", ["11XBR22E660944536"]),
    ("937678 / 923901903916", ["937678", "923901903916"]),
    ("12345 or 67890", ["12345", "67890"]),
    ("71200918, 068, 59", ["71200918"]),                               # too short to search
    ("٩٥٢١٧٧٣١٨٤١٥ - 952177318415", ["952177318415"]),                  # one number, two scripts
    ("918002005950 ٩١٨٠٠٢٠٠٥٩٥٠", ["918002005950"]),
])
def test_fields_with_several_numbers_are_split(value, parts):
    assert split_chassis(value) == parts


@pytest.mark.parametrize("value", [
    "JTDBW23E 963092298",          # a VIN written with a space is ONE number
    "926250 022063",
    "93655199455 ٩٢٢٦٥٥١٩٩٤٥٥",    # two DIFFERENT readings with no separator: not guessed at
    "5000 5000",
    "9113335",
    "KMH-923456",
    "007599 (OCR: TNAA2E888FA907598)",   # brackets and words: the commentary guard's job
    "غير واضح, 12345",
    "ج9/ 90302",                   # a plate filed as chassis is not split on its slash
    "", None,
])
def test_single_identifiers_are_left_alone(value):
    assert split_chassis(value) == []


def test_plates_split_on_commas_and_or_but_not_on_slashes():
    assert split_plates("95669 خ1, 90367 خ1") == ["95669 خ1", "90367 خ1"]
    assert split_plates("خ/ 95066 او / 95069") == ["خ/ 95066", "95069"]
    assert split_plates("خ 5 / 92471") == []
    assert split_plates("12345 خ1 او 12345") == []      # the same serial twice is one plate
    assert split_plates("خ1, 92345") == []


def test_alternates_keep_the_cars_other_fields():
    e = {"make": "Toyota", "chassis": "0738224 او 900506690", "plate": "خ1 9123"}
    rows = db.expand_entry(e)
    assert [(r["chassis"], r["plate"], r["make"]) for r in rows] == [
        ("0738224", "خ1 9123", "Toyota"), ("900506690", "خ1 9123", "Toyota")]


def test_a_list_is_not_paired_with_a_plate():
    e = {"chassis": "91111, 92222, 93333, 94444", "plate": "9054 ج1"}
    rows = db.expand_entry(e)
    assert [(r["chassis"], r["plate"]) for r in rows] == [
        ("91111", None), ("92222", None), ("93333", None), ("94444", None), (None, "9054 ج1")]


def test_ordinary_entry_is_untouched():
    e = {"chassis": "9113335", "plate": "خ 5 / 92471"}
    assert db.expand_entry(e) == [e]


def test_each_number_is_stored_and_findable(tmp_path, monkeypatch, test_config):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "carhunt.db")
    monkeypatch.setattr(db, "LOCK_LOG", tmp_path / "locks.log")
    monkeypatch.setattr(db, "GATE_LOG", tmp_path / "gate.log")
    monkeypatch.setattr(db, "_CFG", test_config)
    db.init()
    text = "كشف\n71200918\n71100765\n71000574\n70443465\n"
    pid = db.add_post("telegram:@x", "tg:@x:1", None, text, [])
    db.add_vehicles(pid, [{"make": "Hyundai", "model": "Accent",
                           "chassis": "71200918, 71100765, 71000574, 70443465"}])
    con = sqlite3.connect(tmp_path / "carhunt.db")
    got = sorted(r[0] for r in con.execute("SELECT chassis_norm FROM vehicles WHERE post_id = ?", (pid,)))
    con.close()
    assert got == ["70443465", "71000574", "71100765", "71200918"]
