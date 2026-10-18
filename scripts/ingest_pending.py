"""Ingest every verdict file that has not been ingested yet, then park it.

The loop used to re-ingest every file on every tick with --force. That is slow
(save_analysis re-runs over hundreds of already-correct posts) and it made the
ungrounded-identifier counter meaningless, because the same fabricated values
were re-detected and re-reported each pass as though they were new. Files move
to done/ once written, so each is ingested exactly once and the counts mean
what they say.

Usage:
    python scripts/ingest_pending.py <batch_dir> <verdict_dir> [--tier T] [--force] [--no-shrink]
"""
import argparse
import json
import pathlib
import shutil
import subprocess
import sys

# This script prints Arabic — post text, plate values like '12345 خ1', the
# identifiers the grounding gate nulls. The Windows console is cp1252 and
# raises UnicodeEncodeError on the first one. Here that is not cosmetic: the
# crash lands INSIDE the ingest loop, after some posts have already been
# written, leaving a half-ingested batch. It did exactly that on wave 1 —
# batch 021 stopped at 64 of 121 posts.
import sys as _sys
try:
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    _sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass



def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("batch_dir", type=pathlib.Path)
    ap.add_argument("verdict_dir", type=pathlib.Path)
    ap.add_argument("--tier", default="local_claude_sonnet")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--no-shrink", action="store_true")
    a = ap.parse_args()

    done = a.verdict_dir / "done"
    done.mkdir(parents=True, exist_ok=True)
    ingested = skipped = ungrounded = unmatched = 0
    alerts: list[str] = []

    for vf in sorted(a.verdict_dir.glob("verdicts_*.json")):
        num = vf.stem.split("_")[1]
        # Vision batches are imgbatch_NNN.json, text batches are batch_NNN.json.
        # This used to assume "batch_" only and `continue` when the file was
        # missing, so a whole vision agent's 12 finished files were skipped in
        # silence while the summary still printed "0 left pending".
        for stem in ("batch_", "imgbatch_"):
            bf = a.batch_dir / f"{stem}{num}.json"
            if bf.exists():
                break
        else:
            unmatched += 1
            print(f"  {vf.name}: NO BATCH FILE in {a.batch_dir} - not ingested")
            continue
        try:                       # a file still being written is not an error
            json.loads(vf.read_text(encoding="utf-8"))
        except Exception:
            skipped += 1
            print(f"  {vf.name}: still being written, leaving for next tick")
            continue
        cmd = [sys.executable, "scripts/ingest_local_verdicts.py", str(vf),
               "--batch", str(bf), "--tier", a.tier]
        if a.force:
            cmd.append("--force")
        if a.no_shrink:
            cmd.append("--no-shrink")
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
        out = r.stdout or ""
        if "COVERAGE FAILURE" in out:
            skipped += 1
            print(f"  {vf.name}: COVERAGE FAILURE — left in place for re-run")
            continue
        # A file whose every entry was rejected wrote NOTHING. Parking it in
        # done/ would lose it silently - that is exactly how one agent's 195
        # posts and 3,277 vehicles disappeared behind an "ingested" line.
        # Only a REAL rejection blocks parking. "wrote 0 verdicts" alone is
        # legitimate — every post in the file was already scored (free_pass
        # settles reposts, another agent got there first). Treating that as a
        # rejection stranded two files forever, retried every tick.
        if "malformed verdict" in out:
            bad = out.count("malformed verdict")
            skipped += 1
            print(f"  {vf.name}: {bad} entr(ies) rejected - NOT ingested, "
                  f"left in place for re-run")
            continue
        for line in out.splitlines():
            if line.startswith("  !!"):
                alerts.append(line.strip())
            if "UNGROUNDED IDENTIFIERS NULLED:" in line:
                ungrounded += int(line.split(":")[1].strip().split()[0])
        shutil.move(str(vf), str(done / vf.name))
        ingested += 1

    print(f"\ningested {ingested} new verdict file(s); {skipped} left pending")
    if unmatched:
        print(f"!! {unmatched} verdict file(s) had NO matching batch "
              f"and were NOT ingested - wrong batch_dir?")
    print(f"ungrounded identifiers nulled (this run only): {ungrounded}")
    if alerts:
        print("\n*** SCORED >=70 — VERIFY ***")
        for al in alerts:
            print("  ", al)


if __name__ == "__main__":
    main()
