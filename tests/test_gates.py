"""The grounding gate, now applied to every write path (db.add_vehicles).

Must keep failing closed: a missing field costs a family one lookup, a wrong
one sends them to a stranger. Synthetic values only.
"""
import sqlite3

import pytest

from carhunt import db
from carhunt.gates import UNCHECKED_PHOTO, check_entries, grounded, source_runs

POST = "كشف\nكورولا شاسي 7614337\nاكسنت ٦١٣٥٧٢ لوحة خ4/ 13297\nللتواصل 0912 345 678"


def test_values_inside_one_number_are_grounded():
    runs = source_runs(POST)
    for v in ("7614337", "613572", "13297 خ4", "0912345678", "614337"):
        assert grounded(v, runs), v


def test_digits_glued_across_two_numbers_are_not():
    """The old gate joined every digit in the post into one string, so a value
    straddling two numbers ('4337' + '613') passed."""
    assert not grounded("4337613", source_runs(POST))


def test_invented_value_is_nulled_and_reported():
    e = [{"chassis": "5559911", "contact": "0912345678"}]
    changes = check_entries(e, POST, photos_unread=False)
    assert e[0]["chassis"] is None and e[0]["contact"] == "0912345678"
    assert changes and "nulled" in changes[0]


def test_short_region_split_plate_survives():
    assert grounded("28خ4", source_runs("لوحة 28 خ4"))


def test_unreadable_photo_keeps_the_value_but_says_so():
    e = [{"chassis": "JTTEST01J3L410078"}]
    check_entries(e, "كورولا للبيع", photos_unread=True)
    assert e[0]["chassis"] == "JTTEST01J3L410078"
    assert e[0]["low_confidence"] == UNCHECKED_PHOTO


def test_prose_in_an_identifier_field_is_stripped():
    e = [{"chassis": "no match for 7614337 here"}]
    check_entries(e, POST, photos_unread=False)
    from carhunt.normalize import has_prose
    assert e[0]["chassis"] is None or not has_prose(e[0]["chassis"])


# ---- wired into the database: every lane passes through it ----------------
@pytest.fixture()
def tmpdb(tmp_path, monkeypatch, test_config):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "carhunt.db")
    monkeypatch.setattr(db, "LOCK_LOG", tmp_path / "locks.log")
    monkeypatch.setattr(db, "GATE_LOG", tmp_path / "gate.log")
    monkeypatch.setattr(db, "_CFG", test_config)
    db.init()
    return tmp_path / "carhunt.db"


def test_add_vehicles_grounds_against_text_plus_ocr(tmpdb):
    pid = db.add_post("telegram:@x", "tg:@x:1", None, "كورولا للبيع", ["a.jpg"])
    con = sqlite3.connect(tmpdb)
    con.execute("UPDATE media SET ocr_text = 'VIN XXXBB9HE5F7654321', ocr_at = '2026-11-01' "
                "WHERE post_id = ?", (pid,))
    con.commit()
    db.add_vehicles(pid, [{"chassis": "7654321"}, {"chassis": "5559911"}])
    got = [r[0] for r in con.execute("SELECT chassis FROM vehicles WHERE post_id = ? ORDER BY id", (pid,))]
    con.close()
    assert got == ["7654321", None]


def test_live_lane_without_a_model_leaves_the_post_unscored(tmpdb, test_config):
    from carhunt.pipeline import analyze_post
    pid = db.add_post("telegram:@x", "tg:@x:2", None, "عربة للبيع شاسي 613572")
    v = analyze_post(None, dict(test_config, general_sources=[]), pid, "عربة للبيع شاسي 613572", [],
                     post_source="telegram:@x")
    assert v["tier"] == "queued" and v["score"] is None
    con = sqlite3.connect(tmpdb)
    assert con.execute("SELECT score FROM posts WHERE id = ?", (pid,)).fetchone()[0] is None
    con.close()


def test_live_lane_still_raises_the_target_alert_for_free(tmpdb, test_config, car):
    from carhunt.pipeline import analyze_post
    text = f"شاسي {car['vin'][-7:]}"
    pid = db.add_post("telegram:@x", "tg:@x:3", None, text)
    v = analyze_post(None, dict(test_config, general_sources=[]), pid, text, [], post_source="telegram:@x")
    assert v["score"] == 95 and v["tier"] == "identifier_scan"
