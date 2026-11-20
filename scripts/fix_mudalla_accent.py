"""Relabel rows where a standalone مضلع was filed as a Toyota Hilux.

Owner's rule, 2026-11-20: مضلع on its own is a Hyundai Accent. Until then the
alias table AND the model prompt both said "Toyota Hilux double-cab", so the
label is in two kinds of row:

  * list/prose-parser rows, whose make/model came from the alias table;
  * model-read rows, where a model wrote make=Toyota, model="Hilux ..."
    because the prompt told it to.

scripts/rederive_model_keys.py cannot repair the second kind: it trusts the
make/model fields, and here those fields ARE the error. This script judges by
the DESCRIPTION, the post's own words.

A row is changed only when ALL hold:
  * its stored model_key is "toyota hilux";
  * the description holds مضلع as a word;
  * the description holds no ARABIC evidence of a real pickup or Toyota
    (هايلوكس, بوكس, بكس, تايوتا, تويوتا, دبل, قمار, غمار, بلاط).
English in a description is the model's gloss, written under the old prompt
("مضلع (Hilux double-cab)"), not the poster's words, so it is not evidence and
is not read. The new label is whatever carhunt.aliases reads in the Arabic of
the description (an Avante if the post says "افانتي مضلع"), which for a bare
مضلع is Hyundai Accent.

    python scripts/fix_mudalla_accent.py            # dry run
    python scripts/fix_mudalla_accent.py --apply    # writes an undo file first
    python scripts/rederive_model_keys.py --undo data/mudalla_accent_<date>.jsonl
"""
import argparse
import datetime
import json
import pathlib
import re
import sys
from collections import Counter

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from carhunt import db  # noqa: E402
from carhunt.aliases import _find_word, model_keys_in, normalize_ar  # noqa: E402

PICKUP_EVIDENCE = tuple(normalize_ar(w) for w in (
    "هايلوكس", "بوكس", "بكس", "بكاسي", "تايوتا", "تويوتا", "دبل", "قمار",
    "غمار", "بلاط"))
_LATIN = re.compile(r"[A-Za-z]+")
BATCH = 2000


def make_model(key):
    make, _, model = key.partition(" ")
    return make.title(), model.title()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--show", type=int, default=12)
    args = ap.parse_args()
    db.init()
    conn = db.connect()

    c = Counter()
    to = Counter()
    kept_examples, changes = Counter(), []
    for vid, make, model, desc in conn.execute(
            "SELECT id, make, model, description FROM vehicles WHERE model_key = 'toyota hilux'"):
        c["rows labelled toyota hilux"] += 1
        d = normalize_ar(desc or "")
        if _find_word(d, "مضلع") == -1:
            c["  no مضلع in the description: left alone"] += 1
            continue
        # whole words: "جربوكس" (gearbox) is not a بوكس
        if any(_find_word(d, w) != -1 for w in PICKUP_EVIDENCE):
            c["  مضلع beside pickup/Toyota words: left alone"] += 1
            kept_examples[(desc or "")[:90]] += 1
            continue
        keys = model_keys_in(_LATIN.sub(" ", desc or ""))
        key = keys[0] if keys else None
        if not key or key == "toyota hilux":
            c["  new rules still read no other model: left alone"] += 1
            continue
        new_make, new_model = make_model(key)
        changes.append((vid, key, new_make, new_model, make, model))
        to[key] += 1

    c["rows to relabel"] = len(changes)
    for k, v in c.items():
        print(f"{k:55s}: {v}")
    print("\nrelabelled as:")
    for k, v in to.most_common(8):
        print(f"  {v:7d}  {k}")
    print("\nleft as Hilux because the text names a pickup or Toyota (sample):")
    for d, n in kept_examples.most_common(args.show):
        print(f"  {n:5d}x  {d}")

    if not args.apply:
        print("\ndry run: nothing written. Add --apply to update.")
        return
    if not changes:
        print("\nnothing to change; no undo file written")
        return
    undo_path = ROOT / "data" / f"mudalla_accent_{datetime.datetime.now():%Y-%m-%d_%H%M%S}.jsonl"
    # A second --apply on 2026-11-20 found nothing to change and overwrote the
    # first run's undo file with an empty one. Never reuse a name, never
    # write an empty file, never open an existing one for writing.
    if undo_path.exists():
        raise SystemExit(f"{undo_path} exists: refusing to overwrite an undo file")
    with open(undo_path, "w", encoding="utf-8") as f:
        for vid, _, _, _, omake, omodel in changes:
            f.write(json.dumps({"id": vid, "model_key": "toyota hilux", "make": omake,
                                "model": omodel}, ensure_ascii=False) + "\n")
    print(f"\nundo file: {undo_path} ({len(changes)} rows)")
    for i in range(0, len(changes), BATCH):
        with db.write_tx() as wconn:
            wconn.executemany(
                "UPDATE vehicles SET model_key = ?, make = ?, model = ? WHERE id = ?",
                [(k, mk, md, vid) for vid, k, mk, md, _, _ in changes[i:i + BATCH]])
    print(f"updated {len(changes)} rows")


if __name__ == "__main__":
    main()
