"""OCR sweep: each image read once, text copied to byte-identical copies.

Measured 2026-11-04: 75% of the unread photo backlog was copies of other
photos, 29,510 of them copies of photos already read.
"""
import sqlite3

import pytest

pytest.importorskip("openai")

import gemini_ocr_sweep as sweep  # noqa: E402
from carhunt import db  # noqa: E402


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "carhunt.db")
    monkeypatch.setattr(db, "LOCK_LOG", tmp_path / "locks.log")
    monkeypatch.setattr(sweep, "DB", tmp_path / "carhunt.db")
    monkeypatch.setattr(sweep, "DATA", tmp_path)
    monkeypatch.setattr(sweep, "log", lambda m: None)
    db.init()
    sweep.ensure_schema()
    con = sqlite3.connect(tmp_path / "carhunt.db")
    con.execute("INSERT INTO posts (id, source, source_ref, text, seen_at) VALUES "
                "(1,'facebook:1','a','كشف عربات','2026-11-01'), (2,'facebook:2','b','عربة','2026-11-02')")
    img = (tmp_path / "x.jpg")
    img.write_bytes(b"\xff\xd8\xff same bytes")
    twin = (tmp_path / "x_copy.jpg")
    twin.write_bytes(b"\xff\xd8\xff same bytes")
    other = (tmp_path / "y.jpg")
    other.write_bytes(b"\xff\xd8\xff different")
    con.executemany("INSERT INTO media (id, post_id, path) VALUES (?,?,?)",
                    [(1, 1, str(img)), (2, 2, str(twin)), (3, 2, str(other)),
                     (4, 2, str(tmp_path / "gone.jpg"))])
    con.commit()
    con.close()
    return tmp_path


def rows(env):
    con = sqlite3.connect(env / "carhunt.db")
    try:
        return {r[0]: r[1:] for r in con.execute(
            "SELECT id, sha1, ocr_text, ocr_engine FROM media ORDER BY id")}
    finally:
        con.close()


def test_hashing_groups_identical_bytes(env):
    con = sqlite3.connect(env / "carhunt.db")
    assert sweep.hash_new_files(con) == 4
    r = rows(env)
    assert r[1][0] == r[2][0] != r[3][0]
    assert r[4][0] == ""                          # missing file marked, not re-read forever
    work = sweep.pending(con, 0)
    con.close()
    # 4 files -> 3 distinct images; the copy pair travels together
    assert sorted(sorted(ids) for _, ids in work) == [[1, 2], [3], [4]]


def test_text_is_copied_to_unread_copies_only(env):
    con = sqlite3.connect(env / "carhunt.db")
    sweep.hash_new_files(con)
    con.execute("UPDATE media SET ocr_text='VIN JTDBR32E', ocr_at='2026-11-01T00:00:00Z', "
                "ocr_engine='gemini-ocr:m+verified' WHERE id=1")
    con.commit()
    con.close()
    assert sweep.propagate_twins() == 1
    r = rows(env)
    assert r[2][1:] == ("VIN JTDBR32E", "gemini-ocr:m+verified")
    assert r[3][1] is None                       # different bytes: untouched
    assert sweep.propagate_twins() == 0          # idempotent


def test_a_failed_read_is_never_copied(env):
    con = sqlite3.connect(env / "carhunt.db")
    sweep.hash_new_files(con)
    con.execute("UPDATE media SET ocr_text='', ocr_at='2026-11-01T00:00:00Z', "
                "ocr_error='unalignable reply' WHERE id=1")
    con.commit()
    con.close()
    assert sweep.propagate_twins() == 0
