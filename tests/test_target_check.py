"""Leads on the family's own car get a careful automatic reading, ADVICE ONLY.

The rule this file pins: the reading is written onto the lead and sent to the
operator, and NOTHING else changes -- not the score, not the hit, not the
lead's visibility. A wrongly dismissed real sighting is the worst failure.
"""
import json
import sqlite3

import pytest

from carhunt import db, verify


@pytest.fixture()
def env(tmp_path, monkeypatch, test_config):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "carhunt.db")
    monkeypatch.setattr(db, "LOCK_LOG", tmp_path / "locks.log")
    monkeypatch.setattr(db, "GATE_LOG", tmp_path / "gate.log")
    monkeypatch.setattr(db, "_CFG", test_config)
    monkeypatch.setattr(verify, "TARGET_QUEUE", tmp_path / "target_queue.json")
    monkeypatch.setattr(verify, "LOG", tmp_path / "verify.log")
    monkeypatch.setattr(verify, "_cfg", lambda: {"model": "test", "timeout_s": 5,
                                                 "max_groups_per_run": 5, "min_confidence": 0.6})
    sent = []
    monkeypatch.setattr(verify.notify, "send_telegram", lambda text: sent.append(text) or True)
    db.init()
    photo = tmp_path / "p.jpg"
    photo.write_bytes(b"\xff\xd8\xff\xe0 fake jpeg")
    tail = test_config["car"]["vin"][-7:]
    near = tail[:3] + ("9" if tail[3] != "9" else "8") + tail[4:]
    pid = db.add_post("telegram:@x", "tg:@x:1", "https://t.me/x/1",
                      f"رصد جديد\n13006\n{near}\n280705", [str(photo)])
    db.save_analysis(pid, {"score": 75, "identifier_hit": f"{near} is one character from target tail {tail}",
                           "identifier_source": "near_miss", "vehicles": []})
    return tmp_path, pid, near, sent


def verdict_of(tmp, pid):
    con = sqlite3.connect(tmp / "carhunt.db")
    try:
        score, v = con.execute("SELECT score, verdict FROM posts WHERE id = ?", (pid,)).fetchone()
    finally:
        con.close()
    return score, json.loads(v)


def test_queue_dedups_by_post(env):
    _, pid, near, _ = env
    assert verify.enqueue_target([{"post_id": pid, "why": "x", "source": "near_miss"}]) == 1
    assert verify.enqueue_target([{"post_id": pid, "why": "x", "source": "near_miss"}]) == 0


def test_evidence_has_the_number_in_context_and_the_photo(env, car):
    tmp, pid, near, _ = env
    con = db.connect()
    item = {"post_id": pid, "why": f"{near} is one character from target tail", "source": "near_miss"}
    ev = verify.target_evidence(con, item, car)
    photos = verify._photos(con, pid)
    con.close()
    assert ev["lead"]["matched_number"] == near
    assert near in ev["post"]["excerpts"][0]["excerpt"]
    assert ev["target_car"]["vin"] == car["vin"]
    assert len(photos) == 1


def test_advice_is_recorded_and_nothing_else_changes(env, car):
    tmp, pid, near, sent = env
    verify.enqueue_target([{"post_id": pid, "why": f"{near} is one character from target tail",
                            "source": "near_miss"}])
    seen = {}

    def fake_ask(ev, photos, cfg):
        seen["photos"] = photos
        return {"verdict": "different car", "confidence": 0.9, "reason": "typed list number",
                "photos_show": ["a stock photo"], "photos_seen": len(photos), "model": "test"}
    con = db.connect()
    stats = verify.run_target(con, car=car, ask=fake_ask)
    con.close()
    score, v = verdict_of(tmp, pid)
    assert stats == {"read": 1, "failed": 0}
    assert score == 75 and v["identifier_hit"]                  # the lead is untouched
    assert v["auto_eye_check"]["verdict"] == "different car"
    assert v["auto_eye_check"]["advice_only"] is True
    assert len(seen["photos"]) == 1
    assert sent and "Advice only" in sent[0] and "different car" in sent[0]
    assert json.loads((tmp / "target_queue.json").read_text()) == []


def test_a_check_that_keeps_failing_is_handed_to_a_human(env, car):
    tmp, pid, near, sent = env
    verify.enqueue_target([{"post_id": pid, "why": "x", "source": "near_miss"}])

    def broken(ev, photos, cfg):
        raise RuntimeError("claude CLI not on PATH")
    con = db.connect()
    for _ in range(verify.MAX_ATTEMPTS):
        verify.run_target(con, car=car, ask=broken)
    con.close()
    assert any("please look by hand" in m for m in sent)
    score, v = verdict_of(tmp, pid)
    assert score == 75 and "auto_eye_check" not in v
