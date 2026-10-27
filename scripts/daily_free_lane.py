"""Once a day, at $0: free_pass -> كشف parser -> Gemini Flash-Lite x2 -> reconcile.

This is the runbook in memory `free-pipeline.md`, made unattended. Run by the
"CarHunt Daily Free Lane" scheduled task; safe to run by hand too.

    python scripts\\daily_free_lane.py
    python scripts\\daily_free_lane.py --calls 20     # a small trial

Order matters and is the same as the runbook: each stage removes work from the
next. OCR is NOT a stage here: the Google Vision project is billing-suspended
and is the one stage that costs money.

The two Flash-Lite models each have their own 500/day free quota, so each gets
--calls (default 460, leaving headroom for retries). Posts go newest first:
intake (~3,000/day) outruns this lane (~800/day), so oldest-first would never
reach anything collected this month.

Cost guard: `--max-calls` caps requests whatever the price table says (the
$646 Vertex bill happened because a guessed price fooled the dollar meter).
On the free tier the meter's dollar figure is notional; nothing is billed
while the project has no active billing. IF BILLING IS EVER RE-ENABLED on the
Gemini key's project, these same calls become paid — re-check before then.

Does nothing while data/PAUSED exists.
"""
import argparse
import datetime
import json
import pathlib
import sqlite3
import subprocess
import sys

# scheduled under pythonw: never flash a console window for a child process
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
LOG = DATA / "logs" / "daily_free_lane.log"
MODELS = ("gemini-3.5-flash-lite", "gemini-3.1-flash-lite")


def log(msg: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}\n")


def run(label: str, args: list[str]) -> int:
    log(f"--- {label}: {' '.join(args)}")
    with LOG.open("a", encoding="utf-8") as fh:
        r = subprocess.run([sys.executable, *args], cwd=ROOT, stdout=fh,
                           stderr=subprocess.STDOUT,
                           env={**__import__("os").environ, "PYTHONUNBUFFERED": "1",
                                "PYTHONIOENCODING": "utf-8"},
                           creationflags=NO_WINDOW)
    log(f"--- {label} exit {r.returncode}")
    return r.returncode


def unscored_ids() -> set[int]:
    conn = sqlite3.connect(f"file:{DATA / 'carhunt.db'}?mode=ro", uri=True)
    return {r[0] for r in conn.execute("SELECT id FROM posts WHERE score IS NULL")}


def reconcile(before: set[int]) -> None:
    """Claimed vehicles (verdict.vehicles) vs stored rows, for every post this
    run scored. Driven from the posts that LEFT the unscored set, so a post
    that claimed vehicles but stored none is caught — keying on `vehicles`
    rows would miss exactly that failure."""
    conn = sqlite3.connect(f"file:{DATA / 'carhunt.db'}?mode=ro", uri=True)
    now_unscored = {r[0] for r in conn.execute(
        "SELECT id FROM posts WHERE score IS NULL")}
    scored = before - now_unscored
    # `vehicles` has NO index on post_id: a per-post COUNT(*) is a full scan
    # of ~1M rows each, and doing that for ~1,000 posts is what was running
    # when the first trial was killed for low memory. One streaming pass,
    # counting only the posts we care about, instead.
    stored_by = dict.fromkeys(scored, 0)
    for (pid,) in conn.execute("SELECT post_id FROM vehicles"):
        if pid in stored_by:
            stored_by[pid] += 1
    bad, claimed, stored = [], 0, 0
    ids = sorted(scored)
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        q = ",".join("?" * len(chunk))
        for pid, c in conn.execute(
                "SELECT id, COALESCE(json_array_length(json_extract(verdict,"
                " '$.vehicles')), 0) FROM posts WHERE id IN"
                # free_pass copies a verdict onto a cross-posted duplicate but
                # stores the rows only on the original: by design, not a loss
                f" ({q}) AND COALESCE(json_extract(verdict, '$.tier'), '')"
                " <> 'duplicate'", chunk):
            s = stored_by[pid]
            claimed += c
            stored += s
            if c != s:
                bad.append((pid, c, s))
    log(f"RECONCILE {len(scored)} posts scored: claimed {claimed} vs stored "
        f"{stored} -> " + ("OK" if not bad else
                           f"MISMATCH on {len(bad)} posts, e.g. {bad[:5]}"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--calls", type=int, default=460,
                    help="max Gemini requests PER MODEL (free quota is 500/day)")
    args = ap.parse_args()

    if (DATA / "PAUSED").exists():
        log("paused (data/PAUSED exists) — skipped")
        return
    before = unscored_ids()
    log(f"=== daily free lane start: {len(before)} unscored ===")

    run("free_pass", ["-m", "carhunt.free_pass"])

    wave = DATA / f"kashf_auto_{datetime.date.today():%Y%m%d}"
    if run("kashf parse", ["scripts/kashf_verdicts.py", "--out", str(wave)]) == 0 \
            and (wave / "verdicts_kashf.json").exists():
        verdicts = json.loads((wave / "verdicts_kashf.json").read_text("utf-8"))
        if verdicts:
            run("kashf ingest", ["scripts/ingest_local_verdicts.py",
                                 str(wave / "verdicts_kashf.json"),
                                 "--batch", str(wave / "batch_kashf.json"),
                                 "--tier", "kashf_parser"])
        else:
            log("kashf: no new list posts")

    for model in MODELS:
        if (DATA / "PAUSED").exists():
            log("paused mid-run — stopping before " + model)
            break
        run(f"gemini {model}", [
            "scripts/openai_backlog.py", "--provider", "gemini", "--use-ocr",
            "--newest-first", "--skip-over-chars", "8000", "--workers", "3",
            "--screen-model", model, "--deep-model", model,
            "--max-calls", str(args.calls), "--budget", "10"])

    reconcile(before)
    run("raw sweep", ["scripts/raw_sweep.py", "--fetch", "--db-scan"])
    log("=== daily free lane done ===")


if __name__ == "__main__":
    main()
