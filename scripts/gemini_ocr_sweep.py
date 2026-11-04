"""Photo OCR on the Gemini FREE tier — the quality-first replacement for the
suspended Google Vision sweep. Benchmarked 2026-10-07 (llm_ocr_benchmark.py,
Vision's own reads as the answer key):

                         RapidOCR (local)   Flash-Lite, 6-10 photos/call
  Arabic-digit lists          60%                92-93%  (tail-6 94%)
  Latin-digit photos          41%                49-52%  (tail-6 71-73%)

The Latin figure understates it: checked by eye, Vision was the one wrong on
several disagreements (e.g. a phone number 09XXXXXXXX Vision read as
09XXXXXXXX). Batching 10 photos per call cost no accuracy; 20 per call DID —
text drifted between photos (Latin recall 26%, extras 91), and even 10 leaked
a neighbour's VIN live. BATCH is 6: zero measured leakage. See BATCH below.

    python scripts\\gemini_ocr_sweep.py --hours 2.5
    python scripts\\gemini_ocr_sweep.py --limit 50          # trial

QUOTA. Free-tier quotas are per model per day and are NOT taken on trust: the
job rotates MODELS and retires a model for the rest of the run on its first
429, recording what it learned in data/gemini_quota.json. The two models the
daily scoring lane uses (gemini-3.5-flash-lite, gemini-3.1-flash-lite) are
excluded by default so OCR and scoring cannot starve each other.

QUALITY GUARDS.
  * Alignment: a reply must be a JSON array with exactly one string per photo.
    Anything else is split in half and retried; never stored misaligned.
  * Second read: a photo whose transcription contains a VIN-like code (11+
    characters, letters AND digits) is read again by one of VERIFY_MODELS (~20 free/day each). Faint
    plates are where a language model guesses — the same faded VIN came back
    two different ways on two runs. Agreement -> engine tag '+verified'.
    Disagreement -> BOTH readings are stored, tag '+disagree': a guess is
    never presented as the only reading. Verifier quota spent -> stored with
    tag '+unverified', so nobody mistakes it for a checked read.
  * Not an image (HTML saved as .jpg etc.) -> ocr_error, never sent.

Re-reads rows the local RapidOCR engine wrote (ocr_engine LIKE 'rapidocr%').
Order: photos from recovery-list posts first, then newest. Stops on
data/PAUSED or data/PAUSED_LIGHT.

ONE READ PER IMAGE (2026-11-04). Files are hashed (media.sha1); an image is
read once and its text is copied to every byte-identical copy, and copies of
images already read get their text at zero calls. 75% of the unread backlog
was copies. A 503 spell rests the run and retries the same batch instead of
ending it. No database lock is held across an API call.
"""
import argparse
import base64
import datetime
import hashlib
import json
import re
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from openai import OpenAI

from carhunt import db
from carhunt import vin as vinlib
from carhunt.config import load_config

DATA = ROOT / "data"
DB = DATA / "carhunt.db"
LOG = DATA / "logs" / "gemini_ocr.log"
QUOTA = DATA / "gemini_quota.json"
BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"

# 6, not 10. Measured on the benchmark sample (neighbour's number appearing in
# a photo's read): 6/call 0 of 60 photos, 10/call 1 of 60, 20/call 20 of 80.
# The live 10/call trial also handed one photo its NEIGHBOUR'S full VIN
# (MR0AW12GX00002178); only the second read caught it. Never raise this
# without re-measuring leakage, not just recall — recall hid it.
BATCH = 6
VERBOSE = False
# flash-lite-latest is left out on purpose: it is an alias, very likely of a
# scoring-lane model, and would silently spend that lane's quota.
MODELS = ["gemini-3.1-flash-lite-preview"]
# Full-Flash models: ~20 free requests/day EACH (measured: 429 after ~20 on
# gemini-3-flash-preview, 2026-10-07). Rotated for VIN second reads only.
VERIFY_MODELS = ["gemini-3-flash-preview", "gemini-3.8-flash", "gemini-3.7-flash",
                 "gemini-3.6-flash", "gemini-3.5-flash"]
SCORING_MODELS = {"gemini-3.5-flash-lite", "gemini-3.1-flash-lite"}

PROMPT = (
    "You are given {k} separate images, numbered in order. For EACH image, "
    "transcribe ALL visible text exactly as written, line by line. Copy every "
    "number digit by digit; keep Arabic-Indic digits as Arabic-Indic and Latin "
    "digits as Latin. Do not translate, correct, complete or guess; write ? for "
    "an unreadable character. Never move text between images; an image with no "
    "text gets an empty string. Reply with ONLY a JSON array of {k} strings, "
    "one transcription per image, in order.")

FOUND_WORDS = ("كشف", "حصر", "لقيناها", "موجود", "الحوش", "عثور", "استلام")
FOLD = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
TOK = re.compile(r"[0-9A-Za-z٠-٩۰-۹]+")


def log(msg: str) -> None:
    line = f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def paused() -> bool:
    return (DATA / "PAUSED").exists() or (DATA / "PAUSED_LIGHT").exists()


def id_tokens(text: str) -> set[str]:
    out = set()
    for t in TOK.findall(text or ""):
        t = t.translate(FOLD).upper()
        if len(t) >= 5 and any(c.isdigit() for c in t):
            out.add(t)
    return out


def vin_like(text: str) -> bool:
    return any(len(t) >= 11 and any(c.isalpha() for c in t) for t in id_tokens(text))


def mime_of(data: bytes) -> str | None:
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return None


class QuotaHit(Exception):
    pass


class Unavailable(Exception):
    """503 "high demand" / timeouts that outlast the retries. Not a quota:
    nothing is recorded, the model is just skipped for this run."""


class Reader:
    def __init__(self, models: list[str], rpm: float):
        key = load_config()["gemini"]["api_key"]
        self.client = OpenAI(api_key=key, base_url=BASE_URL, timeout=120, max_retries=0)
        self.models = list(models)
        self.gap = 60.0 / rpm
        self.last = 0.0
        self.calls: dict[str, int] = {}
        self.quota = json.loads(QUOTA.read_text("utf-8")) if QUOTA.exists() else {}

    def _call(self, model: str, images: list[tuple[str, bytes]]) -> list[str] | None:
        """One request. Returns the per-image texts, None on a bad shape;
        raises QuotaHit on 429."""
        wait = self.gap - (time.time() - self.last)
        if wait > 0:
            time.sleep(wait)
        self.last = time.time()
        content = [{"type": "text", "text": PROMPT.format(k=len(images))}]
        for i, (mime, data) in enumerate(images, 1):
            content += [{"type": "text", "text": f"Image {i}:"},
                        {"type": "image_url", "image_url": {
                            "url": f"data:{mime};base64,{base64.b64encode(data).decode()}"}}]
        verifier = model in VERIFY_MODELS
        attempts = 1 if verifier else 3
        for attempt in range(attempts):
            try:
                extra = {}
                if verifier:
                    # a thinking model: at default effort a 5-photo call timed
                    # out at 120 s; at "low" one photo takes 2-3 s, and it
                    # marks unreadable characters with ? instead of guessing.
                    # One short attempt: a second opinion is not worth minutes.
                    extra["reasoning_effort"] = "low"
                    extra["timeout"] = 45
                r = self.client.chat.completions.create(
                    model=model, temperature=0,
                    messages=[{"role": "user", "content": content}], **extra)
                self.calls[model] = self.calls.get(model, 0) + 1
                if VERBOSE:
                    log(f"  {model} x{len(images)}: {time.time() - self.last:.1f}s")
                txt = (r.choices[0].message.content or "").strip()
                txt = re.sub(r"^```(?:json)?\s*|\s*```$", "", txt).strip()
                out = json.loads(txt)
                if isinstance(out, list) and len(out) == len(images):
                    return [str(x or "") for x in out]
                return None
            except json.JSONDecodeError:
                return None
            except Exception as e:
                msg = str(e)
                log(f"  {model} x{len(images)}: {type(e).__name__} after "
                    f"{time.time() - self.last:.0f}s: {msg[:160]}")
                if "429" in msg and "PerMinute" in msg:
                    # a per-MINUTE limit is not the daily quota: treating it as
                    # one retired gemini-3.5-flash for the day after 12 calls
                    if attempt < attempts - 1:
                        time.sleep(65)
                        continue
                    raise Unavailable(f"{model}: per-minute limit")
                if "429" in msg:
                    raise QuotaHit(msg[:1500])
                if "503" in msg or "500" in msg or "timed out" in msg.lower():
                    if attempt < attempts - 1:
                        time.sleep(30 * (attempt + 1))
                        continue
                    raise Unavailable(f"{model}: {msg[:300]}")
                raise

    def read(self, images: list[tuple[str, bytes]], model: str | None = None) -> tuple[list[str], str]:
        """Texts for `images` and the model used. Splits on a bad shape;
        rotates models on quota. Raises QuotaHit when every model is spent."""
        while True:
            if model is None and not self.models:
                raise QuotaHit("all OCR models exhausted")
            m = model or self.models[0]
            try:
                out = self._call(m, images)
            except QuotaHit as q:
                self._record_429(m, str(q))
                if model is not None:
                    raise
                self.models.pop(0)
                log(f"  {m}: quota reached after {self.calls.get(m, 0)} calls this run — "
                    f"{'switching to ' + self.models[0] if self.models else 'no models left'}")
                continue
            if out is not None:
                return out, m
            if len(images) == 1:
                return [None], m      # None = could not align; never stored as blank
            half = len(images) // 2
            a, _ = self.read(images[:half], model)
            b, _ = self.read(images[half:], model)
            return a + b, m

    def _record_429(self, model: str, msg: str) -> None:
        day = datetime.date.today().isoformat()
        self.quota.setdefault(model, {})[day] = {
            "calls_before_429": self.calls.get(model, 0), "message": msg[:1500]}
        QUOTA.write_text(json.dumps(self.quota, indent=1), encoding="utf-8")


COOLDOWN = 600   # seconds a busy (503 / timeout) verifier sits out


class Verifier:
    """Second reads for VIN-like photos, rotating VERIFY_MODELS.

    A quota 429 retires a model for the run (quota is daily). A 503 or a
    timeout only benches it for COOLDOWN: the free tier's "high demand" spells
    are minutes long, and retiring on the first one turned 18 of 72 photos
    into unverified rows in the first scheduled run.
    """

    def __init__(self, reader: Reader, models: list[str]):
        self.reader = reader
        self.models = list(models)
        self.bench: dict[str, float] = {}

    def available(self) -> list[str]:
        now = time.time()
        return [m for m in self.models if self.bench.get(m, 0) <= now]

    def second_read(self, image: tuple[str, bytes]) -> tuple[str, str] | None:
        for m in self.available():
            try:     # one photo per call: see Reader._call
                out, _ = self.reader.read([image], model=m)
            except QuotaHit:
                self.models.remove(m)
                log(f"  {m}: verify quota reached{'' if self.models else ' — no verifiers left today'}")
                continue
            except Unavailable:
                self.bench[m] = time.time() + COOLDOWN
                log(f"  {m}: busy — benched {COOLDOWN // 60} min")
                continue
            return (out[0], m) if out[0] is not None else None
        return None


def compare(first: str, second: str) -> bool:
    """Do two reads agree on every VIN-length code?"""
    va = {t for t in id_tokens(first) if len(t) >= 11}
    vb = {t for t in id_tokens(second) if len(t) >= 11}
    return va == vb


def catch_up(conn, verifier: Verifier, deadline) -> None:
    """Spend spare verifier quota on photos stored '+unverified' earlier.

    Verifier quota is daily and small (~20/model), so it goes to the reads
    most likely to be WRONG first (carhunt/vin.second_read_risk): a Toyota or
    Hyundai VIN failing its check digit, then I/O/Q, then weak families, and
    passing reads last. Measured: verified reads pass the check digit 99% of
    the time, single reads 92%. A verdict lands on every byte-identical copy.
    """
    rows = conn.execute(
        "SELECT min(path), group_concat(id), max(ocr_text), max(ocr_engine), max(post_id) "
        "FROM media WHERE ocr_engine LIKE '%+unverified' "
        "GROUP BY COALESCE(NULLIF(sha1, ''), path)").fetchall()
    if not rows:
        return
    rows.sort(key=lambda r: (vinlib.second_read_risk(r[2]), -(r[4] or 0)))
    ok = bad = 0
    for path, ids, text, engine, _ in rows:
        if not verifier.available() or (deadline and time.time() > deadline) or paused():
            break
        try:
            data = Path(path).read_bytes()
        except OSError:
            continue
        got = verifier.second_read((mime_of(data) or "image/jpeg", data))
        if not got:
            continue
        vtext, vmodel = got
        base = engine.replace("+unverified", "")
        if compare(text, vtext):
            new_text, tag = text, base + "+verified"
            ok += 1
        else:
            new_text = f"{text}\n--- second read ({vmodel}) ---\n{vtext}"
            tag = base + "+disagree"
            bad += 1
        with db.write_tx() as w:
            w.executemany("UPDATE media SET ocr_text=?, ocr_engine=? WHERE id=?",
                          [(new_text, tag, int(i)) for i in ids.split(",")])
    if ok or bad:
        log(f"catch-up: {ok} earlier VIN reads now verified, {bad} disagree "
            f"({len(rows) - ok - bad} still unverified)")


# ---- byte-identical copies ---------------------------------------------------
# The same photo is cross-posted to many groups: measured 2026-11-04, 179,191
# unread files held only 44,252 distinct images, and 29,510 were copies of a
# photo ALREADY read. Identical bytes give identical text, so each image is
# read once and its text lands on every copy -- quality-neutral, zero calls.

def ensure_schema() -> None:
    with db.write_tx() as w:
        cols = {r[1] for r in w.execute("PRAGMA table_info(media)")}
        if "ocr_engine" not in cols:
            w.execute("ALTER TABLE media ADD COLUMN ocr_engine TEXT")
        if "sha1" not in cols:
            w.execute("ALTER TABLE media ADD COLUMN sha1 TEXT")
        w.execute("CREATE INDEX IF NOT EXISTS idx_media_sha1 ON media(sha1)")


def hash_new_files(conn, deadline=None, chunk: int = 500) -> int:
    """sha1 of every media file not hashed yet ('' = file missing)."""
    paths = [r[0] for r in conn.execute(
        "SELECT DISTINCT path FROM media WHERE sha1 IS NULL")]
    done = 0
    for i in range(0, len(paths), chunk):
        if paused() or (deadline and time.time() > deadline):
            break
        batch = []
        for p in paths[i:i + chunk]:
            try:
                batch.append((hashlib.sha1(Path(p).read_bytes()).hexdigest(), p))
            except OSError:
                batch.append(("", p))
        with db.write_tx() as w:
            w.executemany("UPDATE media SET sha1 = ? WHERE path = ? AND sha1 IS NULL", batch)
        done += len(batch)
    if done:
        log(f"hashed {done} file(s){'' if done == len(paths) else f' of {len(paths)}'}")
    return done


PROPAGATE_SQL = """
WITH best AS (
  SELECT sha1, ocr_text, ocr_engine FROM (
    SELECT sha1, ocr_text, ocr_engine, ROW_NUMBER() OVER (
             PARTITION BY sha1
             ORDER BY (ocr_engine LIKE '%+verified') DESC,
                      (ocr_engine LIKE '%+disagree') DESC, ocr_at DESC) AS rn
      FROM media
     WHERE sha1 IS NOT NULL AND sha1 <> '' AND ocr_at IS NOT NULL AND ocr_error IS NULL
       AND COALESCE(ocr_engine, '') NOT LIKE 'rapidocr%')
   WHERE rn = 1)
UPDATE media
   SET ocr_text = best.ocr_text, ocr_engine = best.ocr_engine, ocr_at = ?, ocr_error = NULL
  FROM best
 WHERE media.sha1 = best.sha1
   AND (media.ocr_at IS NULL OR media.ocr_engine LIKE 'rapidocr%')
"""


def propagate_twins() -> int:
    """Give every unread copy of an already-read image that image's text."""
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with db.write_tx() as w:
        before = w.total_changes      # rowcount is -1 for a statement starting WITH
        w.execute(PROPAGATE_SQL, (now,))
        n = w.total_changes - before
    if n:
        log(f"copied text to {n} byte-identical copies of photos already read (0 calls)")
    return n


def pending(conn, limit: int) -> list[tuple[str, list[int]]]:
    """One entry per distinct image still to read: (a path, every media id
    carrying those bytes). Recovery-list posts first, then newest."""
    found = " OR ".join(f"p.text LIKE '%{w}%'" for w in FOUND_WORDS)
    rows = conn.execute(
        "SELECT min(m.path), group_concat(m.id), max(CASE WHEN " + found + " THEN 1 ELSE 0 END),"
        " max(m.post_id) FROM media m JOIN posts p ON p.id = m.post_id "
        "WHERE m.ocr_at IS NULL OR m.ocr_engine LIKE 'rapidocr%' "
        "GROUP BY COALESCE(NULLIF(m.sha1, ''), m.path) ORDER BY 3 DESC, 4 DESC"
        + (f" LIMIT {limit}" if limit else "")
    ).fetchall()
    return [(p, [int(i) for i in ids.split(",")]) for p, ids, _, _ in rows]


# A 503 "high demand" spell used to END the run: 67 of 85 runs stopped early
# and the primary model used 18% of its daily quota. Now the run rests and
# retries the same batch; only the DAILY quota (QuotaHit) or a long outage stops it.
PRIMARY_REST = 300
MAX_BUSY_IN_A_ROW = 6


def rest(seconds: float, deadline) -> bool:
    """Sleep, waking early for a pause or the deadline. False = stop the run."""
    end = time.time() + seconds
    while time.time() < end:
        if paused() or (deadline and time.time() > deadline):
            return False
        time.sleep(min(15, end - time.time()))
    return True


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=0)
    ap.add_argument("--limit", type=int, default=0, help="files")
    ap.add_argument("--rpm", type=float, default=12)
    ap.add_argument("--models", nargs="+", default=MODELS)
    ap.add_argument("--no-verify", action="store_true")
    ap.add_argument("--verbose", action="store_true", help="log every call's latency")
    args = ap.parse_args()
    global VERBOSE
    VERBOSE = args.verbose

    if paused():
        log("paused — nothing done")
        return
    clash = SCORING_MODELS & set(args.models)
    if clash:
        log(f"WARNING: {sorted(clash)} is also used by the daily scoring lane")

    deadline = time.time() + args.hours * 3600 if args.hours else None
    ensure_schema()
    # reads only; every write goes through db.write_tx AFTER the API calls, so
    # no lock is ever held across a Gemini request (one used to sit for 222 s)
    conn = sqlite3.connect(DB, timeout=60, isolation_level=None)
    conn.execute("PRAGMA busy_timeout=60000")
    hash_new_files(conn, deadline)
    propagate_twins()

    work = pending(conn, args.limit)
    log(f"start: {len(work)} distinct images to read, models {args.models}, verify "
        f"{'off' if args.no_verify else VERIFY_MODELS}")
    reader = Reader(args.models, args.rpm)
    verifier = Verifier(reader, [] if args.no_verify else VERIFY_MODELS)
    done = errs = verified = disagree = blank = unverified = 0
    t0 = time.time()
    now = lambda: datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731

    def flush(updates: list[tuple]) -> None:
        if updates:
            with db.write_tx() as w:
                w.executemany("UPDATE media SET ocr_text=?, ocr_at=?, ocr_error=?, ocr_engine=? "
                              "WHERE id=?", updates)

    if not args.no_verify:
        catch_up(conn, verifier, deadline)

    busy_in_a_row = 0
    try:
        start = 0
        while start < len(work):
            if paused():
                log("pause flag seen — stopping")
                break
            if deadline and time.time() > deadline:
                log("time limit reached")
                break
            group = work[start:start + BATCH]
            updates, images, slots = [], [], []
            for path, rows in group:
                try:
                    data = Path(path).read_bytes()
                except OSError:
                    updates += [("", now(), "file missing", "gemini-ocr", i) for i in rows]
                    errs += 1
                    continue
                mime = mime_of(data)
                if not mime:
                    updates += [("", now(), "not an image", "gemini-ocr", i) for i in rows]
                    errs += 1
                    continue
                images.append((mime, data))
                slots.append((path, rows))
            if not images:
                flush(updates)
                start += BATCH
                continue

            try:
                texts, model = reader.read(images)
            except Unavailable as u:
                busy_in_a_row += 1
                if busy_in_a_row > MAX_BUSY_IN_A_ROW:
                    flush(updates)
                    raise
                log(f"  primary busy ({str(u)[:120]}): resting {PRIMARY_REST // 60} min, "
                    f"then the same batch again ({busy_in_a_row}/{MAX_BUSY_IN_A_ROW})")
                flush(updates)
                if not rest(PRIMARY_REST, deadline):
                    break
                continue                      # same `start`: nothing is skipped
            busy_in_a_row = 0
            # second read for VIN-like photos, by a different, stronger model
            check = [i for i, t in enumerate(texts) if t is not None and vin_like(t)]
            second = {}
            for i in check:
                got = verifier.second_read(images[i])
                if got:
                    second[i] = got
            for i, ((path, rows), text) in enumerate(zip(slots, texts)):
                tag = f"gemini-ocr:{model}"
                if i in check and i not in second:
                    tag += "+unverified"
                    unverified += 1
                if i in second:
                    vtext, vmodel = second[i]
                    if compare(text, vtext):
                        tag += "+verified"
                        verified += 1
                    else:
                        text = f"{text}\n--- second read ({vmodel}) ---\n{vtext}"
                        tag += "+disagree"
                        disagree += 1
                if text is None:
                    updates += [("", now(), "unalignable reply", tag, r) for r in rows]
                    errs += 1
                    continue
                updates += [(text, now(), None, tag, r) for r in rows]
                blank += not text.strip()
                done += 1
            flush(updates)
            start += BATCH
            if (start // BATCH) % 20 == 0:
                log(f"  {done} read, {verified} VINs verified, {disagree} disagree, "
                    f"{blank} blank, {errs} errors, calls {reader.calls}")
    except (QuotaHit, Unavailable) as q:
        log(f"stopping: {q}")
    mins = (time.time() - t0) / 60
    log(f"done: {done} images read in {mins:.1f} min, {verified} VINs verified, "
        f"{disagree} disagreements kept both reads, {unverified} VINs unverified, "
        f"{blank} blank, {errs} errors; "
        f"calls {reader.calls}")
    copied = propagate_twins()      # copies that arrived while this run was reading
    if copied:
        log(f"after the run: {copied} more copies filled")


if __name__ == "__main__":
    if paused():
        main()                       # logs "paused" and does nothing
    else:
        from carhunt import deadman
        with deadman.tracked("photo_ocr", "photo-ocr"):
            main()
