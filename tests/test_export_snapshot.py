"""Export snapshot: a synthetic registry in, the published files out.

Locks the whole build (grouping into dossiers, repost merging, packing, the
template, the privacy pass) so a refactor -- e.g. moving the 84 KB template out
of export_site.py -- can prove it changed NOTHING a visitor receives.

The snapshot hashes the DECOMPRESSED payloads and the HTML with line endings
normalised and the ?v= hashes blanked, so it is stable across operating
systems and zlib versions. After an INTENDED output change, regenerate it:

    UPDATE_SNAPSHOT=1 python -m pytest tests/test_export_snapshot.py
"""
import datetime as _dt
import gzip
import hashlib
import json
import os
import re
import sqlite3
import types

import pytest

from conftest import FIXTURES

SNAPSHOT = FIXTURES / "export_snapshot.json"
FORBIDDEN = "0123456789"          # tests/fixtures/config.test.yaml publish.forbidden_contacts

POSTS = [  # id, source, url, text, seen_at, score
    (1, "facebook:111", "https://www.facebook.com/groups/111/posts/1", "كشف عربات", "2026-10-01T10:00:00", 10),
    (2, "facebook:111", None, "كشف عربات", "2026-10-02T10:00:00", 10),
    (3, "telegram:@chan", "https://t.me/chan/3", "عربة للبيع", "2026-10-03T10:00:00", 20),
    (4, "facebook:222", "https://www.facebook.com/profile.php?id=222", "مفقودة", "2026-10-04T10:00:00", 5),
]
VEHICLES = [  # post_id, make, model, color, year, chassis, plate, status, location, contact, description
    (1, "Toyota", "Corolla", "white", "2015", "613572", None, "found", "Omdurman", None, "كورولا ابيض"),
    (2, "Toyota", "Corolla", "white", "2015", "613572", None, "found", "Omdurman", None, "كورولا ابيض"),
    (2, "Hyundai", "Accent", "red", None, "8264019", None, "found", None, None, "اكسنت احمر"),
    (3, "Toyota", "Hilux", None, "2018", None, "52731 خ 3", "for_sale", "Khartoum", FORBIDDEN,
     f"للتواصل {FORBIDDEN} او 0912345678"),
    (4, "Kia", "Rio", "silver", "2016", "JTTEST01J3L410078", None, "seeking_owner", None,
     "0912345678", "registration card owner named somebody"),
    (4, None, None, None, None, None, None, "unknown", None, None, "عربة بدون رقم"),
]


def _build(tmp_path, monkeypatch, test_config):
    from carhunt import db, export_site, publish_guard

    dbfile = tmp_path / "carhunt.db"
    monkeypatch.setattr(db, "DB_PATH", dbfile)
    db.init()
    con = sqlite3.connect(dbfile)
    con.executemany("INSERT INTO posts (id, source, source_ref, url, text, seen_at, score) "
                    "VALUES (?, ?, 'ref' || ?, ?, ?, ?, ?)",
                    [(i, s, i, u, t, a, sc) for i, s, u, t, a, sc in POSTS])
    con.executemany(
        "INSERT INTO vehicles (post_id, make, model, color, year, chassis, chassis_norm, plate, "
        "plate_norm, status, location, contact, description, extracted_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?, '2026-10-05T00:00:00')",
        [(p, mk, md, c, y, ch, (re.sub(r'[^A-Z0-9]', '', ch.upper()) if ch else None), pl,
          (re.search(r'\d{4,}', pl).group(0) if pl else None), st, lo, co, de)
         for p, mk, md, c, y, ch, pl, st, lo, co, de in VEHICLES])
    con.execute("INSERT INTO media (post_id, path) VALUES (4, 'data/media/x.jpg')")
    con.commit()
    con.close()

    cfg = dict(test_config, facebook={"groups": [{"id": "111", "name": "مجموعة تجربة (12K)"}]})
    monkeypatch.setattr(export_site, "load_config", lambda: cfg)
    monkeypatch.setattr(publish_guard, "load_config", lambda: cfg)
    monkeypatch.setattr(export_site, "OUT", tmp_path / "site" / "arabati-wein.html")

    class _Date(_dt.date):
        @classmethod
        def today(cls):
            return cls(2026, 11, 4)
    monkeypatch.setattr(export_site, "datetime", types.SimpleNamespace(date=_Date))
    export_site.main()
    return tmp_path / "site"


def _digest(site) -> dict:
    html = (site / "arabati-wein.html").read_text(encoding="utf-8").replace("\r\n", "\n")
    html = re.sub(r"\?v=[0-9a-f]{12}", "?v=X", html)
    core = gzip.decompress((site / "core.bin").read_bytes())
    detail = gzip.decompress((site / "detail.bin").read_bytes())
    sha = lambda b: hashlib.sha1(b).hexdigest()
    return {"html": sha(html.encode("utf-8")), "core": sha(core), "detail": sha(detail)}


def test_export_snapshot(tmp_path, monkeypatch, test_config):
    site = _build(tmp_path, monkeypatch, test_config)
    got = _digest(site)
    if os.environ.get("UPDATE_SNAPSHOT") or not SNAPSHOT.exists():
        SNAPSHOT.write_text(json.dumps(got, indent=1) + "\n", encoding="utf-8")
        pytest.skip("snapshot written; re-run to compare")
    assert got == json.loads(SNAPSHOT.read_text(encoding="utf-8"))


def test_export_privacy_pass(tmp_path, monkeypatch, test_config):
    site = _build(tmp_path, monkeypatch, test_config)
    detail = gzip.decompress((site / "detail.bin").read_bytes()).decode("utf-8")
    core = gzip.decompress((site / "core.bin").read_bytes()).decode("utf-8")
    assert FORBIDDEN not in detail and FORBIDDEN not in core
    assert "[رقم محذوف]" in detail                      # masked inside the description
    assert "owner named" not in detail                   # registration-card text redacted
    assert "0912345678" in detail                        # a poster's own contact still ships


def test_export_groups_reposts_into_one_dossier(tmp_path, monkeypatch, test_config):
    site = _build(tmp_path, monkeypatch, test_config)
    core = json.loads(gzip.decompress((site / "core.bin").read_bytes()))
    # 613572 (twice), 8264019, the plate-only Hilux, the Kia VIN, and the
    # identifier-less row -> 5 dossiers
    assert len(core["ord"]) == 5
