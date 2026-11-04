"""The write path: one short BEGIN IMMEDIATE transaction per save.

Before 2026-11-04 a verdict and its vehicle rows were two transactions, and the
per-post DELETE scanned 1.26M rows with the write lock held. "database is
locked" then killed the daily Gemini lane on 5 of 6 runs and left post 171728
scored with no rows -- a state no lane ever retries.
"""
import sqlite3
import threading
import time

import pytest

from carhunt import db


@pytest.fixture()
def tmpdb(tmp_path, monkeypatch, test_config):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "carhunt.db")
    monkeypatch.setattr(db, "LOCK_LOG", tmp_path / "db_locks.log")
    monkeypatch.setattr(db, "_CFG", test_config)   # the alert cap reads config
    db.init()
    return tmp_path / "carhunt.db"


def rows(path, post_id):
    con = sqlite3.connect(path)
    try:
        return con.execute("SELECT chassis, low_confidence FROM vehicles WHERE post_id = ? "
                           "ORDER BY id", (post_id,)).fetchall()
    finally:
        con.close()


def post(path, post_id):
    con = sqlite3.connect(path)
    try:
        return con.execute("SELECT score, extracted FROM posts WHERE id = ?", (post_id,)).fetchone()
    finally:
        con.close()


def test_schema_has_the_lock_critical_indexes(tmpdb):
    con = sqlite3.connect(tmpdb)
    names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    con.close()
    assert {"idx_vehicles_post", "idx_media_post"} <= names


def test_add_post_dedups_on_source_ref(tmpdb):
    first = db.add_post("telegram:@x", "tg:@x:1", None, "نص", ["a.jpg"])
    assert first is not None
    assert db.add_post("telegram:@x", "tg:@x:1", None, "نص") is None


def test_save_analysis_writes_verdict_and_rows_together(tmpdb):
    pid = db.add_post("telegram:@x", "tg:@x:2", None, "كورولا 613572")
    db.save_analysis(pid, {"score": 10, "vehicles": [{"chassis": "613572"}]})
    assert post(tmpdb, pid) == (10, 1)
    assert rows(tmpdb, pid) == [("613572", None)]
    # a re-analysis REPLACES, never appends
    db.save_analysis(pid, {"score": 12, "vehicles": [{"chassis": "8264019"}]})
    assert rows(tmpdb, pid) == [("8264019", None)]


def test_failed_call_keeps_existing_rows(tmpdb):
    pid = db.add_post("telegram:@x", "tg:@x:3", None, "اكسنت 8264019")
    db.save_analysis(pid, {"score": 10, "vehicles": [{"chassis": "8264019"}]})
    db.save_analysis(pid, {"score": None, "vehicles": []})
    assert rows(tmpdb, pid) == [("8264019", None)]


def test_duplicate_verdict_stores_no_rows(tmpdb):
    pid = db.add_post("telegram:@x", "tg:@x:4", None, "نسخة")
    db.save_analysis(pid, {"score": 10, "vehicles": [{"chassis": "613572"}],
                           "vehicles_copied_from_duplicate": 1})
    assert rows(tmpdb, pid) == []
    assert post(tmpdb, pid)[0] == 10


def test_low_confidence_survives_a_reingest(tmpdb):
    pid = db.add_post("telegram:@x", "tg:@x:5", None, "VIN")
    db.add_vehicles(pid, [{"chassis": "JTTEST01J3L410078", "low_confidence": "two readers disagree"}])
    assert rows(tmpdb, pid) == [("JTTEST01J3L410078", "two readers disagree")]


def test_a_failure_mid_save_rolls_back_the_verdict_too(tmpdb, monkeypatch):
    pid = db.add_post("telegram:@x", "tg:@x:6", None, "x")
    db.save_analysis(pid, {"score": 10, "vehicles": [{"chassis": "613572"}]})

    def boom(conn, post_id, rows):          # fails AFTER the verdict UPDATE ran
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(db, "_write_vehicles", boom)
    with pytest.raises(sqlite3.OperationalError):
        db.save_analysis(pid, {"score": 50, "vehicles": [{"chassis": "8264019"}]})
    assert post(tmpdb, pid)[0] == 10                     # verdict NOT half-written
    assert rows(tmpdb, pid) == [("613572", None)]       # rows untouched


def test_write_waits_out_another_writer(tmpdb):
    holder = sqlite3.connect(tmpdb, isolation_level=None, check_same_thread=False)
    holder.execute("BEGIN IMMEDIATE")
    threading.Timer(0.6, lambda: (holder.execute("COMMIT"), holder.close())).start()
    t0 = time.monotonic()
    with db.write_tx(busy_ms=100) as conn:   # busy -> retry with backoff
        conn.execute("UPDATE posts SET score = 1")
    assert time.monotonic() - t0 >= 0.5


def test_write_gives_up_with_the_real_error(tmpdb):
    holder = sqlite3.connect(tmpdb, isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(sqlite3.OperationalError):
            with db.write_tx(attempts=1, busy_ms=50):
                pass
    finally:
        holder.execute("ROLLBACK")
        holder.close()
