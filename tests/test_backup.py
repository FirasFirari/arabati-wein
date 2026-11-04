"""The nightly backup: consistent, verified, compressed, rotated."""
import json
import sqlite3

import pytest

import backup_db as bk


@pytest.fixture()
def env(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    con = sqlite3.connect(data / "carhunt.db")
    con.executescript("CREATE TABLE posts(id INTEGER PRIMARY KEY, text TEXT);"
                      "CREATE TABLE media(id INTEGER PRIMARY KEY);"
                      "CREATE TABLE vehicles(id INTEGER PRIMARY KEY);"
                      "INSERT INTO posts(text) VALUES ('x'), ('y');")
    con.commit()
    con.close()
    (data / "watches.json").write_text("[]", encoding="utf-8")
    monkeypatch.setattr(bk, "DATA_DIR", data)
    monkeypatch.setattr(bk, "BACKUPS", data / "backups")
    monkeypatch.setattr(bk, "SMALL_FILES", [data / "watches.json", data / "absent.json"])
    monkeypatch.setattr(bk, "log", lambda m: None)
    return data


def test_nightly_backup_is_verified_compressed_and_manifested(env):
    from compression import zstd
    dest = bk.nightly()
    manifest = json.loads((dest / "MANIFEST.json").read_text())
    assert manifest["files"]["carhunt.db.zst"]["rows"]["posts"] == 2
    assert "watches.json" in manifest["files"]
    assert not (dest / "carhunt.db.partial").exists()
    raw = zstd.decompress((dest / "carhunt.db.zst").read_bytes())
    assert raw.startswith(b"SQLite format 3")


def test_a_folder_without_manifest_is_not_a_good_backup(env):
    (bk.BACKUPS / "2026-01-01").mkdir(parents=True)
    assert bk.good_backups() == []


def test_rotation_keeps_the_newest_good_backups(env):
    for day in range(1, 11):
        d = bk.BACKUPS / f"2026-01-{day:02d}"
        d.mkdir(parents=True)
        (d / "MANIFEST.json").write_text("{}")
    removed = bk.rotate(keep=7)
    assert removed == ["2026-01-01", "2026-01-02", "2026-01-03"]
    assert [d.name for d in bk.good_backups()][0] == "2026-01-04"


def test_offsite_never_runs_unencrypted(env, monkeypatch):
    monkeypatch.setattr(bk, "load_config", lambda: {"backup": {}})
    assert bk.offsite(force=True) == "not-configured"
