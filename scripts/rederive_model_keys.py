"""Re-derive model labels after a change to carhunt/aliases.py.

A row's model key is computed once, at ingest. When the alias rules change,
stored rows keep the label the OLD rules gave them. This recomputes it and
touches ONLY rows whose stored label is exactly what the old rules produced
and which the new rules read differently. A label somebody corrected by hand,
or one written under a third set of rules, is left alone and counted.

    git show HEAD:carhunt/aliases.py > old_aliases.py      # the rules being replaced
    python scripts/rederive_model_keys.py --old-rules old_aliases.py            # dry run
    python scripts/rederive_model_keys.py --old-rules old_aliases.py --apply
    python scripts/rederive_model_keys.py --undo data/alias_rederive_<stamp>.jsonl

Three kinds of row:

  PARSER rows (list and prose parser). Key, make and model all came from the
  alias table reading the description, so all three are rewritten.

  MODEL rows, ordinary case. make/model are a model's own reading and are
  kept; only the key is recomputed from them.

  MODEL rows, NICKNAME case. The prompt taught the models the same table
  ("دفار = Land Cruiser pickup"), so a model wrote make=Toyota,
  model="Land Cruiser pickup" for a post that only says دفار. There the
  make/model fields ARE the error. Such a row is changed when the ARABIC of
  its description, read by the old rules, gives exactly the stored key, the
  model's make/model say the same thing or nothing, and the new rules read
  that Arabic without the old key in it at all. English in a description is the model's gloss, not
  the poster's words, and is not read. make/model are then rewritten too.

--apply writes an undo file first (never overwriting one), then updates in
small transactions. Check that nothing is left with the DRY run, never with a
second --apply.
"""
import argparse
import datetime
import importlib.util
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

from carhunt import aliases as new  # noqa: E402
from carhunt import db  # noqa: E402

PARSER_TIERS = {"kashf_parser", "prose_parser"}
BATCH = 2000
_LATIN = re.compile(r"[A-Za-z]+")


def old_split(key):
    """How the parsers used to turn a key into make/model, until 2026-11-20."""
    if not key:
        return None, None
    make, _, model = key.partition(" ")
    return make.title() or None, (model.title() or None)


def load_old(path):
    spec = importlib.util.spec_from_file_location("aliases_old", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def tier_of(verdict: str) -> str | None:
    i = verdict.find('"tier"')
    if i == -1:
        return None
    j = verdict.find('"', i + 7)
    return verdict[j + 1:verdict.find('"', j + 1)]


def first(mod, text):
    return (mod.model_keys_in(text or "") or [None])[0]


def undo(path: pathlib.Path) -> None:
    rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    for i in range(0, len(rows), BATCH):
        with db.write_tx() as conn:
            conn.executemany(
                "UPDATE vehicles SET model_key = ?, make = ?, model = ? WHERE id = ?",
                [(r["model_key"], r["make"], r["model"], r["id"]) for r in rows[i:i + BATCH]])
    print(f"restored {len(rows)} rows from {path}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--old-rules", type=pathlib.Path)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--undo", type=pathlib.Path)
    ap.add_argument("--show", type=int, default=30)
    args = ap.parse_args()
    db.init()
    if args.undo:
        undo(args.undo)
        return
    if not args.old_rules:
        ap.error("--old-rules is required")
    old = load_old(args.old_rules)
    old_pairs = {old_split(k) for k in old.ALIASES} | {new.make_model_for(k) for k in new.ALIASES}

    conn = db.connect()
    parser_posts = {pid for pid, verdict in conn.execute(
        "SELECT id, verdict FROM posts WHERE verdict IS NOT NULL")
        if tier_of(verdict) in PARSER_TIERS}

    c = Counter()
    trans = Counter()
    changes = []          # (id, key, make, model, old_key, old_make, old_model)
    cache = {}
    for vid, pid, make, model, desc, stored in conn.execute(
            "SELECT id, post_id, make, model, description, model_key FROM vehicles"):
        c["rows"] += 1
        parser = pid in parser_posts
        k = (parser, make, model, desc, stored)
        if k not in cache:
            target, why = None, None
            if parser:
                o, w = first(old, desc), first(new, desc)
                derived = (make, model) in old_pairs or (make is None and model is None)
                if stored == o and derived:
                    t = (w, *new.make_model_for(w))
                    if t != (stored, make, model):
                        target, why = t, "parser row"
            else:
                o = old.model_key_for(make, model, desc)
                w = new.model_key_for(make, model, desc)
                arabic = _LATIN.sub(" ", desc or "")
                oa, wa = first(old, arabic), first(new, arabic)
                # ...unless the poster's own Arabic names the stored key: a model
                # that wrote "Kia Pride" over a post saying فستو must not turn
                # 217 Vistos into Prides the day the table learns the word Pride
                if stored == o and w != o and not (stored and stored in new.model_keys_in(arabic)):
                    target, why = (w, make, model), "model row: key recomputed"
                # ...and the new rules no longer find the old key in that Arabic
                # AT ALL. "اتوذ او فستو" stays the Visto a model chose: both
                # readings are still there, only their order changed.
                if (oa is not None and stored == oa and wa != oa
                        and oa not in new.model_keys_in(arabic)
                        and old.model_key_for(make, model, None) in (oa, None)):
                    target, why = (wa, *new.make_model_for(wa)), "model row: nickname in the post"
            cache[k] = (target, why)
        target, why = cache[k]
        if target is None:
            continue
        c[why] += 1
        changes.append((vid, *target, stored, make, model))
        trans[(stored, target[0])] += 1

    c["rows to update"] = len(changes)
    for k, v in c.items():
        print(f"{k:42s}: {v}")
    print("\nlargest changes (same key = only make/model rewritten):")
    for (o, w), n in trans.most_common(args.show):
        print(f"  {n:7d}  {o}  ->  {w}")

    if not args.apply:
        print("\ndry run: nothing written. Add --apply to update.")
        return
    if not changes:
        print("\nnothing to change; no undo file written")
        return
    undo_path = ROOT / "data" / f"alias_rederive_{datetime.datetime.now():%Y-%m-%d_%H%M%S}.jsonl"
    if undo_path.exists():       # an undo file is never overwritten
        raise SystemExit(f"{undo_path} exists: refusing to overwrite an undo file")
    with open(undo_path, "x", encoding="utf-8") as f:
        for vid, _, _, _, okey, omake, omodel in changes:
            f.write(json.dumps({"id": vid, "model_key": okey, "make": omake, "model": omodel},
                               ensure_ascii=False) + "\n")
    print(f"\nundo file: {undo_path} ({len(changes)} rows)")
    for i in range(0, len(changes), BATCH):
        with db.write_tx() as wconn:
            wconn.executemany(
                "UPDATE vehicles SET model_key = ?, make = ?, model = ? WHERE id = ?",
                [(w, mk, md, vid) for vid, w, mk, md, _, _, _ in changes[i:i + BATCH]])
    print(f"updated {len(changes)} rows")


if __name__ == "__main__":
    main()
