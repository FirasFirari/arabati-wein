"""The Cloudflare uploader must stage BOTH payloads of a build (they have
different hashes) and keep exactly the previous build's pair as well."""
import pytest

pytest.importorskip("requests")
import cloudflare_deploy as cf  # noqa: E402


def test_stage_keeps_whole_pairs(tmp_path, monkeypatch):
    monkeypatch.setattr(cf, "STAGE", tmp_path / "stage")
    monkeypatch.setattr(cf, "KEEP", tmp_path / "keep")
    monkeypatch.setattr(cf.publish_guard, "forbidden_patterns", lambda: [])
    monkeypatch.setattr(cf.publish_guard, "scan_payload", lambda *a, **k: [])

    def build(tag):
        core, detail = tmp_path / f"c{tag}", tmp_path / f"d{tag}"
        core.write_bytes(b"c" + tag.encode())
        detail.write_bytes(b"d" + tag.encode())
        return {f"core.aaaaaaaaaa{tag}.bin": core, f"detail.bbbbbbbbbb{tag}.bin": detail}

    for tag in ("01", "02", "03"):
        names = cf.stage(build(tag))
    assert sorted(names) == ["core.aaaaaaaaaa02.bin", "core.aaaaaaaaaa03.bin",
                             "detail.bbbbbbbbbb02.bin", "detail.bbbbbbbbbb03.bin"]
    staged = {p.name for p in (tmp_path / "stage").iterdir()}
    assert set(names) <= staged and {"_headers", "index.html", "404.html"} <= staged
    assert sorted(p.name for p in (tmp_path / "keep").glob("*.bin")) == sorted(names)
