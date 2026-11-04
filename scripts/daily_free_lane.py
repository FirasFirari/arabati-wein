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

Does nothing while data/PAUSED exists, and nothing if it already succeeded in
the last 20 hours (the task repeats hourly from 12:00 so a missed slot is
caught up the same day; `--force` overrides). Reports to the dead-man checks
`daily-lane` and `gemini-scoring` (carhunt/deadman.py) and stamps
data/health/job_daily_free_lane.json, which the watchdog reads to catch up a
lane that has not succeeded for 26 hours.

Exit status: 0 when every step exited 0 and reconcile matched, 1 otherwise.
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
sys.path.insert(0, str(ROOT))
from carhunt import deadman, health  # noqa: E402

DATA = ROOT / "data"
JOB = "daily_free_lane"
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


def reconcile(before: set[int]) -> int:
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
    return len(bad)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--calls", type=int, default=460,
                    help="max Gemini requests PER MODEL (free quota is 500/day)")
    ap.add_argument("--force", action="store_true",
                    help="run even if the lane already succeeded in the last 20 h")
    args = ap.parse_args()

    if (DATA / "PAUSED").exists():
        log("paused (data/PAUSED exists) — skipped")
        return 0
    age = health.job_age_seconds(JOB)
    if age is not None and age < 20 * 3600 and not args.force:
        log(f"already succeeded {age / 3600:.1f} h ago — skipped (use --force)")
        return 0
    health.job_start(JOB)
    deadman.ping("daily-lane", start=True)
    before = unscored_ids()
    log(f"=== daily free lane start: {len(before)} unscored ===")
    steps: dict[str, int] = {}

    steps["free_pass"] = run("free_pass", ["-m", "carhunt.free_pass"])

    wave = DATA / f"kashf_auto_{datetime.date.today():%Y%m%d}"
    steps["kashf parse"] = run("kashf parse", ["scripts/kashf_verdicts.py", "--out", str(wave)])
    if steps["kashf parse"] == 0 and (wave / "verdicts_kashf.json").exists():
        verdicts = json.loads((wave / "verdicts_kashf.json").read_text("utf-8"))
        if verdicts:
            steps["kashf ingest"] = run("kashf ingest", [
                "scripts/ingest_local_verdicts.py", str(wave / "verdicts_kashf.json"),
                "--batch", str(wave / "batch_kashf.json"), "--tier", "kashf_parser"])
        else:
            log("kashf: no new list posts")

    gemini_steps = []
    for model in MODELS:
        if (DATA / "PAUSED").exists():
            log("paused mid-run — stopping before " + model)
            break
        label = f"gemini {model}"
        steps[label] = run(label, [
            "scripts/openai_backlog.py", "--provider", "gemini", "--use-ocr",
            "--newest-first", "--skip-over-chars", "8000", "--workers", "3",
            "--screen-model", model, "--deep-model", model,
            "--max-calls", str(args.calls), "--budget", "10"])
        gemini_steps.append(label)
        # exit 3 = some saves failed and were spooled; write them now (no API calls)
        steps[f"replay after {model}"] = run(
            f"replay after {model}", ["scripts/openai_backlog.py", "--replay-spool"])
        gemini_steps.append(f"replay after {model}")

    mismatches = reconcile(before)
    steps["raw sweep"] = run("raw sweep", ["scripts/raw_sweep.py", "--fetch", "--db-scan"])

    failed = {k: v for k, v in steps.items() if v != 0}
    gem_failed = {k: v for k, v in failed.items() if k in gemini_steps}
    summary = (f"{len(before)} unscored at start; steps " +
               ", ".join(f"{k}={v}" for k, v in steps.items()) +
               f"; reconcile mismatches {mismatches}")
    deadman.ping("gemini-scoring", not gem_failed and bool(gemini_steps),
                 "; ".join(f"{k} exit {v}" for k, v in gem_failed.items()) or "ok")
    ok = not failed and mismatches == 0
    health.job_done(JOB, 0 if ok else 1, "" if ok else f"failed: {sorted(failed)}")
    deadman.ping("daily-lane", ok, summary)
    log(f"=== daily free lane done: {'OK' if ok else 'PROBLEMS'} — {summary} ===")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
