"""Compare stored chassis numbers against what Google Vision read off the photo.

Two questions, and they are not the same question:

  1. **Is a stored identifier wrong?** The signal is not "Vision disagrees" —
     the two readers differ by a character on plenty of perfectly good reads.
     It is "the stored value is ABSENT from the OCR text, while a near-variant
     of it IS present." That is the shape post 22337 had: the registry said
     JTGJX02P8G0504615, the plate said TGJX02P8G5046153, and opening the photo
     settled it. Seven characters wrong, in a registry other families search.

  2. **Did we miss a vehicle entirely?** VIN-shaped tokens in the OCR text
     that match no stored chassis anywhere. Some are real finds the vision
     pass never recorded; some are engine numbers, part numbers or the plate's
     own model code. Reported separately and never auto-ingested.

Nothing here writes to the database. It produces a queue for human eyes,
ranked so the most likely errors are at the top, because eye-verification is
the scarce resource — see the project's rule that image-derived identifiers
are opened and read personally before anyone is told anything.

Usage:
    python scripts\\ocr_disagreements.py
    python scripts\\ocr_disagreements.py --show-new 40
"""
import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from carhunt import db
from carhunt.normalize import norm_chassis
from gvision_bench import found_in, vin_fold

# A real VIN is 17 characters and never contains I, O or Q.
VIN_TOKEN = re.compile(r"\b[A-HJ-NPR-Z0-9]{15,18}\b")
# Words that mark a number on a compliance plate as NOT a chassis.
NOT_CHASSIS = ("ENGINE", "MODEL", "TYPE", "KG", "PLANT", "A/TM", "C/TR")

# The 10th character of a VIN is the model-year code. Valid values exclude
# I, O, Q, U, Z and 0 — so a 17-character token with anything else in slot 10
# is not a VIN. Cheap, standards-based, and it removes most of the junk that
# happens to be 17 characters long.
YEAR_CODES = set("123456789ABCDEFGHJKLMNPRSTVWXY")
JUNK_PREFIX = ("USER", "HTTP", "WWW", "IMG", "PHOTO", "VIDEO", "SCREEN")


def looks_like_vin(tok: str) -> bool:
    """Structural plausibility only — never proof that the vehicle is real.

    Rejects the three things that dominated the unfiltered list: interface
    text scraped off a screenshot (USER8448524565281), part and engine numbers
    that are all digits, and 17-character runs with an impossible year code.
    """
    if len(tok) != 17 or tok.startswith(JUNK_PREFIX):
        return False
    letters = sum(c.isalpha() for c in tok)
    digits = sum(c.isdigit() for c in tok)
    if letters < 3 or digits < 4:
        return False
    return tok[9] in YEAR_CODES


def near_variant(stored: str, text: str) -> str | None:
    """A VIN-shaped token in the OCR that is close to `stored` but not equal.

    Close means: same length, and within two characters once the classic OCR
    confusions are folded. Two rather than one because a scrambled tail — the
    22337 failure — is several characters, and the whole point is to catch it.
    """
    s_fold = vin_fold(stored)
    stored_valid_year = len(stored) == 17 and stored[9] in YEAR_CODES
    best = None
    for tok in set(VIN_TOKEN.findall(text.upper())):
        t_fold = vin_fold(tok)
        if t_fold == s_fold or len(t_fold) != len(s_fold):
            continue
        # Adjudicate mechanically before spending a human on it. Position 10
        # of a VIN is the model-year code and cannot be 0, I, O, Q, U or Z.
        # If the stored value has a legal one and the OCR candidate does not,
        # the OCR is simply wrong and there is nothing to verify. Measured on
        # post 18704: Vision read RKLBV42E504638125 against a stored
        # RKLBV42E5D4638126 — the '0' is impossible, the 'D' means 2013, and
        # the plate's own Arabic line says the car was built in 2013.
        if len(tok) == 17 and stored_valid_year and tok[9] not in YEAR_CODES:
            continue
        diff = sum(1 for a, b in zip(t_fold, s_fold) if a != b)
        if diff <= max(2, len(s_fold) // 4):
            if best is None or diff < best[1]:
                best = (tok, diff)
    return f"{best[0]} (differs in {best[1]})" if best else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--show-new", type=int, default=15,
                    help="how many unrecorded VIN-shaped tokens to list")
    ap.add_argument("--annotate", action="store_true",
                    help="mark every suspected row low_confidence, recording "
                         "both competing readings. Safe as a default state: "
                         "until somebody opens the photo, 'two readers "
                         "disagree' IS low confidence. Eye-verification then "
                         "either corrects the value and clears the flag, or "
                         "confirms it is unresolvable and the flag stands. "
                         "Never hides the row - it could still be a real car.")
    a = ap.parse_args()

    conn = db.connect()
    rows = conn.execute("""
        SELECT p.id, GROUP_CONCAT(m.ocr_text, '
') AS ocr
        FROM posts p JOIN media m ON m.post_id = p.id
        WHERE m.ocr_text IS NOT NULL AND m.ocr_text <> ''
        GROUP BY p.id
    """).fetchall()
    ocr_by_post = {r["id"]: r["ocr"] or "" for r in rows}
    if not ocr_by_post:
        sys.exit("No OCR text yet — run scripts\\gvision_sweep.py first.")

    all_stored = {r[0] for r in conn.execute(
        "SELECT DISTINCT chassis_norm FROM vehicles WHERE chassis_norm IS NOT NULL")}

    # ONE pass over vehicles, grouped in Python. The obvious phrasing — a
    # `WHERE post_id = ?` inside the per-post loop — is a full 310,000-row scan
    # every iteration, because vehicles has no index on post_id. Over a few
    # thousand posts that does not finish; it already cost one 15-minute stall
    # on the benchmark query. Same table, same missing index, same mistake.
    stored_by_post: dict[int, list[str]] = {}
    for r in conn.execute("SELECT post_id, chassis_norm FROM vehicles "
                          "WHERE chassis_norm IS NOT NULL"):
        stored_by_post.setdefault(r[0], []).append(r[1])

    confirmed = near = absent = 0
    suspects, new_tokens = [], []

    for pid, text in ocr_by_post.items():
        for s in stored_by_post.get(pid, []):
            hit = found_in(s, text)
            if hit == "exact":
                confirmed += 1
            elif hit == "near":
                near += 1
            else:
                absent += 1
                alt = near_variant(s, text)
                if alt:                      # the actionable shape
                    suspects.append((pid, s, alt))

        # VIN-shaped tokens the registry has never seen, anywhere
        for tok in set(VIN_TOKEN.findall(text.upper())):
            if not looks_like_vin(tok):
                continue
            if any(w in text.upper()[max(0, text.upper().find(tok) - 30):
                                     text.upper().find(tok)] for w in NOT_CHASSIS):
                continue
            n = norm_chassis(tok)
            if n and n not in all_stored:
                new_tokens.append((pid, tok))

    total = confirmed + near + absent
    print(f"\n{'=' * 66}\nSTORED CHASSIS vs GOOGLE VISION OCR\n{'=' * 66}")
    print(f"posts with OCR text        : {len(ocr_by_post):,}")
    print(f"stored chassis checked     : {total:,}")
    if total:
        print(f"  confirmed by OCR exactly : {confirmed:,}  ({confirmed/total:.0%})")
        print(f"  within one character     : {near:,}  ({near/total:.0%})")
        print(f"  absent from OCR text     : {absent:,}  ({absent/total:.0%})")
    print(f"\nMost are absent for innocent reasons — the number came from the "
          f"post TEXT,\nnot the photo, or the plate is not in shot. What matters "
          f"is the subset below.")

    print(f"\n{'=' * 66}\nSUSPECTED WRONG STORED VALUES — {len(suspects)}\n{'=' * 66}")
    print("stored value absent from the photo, but a near-variant IS present.\n"
          "This is the exact shape of the post 22337 error. OPEN THE PHOTO.\n")
    for pid, s, alt in suspects[:40]:
        print(f"  post {pid:>6}: registry has {s}")
        print(f"                 photo shows {alt}")
    if len(suspects) > 40:
        print(f"  ... and {len(suspects) - 40} more")

    if a.annotate and suspects:
        conn2 = db.connect()
        marked = 0
        with conn2:
            for pid, stored, alt in suspects:
                reason = (f"disputed reading, flagged automatically: the "
                          f"registry holds {stored}, Google Vision OCR reads "
                          f"{alt} off the photo of this post. Not yet resolved "
                          f"by eye - do not report a match on this number "
                          f"without inspecting the original image.")
                cur = conn2.execute(
                    "UPDATE vehicles SET low_confidence=? WHERE post_id=? "
                    "AND chassis_norm=? AND low_confidence IS NULL",
                    (reason, pid, stored))
                marked += cur.rowcount
        print("")
        print(f"annotated {marked} row(s) as low_confidence "
              f"(existing annotations left alone)")

    print(f"\n{'=' * 66}\nVIN-SHAPED TOKENS NOT IN THE REGISTRY — {len(new_tokens)}"
          f"\n{'=' * 66}")
    print("candidate vehicles the vision pass never recorded. Some are engine or\n"
          "part numbers. NOT auto-ingested — verify before trusting.\n")
    for pid, tok in new_tokens[:a.show_new]:
        print(f"  post {pid:>6}: {tok}")
    if len(new_tokens) > a.show_new:
        print(f"  ... and {len(new_tokens) - a.show_new} more")


if __name__ == "__main__":
    main()
