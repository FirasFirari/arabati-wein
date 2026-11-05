"""Nightly verified backup of the registry, plus a weekly encrypted offsite copy.

    python scripts\\backup_db.py            # nightly (the "CarHunt Backup" task)
    python scripts\\backup_db.py --offsite  # force the weekly copy now

Until 2026-11-04 there was no backup of anything: a 917 MB database, the
Telegram sessions and the watch registrations lived only on this laptop.

Nightly, into data/backups/<YYYY-MM-DD>/:
  1. carhunt.db via the SQLite backup API in ONE step: a consistent snapshot
     while the collectors keep writing (measured: 7 s, writers delayed <5 ms).
  2. PRAGMA integrity_check on the copy; a copy that fails is deleted, never
     kept as if it were a backup.
  3. Compressed with zstd (stdlib, Python 3.14): 917 MB -> ~92 MB.
  4. The small irreplaceable files: Telegram sessions, config.yaml, watches,
     reviewed judgements, sweep state.
  5. A MANIFEST.json with sizes and sha256, written LAST: a folder without one
     is an incomplete backup and is never counted as good.
  The 7 newest good folders are kept.

Weekly offsite (when `backup.age_recipient` is configured and `age` is
installed): the newest good folder is packed and ENCRYPTED with age to a public
key, then written to `backup.offsite_dir` (the Google Drive folder, which
syncs it). This job never holds the decryption key -- keep that offline. Four
weekly and three monthly copies are kept. Nothing unencrypted ever leaves the
machine: the database holds registration-card OCR and watch contacts.

Reports to dead-man checks `backup-nightly` and `backup-offsite`.
Does nothing while data/PAUSED exists.
"""
import argparse
import datetime
import hashlib
import io
import json
import shutil
import sqlite3
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from carhunt import deadman  # noqa: E402
from carhunt.config import DATA_DIR, load_config  # noqa: E402

BACKUPS = DATA_DIR / "backups"
KEEP_NIGHTLY = 7
SMALL_FILES = [
    ROOT / "carhunt.session", ROOT / "carhunt_bot.session", ROOT / "config.yaml",
    DATA_DIR / "watches.json", DATA_DIR / "watch_reviewed.json",
    DATA_DIR / "raw_sweep_seen.json", DATA_DIR / "raw_sweep_state.json",
    DATA_DIR / "raw_watch_hits.json", DATA_DIR / "last_deploy.json",
    DATA_DIR / "gemini_quota.json", DATA_DIR / "verify_queue.json",
    DATA_DIR / "verify_verdicts.jsonl", DATA_DIR / "leads_outbox.md",
]
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def log(msg: str) -> None:
    line = f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    try:
        (DATA_DIR / "logs").mkdir(parents=True, exist_ok=True)
        with open(DATA_DIR / "logs" / "backup.log", "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def snapshot(db_path: Path, dest_dir: Path) -> dict:
    """Consistent copy -> integrity check -> zstd. Returns manifest entries."""
    from compression import zstd

    dest_dir.mkdir(parents=True, exist_ok=True)
    raw = dest_dir / "carhunt.db.partial"
    if raw.exists():
        raw.unlink()
    src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=120)
    dst = sqlite3.connect(raw)
    try:
        src.backup(dst)      # one step: a single consistent snapshot under WAL
    finally:
        dst.close()
        src.close()
    chk = sqlite3.connect(raw)
    try:
        result = chk.execute("PRAGMA integrity_check").fetchone()[0]
        counts = {t: chk.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                  for t in ("posts", "media", "vehicles")}
    finally:
        chk.close()
    if result != "ok":
        raw.unlink()
        raise RuntimeError(f"integrity_check failed on the copy: {result[:200]}")
    out = dest_dir / "carhunt.db.zst"
    with open(raw, "rb") as fin, zstd.open(out, "wb", level=9) as fout:
        shutil.copyfileobj(fin, fout, 1 << 20)
    raw_size = raw.stat().st_size
    raw.unlink()
    return {"carhunt.db.zst": {"bytes": out.stat().st_size, "sha256": sha256(out),
                               "raw_bytes": raw_size, "rows": counts}}


def copy_small(dest_dir: Path) -> dict:
    entries = {}
    for f in SMALL_FILES:
        if f.exists():
            target = dest_dir / (("root_" + f.name) if f.parent == ROOT else f.name)
            shutil.copy2(f, target)
            entries[target.name] = {"bytes": target.stat().st_size, "sha256": sha256(target)}
    return entries


def good_backups() -> list[Path]:
    return sorted((d for d in BACKUPS.iterdir() if d.is_dir() and (d / "MANIFEST.json").exists()),
                  key=lambda d: d.name) if BACKUPS.exists() else []


def rotate(keep: int = KEEP_NIGHTLY) -> list[str]:
    removed = []
    for d in good_backups()[:-keep]:
        shutil.rmtree(d)
        removed.append(d.name)
    return removed


def nightly() -> Path:
    BACKUPS.mkdir(parents=True, exist_ok=True)
    dest = BACKUPS / datetime.date.today().isoformat()
    if (dest / "MANIFEST.json").exists():
        log(f"{dest.name} already has a good backup — kept")
        return dest
    started = datetime.datetime.now()
    entries = snapshot(DATA_DIR / "carhunt.db", dest)
    entries.update(copy_small(dest))
    manifest = {"created": started.isoformat(timespec="seconds"),
                "seconds": round((datetime.datetime.now() - started).total_seconds(), 1),
                "files": entries}
    (dest / "MANIFEST.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    db = entries["carhunt.db.zst"]
    log(f"nightly OK {dest.name}: {db['raw_bytes'] / 1e6:.0f} MB -> {db['bytes'] / 1e6:.0f} MB, "
        f"rows {db['rows']}, {len(entries) - 1} small files, {manifest['seconds']} s")
    gone = rotate()
    if gone:
        log(f"rotated out {gone}")
    return dest


# ---- weekly offsite --------------------------------------------------------
def _age_binary(cfg: dict) -> str | None:
    configured = (cfg.get("backup") or {}).get("age_path")
    if configured and Path(configured).exists():
        return configured
    return shutil.which("age")


def offsite(force: bool = False) -> str:
    """Returns 'ok', 'not-due' or 'not-configured' (raises on failure)."""
    cfg = load_config() or {}
    bcfg = cfg.get("backup") or {}
    recipient = (bcfg.get("age_recipient") or "").strip()
    target = Path(bcfg.get("offsite_dir") or (Path.home() / "My Drive" / "CarHunt backups"))
    age = _age_binary(cfg)
    if not recipient or not age:
        log("offsite: skipped — set backup.age_recipient and install age "
            "(nothing unencrypted ever leaves this machine)")
        return "not-configured"
    if not target.parent.exists():
        raise RuntimeError(f"offsite folder's parent {target.parent} does not exist")
    target.mkdir(parents=True, exist_ok=True)
    existing = sorted(target.glob("carhunt-*.tar.age"))
    if existing and not force:
        newest = datetime.date.fromisoformat(existing[-1].stem.split("carhunt-")[1][:10])
        if (datetime.date.today() - newest).days < 7:
            return "not-due"
    goods = good_backups()
    if not goods:
        raise RuntimeError("no good local backup to send")
    src = goods[-1]
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        tar.add(src, arcname=src.name)
    out = target / f"carhunt-{src.name}.tar.age"
    tmp = out.with_suffix(".partial")
    r = subprocess.run([age, "-r", recipient, "-o", str(tmp)], input=buf.getvalue(),
                       capture_output=True, creationflags=NO_WINDOW)
    if r.returncode != 0 or not tmp.exists():
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"age failed (exit {r.returncode})")
    tmp.replace(out)
    log(f"offsite OK: {out.name} ({out.stat().st_size / 1e6:.0f} MB) -> {target}")
    _rotate_offsite(target)
    return "ok"


def _rotate_offsite(target: Path) -> None:
    """Keep the 4 newest weekly copies plus the newest of each of 3 months."""
    files = sorted(target.glob("carhunt-*.tar.age"))
    keep = set(files[-4:])
    by_month = {}
    for f in files:
        by_month[f.stem.split("carhunt-")[1][:7]] = f
    keep.update(sorted(by_month.values())[-3:])
    for f in files:
        if f not in keep:
            f.unlink()
            log(f"offsite rotated out {f.name}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offsite", action="store_true", help="force the weekly offsite copy now")
    args = ap.parse_args()
    if (DATA_DIR / "PAUSED").exists():
        log("paused (data/PAUSED exists) — skipped")
        return 0
    with deadman.tracked("backup", "backup-nightly"):
        nightly()
    try:
        state = offsite(force=args.offsite)
        if state == "ok":
            deadman.ping("backup-offsite")
    except Exception as e:
        log(f"offsite FAILED: {type(e).__name__} {e}")
        deadman.ping("backup-offsite", False, type(e).__name__)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        log(f"nightly FAILED: {type(e).__name__} {e}")
        sys.exit(1)
