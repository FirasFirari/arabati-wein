"""Print the batches that genuinely still need an agent.

Assigning ranges by hand went wrong twice on 2026-08-24: once by giving
085-090 to two agents at the same time, and once by computing the outstanding
set from `done/` alone — which missed batches whose verdict file already
existed on disk but had not been ingested yet, so they looked untouched and
were dispatched a second time.

A batch is outstanding only if there is NO verdict file for it anywhere:
neither ingested (in some `done/`) nor sitting written-but-not-yet-ingested in
an agent's folder. Run this immediately before every dispatch and assign only
what it prints.

Usage:
    python scripts/next_batches.py [--dir data/reextract_batches] [--verdicts data/reextract_verdicts] [--limit N]
"""
import argparse
import glob
import pathlib


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="data/reextract_batches")
    ap.add_argument("--verdicts", default="data/reextract_verdicts")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()

    batches = {pathlib.Path(f).stem.split("_")[1]
               for f in glob.glob(f"{a.dir}/batch_*.json")}
    # every verdict file, wherever it sits: ingested (done/) or still pending
    covered = {pathlib.Path(f).stem.split("_")[1]
               for f in glob.glob(f"{a.verdicts}/**/verdicts_*.json", recursive=True)}

    todo_all = sorted(batches - covered)
    todo = todo_all[:a.limit] if a.limit else todo_all

    print(f"batches total      : {len(batches)}")
    print(f"already covered    : {len(batches & covered)}")
    print(f"OUTSTANDING        : {len(todo_all)}")
    if a.limit and len(todo_all) > a.limit:
        # --limit truncates the LIST, never the count. Reading a truncated list
        # as the total once turned "88 outstanding" into a reported "14".
        print(f"  (list below shows the first {a.limit} of {len(todo_all)} "
              f"— DISPLAY limit only)")
    if todo:
        print("\nassign these (and nothing else):")
        print("  " + ", ".join(f"batch_{n}.json" for n in todo))
    else:
        print("\nnothing outstanding — every batch has a verdict file")


if __name__ == "__main__":
    main()
