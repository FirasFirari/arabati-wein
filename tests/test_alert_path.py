"""A target hit must reach a human; a model must never supply its own evidence."""
import json
import sqlite3

import pytest

from carhunt import db


def test_a_model_cannot_write_its_own_evidence():
    pytest.importorskip("anthropic")
    from carhunt.analyzer import _parse_verdict
    reply = json.dumps({"score": 95, "identifier_hit": "chassis matches target",
                        "verified_by_eye": "fine", "reasons": "looks like it"})
    v = _parse_verdict(reply)
    assert "identifier_hit" not in v and "verified_by_eye" not in v
    assert v["score"] == 95          # the score stays; the cap then holds it at 69


@pytest.fixture()
def tmpdb(tmp_path, monkeypatch, test_config):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "carhunt.db")
    monkeypatch.setattr(db, "LOCK_LOG", tmp_path / "locks.log")
    monkeypatch.setattr(db, "GATE_LOG", tmp_path / "gate.log")
    monkeypatch.setattr(db, "_CFG", test_config)
    db.init()
    return tmp_path / "carhunt.db"


def test_target_number_in_a_registry_row_is_found(tmpdb, car):
    import raw_sweep
    tail = car["vin"][-7:]
    pid = db.add_post("facebook:1", "fb:1", None, f"كشف\nكورولا {tail}")
    other = db.add_post("facebook:1", "fb:2", None, "كشف\nكورولا 613572")
    db.add_vehicles(pid, [{"chassis": tail}])
    db.add_vehicles(other, [{"chassis": "613572"}])
    con = sqlite3.connect(tmpdb)
    hits = raw_sweep.target_vehicle_scan(con, car, 0)
    assert [h["post_id"] for h in hits] == [pid]
    last = con.execute("SELECT MAX(id) FROM vehicles").fetchone()[0]
    assert raw_sweep.target_vehicle_scan(con, car, last) == []     # incremental
    con.close()


def test_near_miss_uses_every_digit_the_number_has(car):
    pytest.importorskip("openai")
    from openai_backlog import near_miss
    tail7 = car["vin"][-7:]                                    # fictional: 7654321
    two_off = "8" + tail7[1:4] + "9" + tail7[5:]                # differs at 2 of 7
    assert near_miss(two_off, car) is None                      # was a "near miss" via 6 digits
    assert near_miss(tail7[:3] + "9" + tail7[4:], car)          # one off at 7: still raised
    assert near_miss(tail7[1:4] + "9" + tail7[5:], car)         # a 6-digit fragment, one off
    assert near_miss(tail7, car) is None                        # exact is a HIT, not a near miss
