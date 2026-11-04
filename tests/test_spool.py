"""A verdict whose save fails is spooled, not lost, and replayed later.

The free-tier call that produced it cannot be bought back the same day.
"""
import json
import sqlite3

import pytest

pytest.importorskip("anthropic")
pytest.importorskip("openai")

import openai_backlog as ob  # noqa: E402
from carhunt import db  # noqa: E402


@pytest.fixture()
def env(tmp_path, monkeypatch, test_config):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "carhunt.db")
    monkeypatch.setattr(db, "LOCK_LOG", tmp_path / "db_locks.log")
    monkeypatch.setattr(db, "_CFG", test_config)
    monkeypatch.setattr(ob, "SPOOL_DIR", tmp_path / "spool")
    db.init()
    return tmp_path


def score(path, pid):
    con = sqlite3.connect(path / "carhunt.db")
    try:
        return con.execute("SELECT score FROM posts WHERE id = ?", (pid,)).fetchone()[0]
    finally:
        con.close()


def test_spooled_verdict_is_replayed(env):
    pid = db.add_post("telegram:@x", "tg:@x:1", None, "كورولا 613572")
    f = ob.spool_verdict(pid, {"score": 10, "vehicles": [{"chassis": "613572"}]}, False,
                         "OperationalError database is locked")
    assert json.loads(f.read_text(encoding="utf-8").splitlines()[0])["id"] == pid
    assert ob.replay_spool("ALL") == 0
    assert score(env, pid) == 10
    assert not f.exists() and f.with_suffix(".replayed").exists()


def test_replay_never_overwrites_a_post_scored_since(env):
    pid = db.add_post("telegram:@x", "tg:@x:2", None, "اكسنت 8264019")
    ob.spool_verdict(pid, {"score": 10, "vehicles": []}, False, "locked")
    db.save_analysis(pid, {"score": 33, "vehicles": [{"chassis": "8264019"}]})
    assert ob.replay_spool("ALL") == 0
    assert score(env, pid) == 33


def test_a_still_failing_replay_stays_in_the_spool(env, monkeypatch):
    pid = db.add_post("telegram:@x", "tg:@x:3", None, "x")
    f = ob.spool_verdict(pid, {"score": 10, "vehicles": []}, False, "locked")

    def locked(*a, **k):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(db, "save_analysis", locked)
    assert ob.replay_spool("ALL") == 3
    assert f.exists() and len(f.read_text(encoding="utf-8").splitlines()) == 1


def test_rescan_keeps_the_higher_score_and_the_longer_list(env):
    pid = db.add_post("telegram:@x", "tg:@x:4", None, "y")
    db.save_analysis(pid, {"score": 40, "vehicles": [{"chassis": "613572"}, {"chassis": "8264019"}]})
    ob.save_rescan(pid, {"score": 20, "vehicles": [{"chassis": "613572"}]})
    con = sqlite3.connect(env / "carhunt.db")
    n = con.execute("SELECT COUNT(*) FROM vehicles WHERE post_id = ?", (pid,)).fetchone()[0]
    con.close()
    assert score(env, pid) == 40 and n == 2
