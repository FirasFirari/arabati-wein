"""Find official impound / lost-vehicle YARDS in the OCR of photographed signage.

This searches a channel that did not exist before the Google Vision sweep: the
text physically painted on signs, gates and walls in 16,800 photographs. The
registry search asks "is the family's chassis in a post?" and has answered no
across ~300,000 records. This asks a different and probably better question:
"where are the buildings that hold recovered cars?"

That matters because a yard is searched AT THE GATE BY CHASSIS NUMBER, which is
the one identifier the family already holds. It does not require the car to
have been photographed, posted, or recognised by anyone.

SAFETY. Every one of these facilities has been hijacked as scam bait — post
19058 has a private WhatsApp number stamped onto the government sign itself.
Approach ONLY through the police or traffic authority. Never through a phone
number found in or beside one of these posts, and never through a
Messenger-funnel account. Read the extortion section of the sudan-recovery-intel
notes before contacting anyone.

Usage:
    python scripts\\find_yards.py
    python scripts\\find_yards.py --all      # every match, not just strong ones
"""
import argparse
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from carhunt import db

# Words that appear on OFFICIAL signage. Split into two classes: a match needs
# at least one INSTITUTION word plus one YARD word, so a post that merely says
# "شرطة" in passing does not qualify.
INSTITUTION = ["شرطة السودان", "هيئة المباحث", "إدارة المرور", "ادارة المرور",
               "SUDAN POLICE", "POLICE", "TRAFFIC"]
YARD = ["حوش السيارات", "حوش العربات", "المفقودة", "المحجوزة", "المضبوطة",
        "IMPOUND", "SEIZURE", "RECOVERED"]


def norm(s: str) -> str:
    """Collapse whitespace so a sign broken across OCR lines still matches."""
    return re.sub(r"\s+", " ", s or "").strip()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true",
                    help="include weak single-term matches")
    a = ap.parse_args()

    conn = db.connect()
    rows = conn.execute(
        "SELECT post_id, ocr_text FROM media "
        "WHERE ocr_text IS NOT NULL AND ocr_text <> ''").fetchall()

    by_post = defaultdict(list)
    for r in rows:
        by_post[r[0]].append(r[1])

    signs = Counter()
    hits = {}
    for pid, chunks in by_post.items():
        text = norm(" ".join(chunks))
        up = text.upper()
        inst = [w for w in INSTITUTION if w in text or w in up]
        yard = [w for w in YARD if w in text or w in up]
        if not (inst and yard) and not (a.all and (inst or yard)):
            continue
        hits[pid] = (inst, yard, text)
        # the sign itself, as a fingerprint for grouping distinct facilities
        for m in re.finditer(r"(حوش[^|]{0,60})", text):
            signs[norm(m.group(1))[:70]] += 1

    print(f"searched {len(rows):,} OCR'd images across {len(by_post):,} posts")
    print(f"posts whose PHOTOS show official yard signage: {len(hits)}\n")

    print("=" * 70)
    print("DISTINCT FACILITY SIGNS (by how the sign itself reads)")
    print("=" * 70)
    for sign, n in signs.most_common(15):
        print(f"  {n:>4}x  {sign}")

    print("\n" + "=" * 70)
    print("POSTS TO OPEN")
    print("=" * 70)
    for pid in sorted(hits)[:40]:
        inst, yard, text = hits[pid]
        row = conn.execute("SELECT url, seen_at FROM posts WHERE id = ?",
                           (pid,)).fetchone()
        url = (row["url"] if row else None) or "(no url)"
        print(f"\npost {pid}  {(row['seen_at'] or '')[:10] if row else ''}")
        print(f"  {url}")
        print(f"  sign: {text[:150]}")
    if len(hits) > 40:
        print(f"\n... and {len(hits) - 40} more posts")

    print("\n" + "=" * 70)
    print("A yard is searched at the gate BY CHASSIS NUMBER — the identifier the")
    print("family already holds. Approach via the police or traffic authority")
    print("ONLY. Every one of these has been hijacked as scam bait; post 19058")
    print("has a private WhatsApp number stamped onto the government sign.")
    print("=" * 70)


if __name__ == "__main__":
    main()
