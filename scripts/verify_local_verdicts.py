"""Mechanically verify a local verdict file against its source batch.

Checks that need no gold labels:
  1. coverage      — every post id in the batch is present
  2. grounding     — every chassis/plate/contact emitted occurs in that post's
                     text (valid because these posts are text-only)
  3. body-type     — no van/pickup/bus described as a sedan model
  4. target scan   — re-runs the deterministic identifier scan over the text,
                     independent of whatever the model concluded

Usage:
    python scripts/verify_local_verdicts.py data/local_batches data/local_verdicts
"""
import json
import pathlib
import re
import sys
import unicodedata

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from carhunt.aliases import normalize_ar
from carhunt.config import load_config
from carhunt.prefilter import identifier_hit

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


AR = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
SEDANS = ("corolla", "camry", "accent", "avante", "lancer", "elantra", "sunny", "sephia")
BODYTYPES = ("شريح", "بوكس", "حافل", "لوري", "قلاب", "ركش", "تكتك", "امجاد", "أمجاد", "دقو")


def norm(s):
    s = unicodedata.normalize("NFKC", str(s or "")).translate(AR)
    # normalize_ar folds ة/ه, أإآ/ا, ى/ي — otherwise a faithful transcription
    # that spells سعودية where the post wrote سعوديه reads as ungrounded
    return normalize_ar(re.sub(r"[\s\-_/.,]", "", s).lower())


def digits(s):
    return re.sub(r"\D", "", unicodedata.normalize("NFKC", str(s or "")).translate(AR))


def grounded(value, text):
    """Is every component of this identifier actually present in the post?

    Not a contiguous match: posts write a plate as "4008 / خ2" and a model may
    emit "خ2 4008", and a chassis printed twice (Latin + Arabic-Indic) may be
    emitted as "E4F5072023 / 5062023". Both are faithful. So require that each
    digit-run and each Arabic token of the emitted value occurs in the source,
    which still catches an invented number while allowing re-ordering.
    """
    if value in (None, "", "null"):
        return True
    ntext, dtext = norm(text), digits(text)
    if norm(value) and norm(value) in ntext:
        return True
    runs = re.findall(r"\d{2,}", unicodedata.normalize("NFKC", str(value)).translate(AR))
    words = re.findall(r"[؀-ۿ]+", str(value))
    if not runs and not words:
        return True
    for r in runs:
        if r not in dtext:
            return False
    for w in words:
        if norm(w) not in ntext:
            return False
    return True


def main() -> None:
    batches = pathlib.Path(sys.argv[1])
    verdicts = pathlib.Path(sys.argv[2])
    car = load_config()["car"]
    total_posts = total_veh = 0
    problems = []
    alerts = []

    for vf in sorted(verdicts.glob("verdicts_*.json")):
        num = vf.stem.split("_")[1]
        bf = batches / f"batch_{num}.json"
        if not bf.exists():
            problems.append(f"{vf.name}: no matching batch file")
            continue
        posts = {p["id"]: p for p in json.loads(bf.read_text(encoding="utf-8"))}
        try:
            data = json.loads(vf.read_text(encoding="utf-8"))
        except Exception as e:
            problems.append(f"{vf.name}: invalid JSON — {e}")
            continue
        seen = {i.get("id") for i in data}
        missing = sorted(set(posts) - seen)
        if missing:
            problems.append(f"{vf.name}: MISSING ids {missing[:10]}")

        halluc = bodytype = 0
        for item in data:
            pid = item.get("id")
            if pid not in posts:
                problems.append(f"{vf.name}: id {pid} not in batch")
                continue
            text = posts[pid]["text"]
            v = item.get("verdict") or {}
            total_posts += 1
            for veh in (v.get("vehicles") or []):
                total_veh += 1
                for f in ("chassis", "plate", "contact"):
                    if not grounded(veh.get(f), text):
                        halluc += 1
                        problems.append(f"{vf.name} post {pid}: ungrounded {f}={veh.get(f)!r}")
                desc = (veh.get("description") or "") + " " + (veh.get("model") or "")
                # whole words only: بوكس sits inside جربوكس ("gearbox"), and a
                # post saying the engine and gearbox are missing is still a sedan
                dwords = re.findall(r"[\w؀-ۿ]+", desc)
                if any(any(w.startswith(b) for b in BODYTYPES) for w in dwords) and \
                   any(s in (veh.get("model") or "").lower() for s in SEDANS):
                    bodytype += 1
                    problems.append(f"{vf.name} post {pid}: body-type as sedan {veh.get('model')!r}")
            hit = identifier_hit(text, car)
            if hit:
                alerts.append((pid, hit, posts[pid].get("url")))

    print(f"verified {total_posts} posts, {total_veh} vehicles")
    print(f"problems: {len(problems)}")
    for p in problems[:25]:
        print("   ", p)
    if alerts:
        print("\n*** DETERMINISTIC TARGET HITS — VERIFY MANUALLY ***")
        for pid, hit, url in alerts:
            print(f"   post {pid}: {hit}\n      {url}")
    else:
        print("\nno deterministic target-identifier hits in these batches")
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
