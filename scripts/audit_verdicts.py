"""Audit a provider's raw verdicts before they are allowed near the registry.

Reads the JSONL written by `openai_backlog.py --dry-run` and answers the only
question that matters about a new model: is it READING, or is it writing
plausible-looking numbers?

Every check here exists because something got past a softer one:

  * Fabrication is invisible in self-reports. On the local pipeline the
    grounding gate caught 106 invented identifiers and ZERO of them were
    flagged by the agent that produced them. So the fabrication rate is
    measured mechanically, per provider, and reported as a rate — not as a
    pass/fail.
  * Truncation looks like laziness. A model that returns 16 vehicles for a
    كشف list of 1,402 chassis numbers has not "found 16 vehicles", it has been
    cut off mid-write. This counts chassis-shaped digit runs in the source
    text and flags any verdict returning far fewer.
  * Recall needs a control. Against --benchmark posts the local Sonnet/vision
    pipeline already extracted, every identifier has a known-good answer, so
    MISSED and EXTRA are countable rather than a matter of impression.
  * Prompt echo reads as a jackpot. A model reciting the target's own VIN back
    is the single most dangerous output this project can receive, because it
    looks exactly like success. Counted separately and never buried.
  * The body-type trap. شريحة/بوكس/حافلة name a body type, not a model; a
    model that maps "شريحه تايوتا" to a Corolla is inventing the one thing the
    family is searching for.

Usage:
    python scripts\\audit_verdicts.py data\\benchmark_moonshot.jsonl
    python scripts\\audit_verdicts.py FILE --sample 12 > report.txt
"""
import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# The report quotes Arabic post text and Arabic body-type words. The
# Windows console is cp1252 and raises UnicodeEncodeError on the first
# one, killing the report halfway through — after the checks have run
# but before they are shown, which is the worst possible place to die.
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

from carhunt import db
from carhunt.config import load_config
from carhunt.normalize import norm_chassis, norm_plate

# Body-type words that are NOT models. A sedan model name on a post carrying
# one of these is the analyzer prompt's own documented failure case.
BODY_TYPE_WORDS = ["شريحة", "شريحه", "بوكس", "بوكسي", "حافلة", "باص",
                   "لوري", "قلاب", "ركشة", "تكتك", "امجاد"]
SEDAN_MODELS = ["corolla", "camry", "accent", "avante", "lancer", "picanto",
                "getz", "atos", "sunny", "elantra"]

ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")


def digits(s) -> str:
    return re.sub(r"\D", "", str(s or "").translate(ARABIC_DIGITS))


def chassis_runs(text: str) -> int:
    """How many chassis-shaped numbers are visibly in this text?

    A floor, not a count: it is looking for runs of 6+ digits, which is the
    shape Sudanese posts quote a chassis tail in. Used only to catch a verdict
    that returned dramatically fewer vehicles than the post plainly contains.
    """
    return len(re.findall(r"[0-9]{6,}", (text or "").translate(ARABIC_DIGITS)))


def truth_for(post_id: int) -> tuple[set, set]:
    """Chassis and plate norms the trusted local pipeline already found here."""
    conn = db.connect()
    rows = conn.execute(
        "SELECT chassis_norm, plate_norm FROM vehicles WHERE post_id = ?",
        (post_id,)).fetchall()
    return ({r["chassis_norm"] for r in rows if r["chassis_norm"]},
            {r["plate_norm"] for r in rows if r["plate_norm"]})


def verdict_identifiers(v: dict) -> tuple[set, set]:
    chassis, plates = set(), set()
    for veh in (v.get("vehicles") or []):
        if not isinstance(veh, dict):
            continue
        if veh.get("chassis"):
            n = norm_chassis(str(veh["chassis"]))
            if n:
                chassis.add(n)
        if veh.get("plate"):
            n = norm_plate(str(veh["plate"]))
            if n:
                plates.add(n)
    return chassis, plates


def all_digit_runs(v: dict) -> set:
    """Every 5+ digit run ANYWHERE in the verdict, normalised.

    Strict field-by-field recall punishes a disagreement that is not an error.
    Sudanese posts routinely give a bare unlabelled number — post 22295 is
    "بوكس دبل مستبشي ٢٠٠٥ / ٠٠٠٨٤٧" and nothing in it says whether 000847 is a
    chassis or a plate; the local pipeline filed it as chassis and Moonshot as
    plate. The number was READ either way, which is what the search actually
    depends on. So recall is reported twice: strictly by field, and by whether
    the digits were captured at all. The gap between the two is a filing
    disagreement; a miss in BOTH is a real failure to read.
    """
    runs = set()
    def scan(x):
        for run in re.findall(r"[0-9٠-٩۰-۹]{5,}", str(x or "")):
            n = norm_chassis(run)
            if n:
                runs.add(n)
    for veh in (v.get("vehicles") or []):
        if isinstance(veh, dict):
            for f in ("chassis", "plate", "description"):
                scan(veh.get(f))
    ex = v.get("extracted") or {}
    if isinstance(ex, dict):
        for f in ("vin", "plate", "described_vehicle"):
            scan(ex.get(f))
    return runs


VIN_OK = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")


def malformed_vins(v: dict) -> list[str]:
    """VIN-length identifiers that cannot be a VIN.

    A real 17-character VIN is 17 alphanumerics with no I, O or Q — the
    standard omits them precisely because they misread as 1 and 0. Anything
    VIN-shaped but 15 or 18 characters, or carrying a space or a hyphen, was
    not read, it was mangled: Moonshot's image pass returned
    "JTF-JS02P5000056924" (18 chars, hyphen), "TGX02P860546153" (15, leading
    J dropped) and "521010 6 28 5" for plates the local vision pipeline read
    cleanly. Short fragments are NOT flagged — posts here legitimately quote
    6-7 digit chassis tails, and the prompt asks for partial fragments to be
    kept as seen.
    """
    bad = []
    for veh in (v.get("vehicles") or []):
        if not isinstance(veh, dict):
            continue
        raw = str(veh.get("chassis") or "").strip()
        if len(re.sub(r"[^A-Za-z0-9]", "", raw)) < 14:
            continue                      # a fragment, not a claimed full VIN
        if not VIN_OK.match(raw.upper()):
            bad.append(raw)
    return bad


def contract_problems(v: dict) -> list[str]:
    """Does the verdict obey the JSON shape every downstream consumer assumes?"""
    bad = []
    if not isinstance(v.get("vehicles"), list):
        bad.append(f"vehicles is {type(v.get('vehicles')).__name__}, not list")
    else:
        for veh in v["vehicles"]:
            if not isinstance(veh, dict):
                bad.append(f"vehicle entry is {type(veh).__name__}, not dict")
                break
    score = v.get("score")
    if not isinstance(score, int) or not 0 <= score <= 100:
        bad.append(f"score={score!r}")
    if not isinstance(v.get("is_car_post"), bool):
        bad.append(f"is_car_post={v.get('is_car_post')!r}")
    ex = v.get("extracted")
    if ex is not None and not isinstance(ex, dict):
        bad.append(f"extracted is {type(ex).__name__}")
    return bad


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("path", type=Path)
    ap.add_argument("--sample", type=int, default=8,
                    help="how many full verdicts to print for reading by eye. "
                         "The mechanical checks below cannot see a wrong "
                         "reading that is well-formed; only eyes can.")
    a = ap.parse_args()

    cfg = load_config()
    car = cfg["car"]
    target_chassis = norm_chassis(car.get("vin") or "")
    target_tail = target_chassis[-7:] if target_chassis else ""
    target_plate = norm_plate(car.get("plate") or "")

    entries = [json.loads(line) for line in
               a.path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not entries:
        sys.exit(f"{a.path} is empty — the run wrote nothing.")

    db.init()
    conn = db.connect()

    n = len(entries)
    errors = fabricated = fab_posts = echoes = trap = 0
    contract_bad = truncated = malformed = 0
    matched = missed = extra = bench_posts = 0
    misfiled = unread = 0
    tiers = Counter()
    scores = Counter()
    missed_examples, extra_examples, trap_examples, trunc_examples = [], [], [], []
    malformed_examples = []

    for e in entries:
        pid, v = e["id"], e["verdict"]
        row = conn.execute(
            "SELECT text, (SELECT COUNT(*) FROM media m WHERE m.post_id = p.id) imgs "
            "FROM posts p WHERE p.id = ?", (pid,)).fetchone()
        text = (row["text"] if row else "") or ""
        has_images = bool(row and row["imgs"])

        tiers[v.get("tier") or "(none)"] += 1
        if v.get("error"):
            errors += 1
            continue

        problems = contract_problems(v)
        if problems:
            contract_bad += 1
            print(f"CONTRACT post {pid}: {'; '.join(problems)}")

        bad_vins = malformed_vins(v)
        if bad_vins:
            malformed += len(bad_vins)
            if len(malformed_examples) < 8:
                malformed_examples.append(f"post {pid}: {bad_vins[:3]}")

        dropped = v.get("ungrounded_dropped") or []
        if dropped:
            fabricated += len(dropped)
            fab_posts += 1

        v_chassis, v_plates = verdict_identifiers(v)

        # Prompt echo — the model handing back the target's own identifiers.
        if target_chassis and (target_chassis in v_chassis
                               or any(c.endswith(target_tail) for c in v_chassis if target_tail)):
            echoes += 1
            print(f"ECHO post {pid}: returned the target chassis "
                  f"{sorted(v_chassis)} — images={has_images}")
        if target_plate and target_plate in v_plates:
            echoes += 1
            print(f"ECHO post {pid}: returned the target plate {target_plate}")

        # Body-type trap.
        if any(w in text for w in BODY_TYPE_WORDS):
            for veh in (v.get("vehicles") or []):
                if isinstance(veh, dict) and any(
                        s in str(veh.get("model") or "").lower() for s in SEDAN_MODELS):
                    trap += 1
                    if len(trap_examples) < 5:
                        trap_examples.append(
                            f"post {pid}: model={veh.get('model')!r} but text "
                            f"says {[w for w in BODY_TYPE_WORDS if w in text]}")
                    break

        # Truncation — far fewer vehicles than the text plainly carries.
        runs = chassis_runs(text)
        got = len(v.get("vehicles") or []) if isinstance(v.get("vehicles"), list) else 0
        if runs >= 5 and got < runs * 0.5:
            truncated += 1
            if len(trunc_examples) < 5:
                trunc_examples.append(
                    f"post {pid}: {runs} chassis-shaped numbers in text, "
                    f"{got} vehicles returned")

        # Recall against the trusted pass, where there is one.
        t_chassis, _ = truth_for(pid)
        if t_chassis:
            bench_posts += 1
            anywhere = all_digit_runs(v)
            matched += len(t_chassis & v_chassis)
            miss = t_chassis - v_chassis
            ext = v_chassis - t_chassis
            missed += len(miss)
            extra += len(ext)
            # Of the strict misses, how many were read but filed elsewhere?
            misfiled += len(miss & anywhere)
            unread += len(miss - anywhere)
            if miss and len(missed_examples) < 6:
                real = sorted(miss - anywhere)[:4]
                filed = sorted(miss & anywhere)[:4]
                missed_examples.append(
                    f"post {pid}: NOT READ {real} | read-but-misfiled {filed}")
            if ext and len(extra_examples) < 6:
                extra_examples.append(f"post {pid}: extra {sorted(ext)[:4]}")

        s = v.get("score")
        scores[(s // 20 * 20) if isinstance(s, int) else "n/a"] += 1

    print(f"\n{'=' * 66}\nAUDIT {a.path.name} — {n} verdicts\n{'=' * 66}")
    print(f"errors / no verdict returned : {errors}")
    print(f"JSON contract violations     : {contract_bad}")
    print(f"tiers                        : {dict(tiers)}")
    print(f"score buckets                : {dict(sorted(scores.items(), key=str))}")
    print(f"\nFABRICATION (text-only posts; identifiers nulled by the gate)")
    print(f"  fields nulled              : {fabricated} across {fab_posts} posts")
    print(f"  rate                       : {fab_posts / n:.1%} of posts")
    print(f"\nPROMPT ECHO (target's own identifiers handed back): {echoes}")
    print(f"BODY-TYPE TRAP (شريحة etc. -> sedan model)        : {trap}")
    if trap:
        print("    (per-POST check, not per-line: on a mixed كشف list where one "
              "line really is an Accent and another line says شريحة, this "
              "flags a FALSE POSITIVE — read it before acting on it)")
    for x in trap_examples:
        print(f"    {x}")
    print(f"SUSPECTED TRUNCATION                              : {truncated}")
    print(f"MALFORMED VINs (17-char claims that cannot be)    : {malformed}")
    for x in malformed_examples:
        print(f"    {x}")
    for x in trunc_examples:
        print(f"    {x}")

    if bench_posts:
        recall = matched / (matched + missed) if (matched + missed) else 0
        print(f"\nRECALL vs the local pipeline ({bench_posts} posts with known answers)")
        print(f"  chassis matched            : {matched}")
        print(f"  chassis MISSED (by field)  : {missed}   (strict recall {recall:.1%})")
        true_recall = ((matched + misfiled) / (matched + missed)
                       if (matched + missed) else 0)
        print(f"    of those, read but filed elsewhere : {misfiled}")
        print(f"    of those, NOT READ AT ALL          : {unread}")
        print(f"  digits captured anywhere   : recall {true_recall:.1%}  "
              f"<- the number that matters")
        print(f"  chassis EXTRA              : {extra}   (may be real finds OR "
              f"invented — read them)")
        for x in missed_examples:
            print(f"    {x}")
        for x in extra_examples:
            print(f"    {x}")
    else:
        print("\nNo ground truth available for these posts (not a benchmark run).")

    # Nothing above can see a wrong reading that is well-formed. Print whole
    # verdicts against their source text so a person actually reads some.
    print(f"\n{'=' * 66}\nSAMPLE FOR READING BY EYE ({a.sample})\n{'=' * 66}")
    step = max(len(entries) // max(a.sample, 1), 1)
    for e in entries[::step][:a.sample]:
        pid, v = e["id"], e["verdict"]
        row = conn.execute("SELECT text FROM posts WHERE id = ?", (pid,)).fetchone()
        print(f"\n--- post {pid} | {e.get('url')}")
        print(f"TEXT: {((row['text'] if row else '') or '')[:700]}")
        truth, _ = truth_for(pid)
        print(f"KNOWN CHASSIS (local pipeline): {sorted(truth)}")
        print(f"MOONSHOT SAID: score={v.get('score')} tier={v.get('tier')}")
        print(f"  reasons: {str(v.get('reasons'))[:300]}")
        for veh in (v.get("vehicles") or [])[:12]:
            if isinstance(veh, dict):
                print(f"  - {veh.get('make')} {veh.get('model')} {veh.get('year')} "
                      f"{veh.get('color')} chassis={veh.get('chassis')} "
                      f"plate={veh.get('plate')} contact={veh.get('contact')}")
        if v.get("ungrounded_dropped"):
            print(f"  NULLED BY GATE: {v['ungrounded_dropped']}")


if __name__ == "__main__":
    main()
