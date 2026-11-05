"""SQLite storage for collected posts and match verdicts.

Concurrency, learned the hard way (2026-11): several processes write this one
file (collectors, the scoring lanes, the OCR sweep, the hourly sweep). Every
write goes through `write_tx()` -- BEGIN IMMEDIATE, short, retried, and the
connection CLOSED afterwards. A deferred transaction that reads and then writes
fails instantly with "database is locked" in WAL mode (busy_timeout is not
consulted for that upgrade), and that is what killed the daily Gemini lane on
5 of its 6 runs before 2026-11-04. Never hold a write transaction across a
network call.
"""
import contextlib
import datetime
import json
import os
import random
import sqlite3
import sys
import time

from .config import DATA_DIR

DB_PATH = DATA_DIR / "carhunt.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
    id          INTEGER PRIMARY KEY,
    source      TEXT NOT NULL,           -- 'telegram:@channel', 'intake', 'facebook', ...
    source_ref  TEXT NOT NULL UNIQUE,    -- stable dedup key, e.g. 'tg:@channel:12345'
    url         TEXT,
    text        TEXT,
    seen_at     TEXT NOT NULL,
    score       INTEGER,                 -- 0-100, NULL until analyzed
    verdict     TEXT                     -- full JSON verdict from the analyzer
);
CREATE TABLE IF NOT EXISTS media (
    id      INTEGER PRIMARY KEY,
    post_id INTEGER NOT NULL REFERENCES posts(id),
    path    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_posts_score ON posts(score);
CREATE TABLE IF NOT EXISTS vehicles (
    id           INTEGER PRIMARY KEY,
    post_id      INTEGER NOT NULL REFERENCES posts(id),
    make         TEXT,
    model        TEXT,
    color        TEXT,
    year         TEXT,
    chassis      TEXT,                -- as written in the post
    chassis_norm TEXT,                -- normalized (see normalize.py)
    plate        TEXT,
    plate_norm   TEXT,
    status       TEXT,                -- spotted | for_sale | seeking_owner | recovered | unknown
    location     TEXT,
    contact      TEXT,
    description  TEXT,
    extracted_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_vehicles_chassis ON vehicles(chassis_norm);
CREATE INDEX IF NOT EXISTS idx_vehicles_plate ON vehicles(plate_norm);
-- Every save replaces a post's rows (DELETE ... WHERE post_id = ?). Without
-- this index that DELETE scanned 1.26M rows while holding the write lock.
CREATE INDEX IF NOT EXISTS idx_vehicles_post ON vehicles(post_id);
CREATE INDEX IF NOT EXISTS idx_media_post ON media(post_id);
"""


def connect() -> sqlite3.Connection:
    """A connection for callers that manage it themselves.

    Inside this module, reads use `_reader()` and writes use `write_tx()`, both
    of which close the connection; `with connect() as conn:` alone commits but
    never closes, so handles leaked until garbage collection.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def _reader():
    return contextlib.closing(connect())


LOCK_LOG = DATA_DIR / "logs" / "db_locks.log"


def _log_lock(message: str) -> None:
    """One line per slow lock, naming the process -- how a long holder is found."""
    try:
        LOCK_LOG.parent.mkdir(parents=True, exist_ok=True)
        who = os.path.basename(sys.argv[0] or "?") if sys.argv else "?"
        stamp = datetime.datetime.now().isoformat(timespec="seconds")
        with open(LOCK_LOG, "a", encoding="utf-8") as f:
            f.write(f"{stamp} pid={os.getpid()} {who}: {message}\n")
    except Exception:
        pass  # logging must never break a write


@contextlib.contextmanager
def write_tx(attempts: int = 5, busy_ms: int = 30000):
    """BEGIN IMMEDIATE ... COMMIT on a fresh connection that is always closed.

    Only BEGIN is retried: once it succeeds SQLite guarantees no SQLITE_BUSY
    until COMMIT. Keep the block short and never put an API call inside it.
    Worst case before giving up is ~3 minutes of waiting, then the error.
    """
    for attempt in range(attempts):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(DB_PATH, timeout=busy_ms / 1000, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout={busy_ms}")
        t0 = time.monotonic()
        try:
            conn.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as exc:
            conn.close()
            busy = (getattr(exc, "sqlite_errorcode", 5) & 0xFF) == sqlite3.SQLITE_BUSY
            if not busy or attempt == attempts - 1:
                raise
            _log_lock(f"BEGIN IMMEDIATE busy after {time.monotonic() - t0:.1f}s, "
                      f"retry {attempt + 1}")
            time.sleep(min(60, 2 ** attempt) + random.random())
            continue
        waited = time.monotonic() - t0
        try:
            yield conn
            conn.execute("COMMIT")
        except BaseException:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        finally:
            held = time.monotonic() - t0 - waited
            if waited > 5 or held > 1:
                _log_lock(f"write txn waited {waited:.1f}s held {held:.2f}s")
            conn.close()
        return


def init() -> None:
    with _reader() as conn:
        conn.executescript(SCHEMA)
        for ddl in ("ALTER TABLE posts ADD COLUMN extracted INTEGER DEFAULT 0",
                    "ALTER TABLE vehicles ADD COLUMN model_key TEXT",
                    "ALTER TABLE posts ADD COLUMN content_hash TEXT",
                    # Why a whole column rather than a note in `description`:
                    # this has to survive into the PUBLISHED payload and change
                    # how a row reads. A worn GIAD plate where two independent
                    # readers disagree on four characters must not be shown to
                    # a searching family as a precise chassis number. Holds the
                    # reason, so the caveat travels with the doubt.
                    "ALTER TABLE vehicles ADD COLUMN low_confidence TEXT",
                    "CREATE INDEX IF NOT EXISTS idx_posts_hash ON posts(content_hash)",
                    # Photo OCR. These used to be added only by the old Vision
                    # sweep script, so a fresh install had no ocr_text at all.
                    # media.ocr_text is DATABASE-ONLY: it holds registration-card
                    # text (owner names, addresses) and is never exported.
                    "ALTER TABLE media ADD COLUMN ocr_text TEXT",
                    "ALTER TABLE media ADD COLUMN ocr_at TEXT",
                    "ALTER TABLE media ADD COLUMN ocr_error TEXT",
                    "ALTER TABLE media ADD COLUMN ocr_engine TEXT",
                    "ALTER TABLE media ADD COLUMN sha1 TEXT",
                    "CREATE INDEX IF NOT EXISTS idx_media_ocr ON media(ocr_at)",
                    "CREATE INDEX IF NOT EXISTS idx_media_sha1 ON media(sha1)"):
            try:
                conn.execute(ddl)
            except sqlite3.OperationalError as exc:
                # The ONLY expected failure is "already there". Anything else
                # -- "database is locked" above all -- used to be swallowed
                # here, leaving a service running on a half-migrated schema.
                if "duplicate column" not in str(exc) and "already exists" not in str(exc):
                    raise
        conn.commit()


def has_post(source_ref: str) -> bool:
    with _reader() as conn:
        row = conn.execute(
            "SELECT 1 FROM posts WHERE source_ref = ?", (source_ref,)
        ).fetchone()
    return row is not None


def has_text_prefix(source: str, prefix: str) -> bool:
    """Is a LONGER post from this source already stored that starts with `prefix`?"""
    with _reader() as conn:
        row = conn.execute(
            "SELECT 1 FROM posts WHERE source = ? AND length(text) > ? "
            "AND substr(text, 1, ?) = ? LIMIT 1",
            (source, len(prefix), len(prefix), prefix)).fetchone()
    return row is not None


def fill_url(source_ref: str, url: str) -> bool:
    """Set the url of a stored post that has none. True if a row changed."""
    with write_tx() as conn:
        cur = conn.execute(
            "UPDATE posts SET url = ? WHERE source_ref = ? AND url IS NULL",
            (url, source_ref))
    return cur.rowcount > 0


def add_post(source: str, source_ref: str, url: str | None, text: str | None,
             media_paths: list[str] | None = None) -> int | None:
    """Insert a post; returns its id, or None if already stored."""
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with write_tx() as conn:
        if conn.execute("SELECT 1 FROM posts WHERE source_ref = ?", (source_ref,)).fetchone():
            return None
        try:
            cur = conn.execute(
                "INSERT INTO posts (source, source_ref, url, text, seen_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (source, source_ref, url, text, now),
            )
        except sqlite3.IntegrityError:
            return None
        post_id = cur.lastrowid
        for path in media_paths or []:
            conn.execute(
                "INSERT INTO media (post_id, path) VALUES (?, ?)", (post_id, str(path))
            )
    return post_id


_CFG = None


def _cap_alert(post_id: int, verdict: dict) -> None:
    """Apply `prefilter.cap_unbacked_alert` using this post's text + photo OCR."""
    global _CFG
    score = verdict.get("score")
    if not isinstance(score, (int, float)):
        return
    if _CFG is None:
        from .config import load_config
        _CFG = load_config()
    threshold = _CFG["matching"]["alert_threshold"]
    if score < threshold:
        return
    from .prefilter import cap_unbacked_alert
    with _reader() as conn:
        row = conn.execute("SELECT text FROM posts WHERE id = ?", (post_id,)).fetchone()
        ocr = conn.execute(
            "SELECT group_concat(ocr_text, char(10)) FROM media WHERE post_id = ? "
            "AND ocr_text <> ''", (post_id,)).fetchone()[0]
    text = "\n".join(x for x in ((row["text"] if row else None), ocr) if x)
    cap_unbacked_alert(verdict, text, _CFG["car"], threshold)


def set_verdict(post_id: int, verdict: dict) -> None:
    """Store a verdict. EVERY lane writes through here, so the alert cap lives
    here too: a score at or above `alert_threshold` with no identifier of the
    target behind it is held at threshold - 1 (see
    `prefilter.cap_unbacked_alert`). The verdict dict is capped IN PLACE, so a
    caller that checks the score afterwards (the live Telegram alert) sees the
    capped value and does not alert on resemblance alone.
    """
    _cap_alert(post_id, verdict)
    with write_tx() as conn:
        _write_verdict(conn, post_id, verdict)


def _write_verdict(conn, post_id: int, verdict: dict) -> None:
    conn.execute(
        "UPDATE posts SET score = ?, verdict = ? WHERE id = ?",
        (verdict.get("score"), json.dumps(verdict, ensure_ascii=False), post_id),
    )


def save_analysis(post_id: int, verdict: dict) -> None:
    """Store the match verdict AND any vehicle records it extracted, atomically.

    Only a verdict that actually produced a vehicles list is authoritative:
    a failed call (score None) must never wipe rows or mark the post done.
    Verdict and rows commit together: as two transactions, a lock between them
    left post 171728 scored with no rows, and no lane retries a scored post.
    """
    _cap_alert(post_id, verdict)
    rows = None
    if (not verdict.get("vehicles_copied_from_duplicate")   # rows live on the original
            and verdict.get("score") is not None):          # failed call: keep what is stored
        vehicles = verdict.get("vehicles")
        if isinstance(vehicles, list):
            entries = [v for v in vehicles if isinstance(v, dict)]
            gate(post_id, entries)          # mutates the verdict's own entries too
            rows = _vehicle_rows(post_id, entries)
    with write_tx() as conn:
        _write_verdict(conn, post_id, verdict)
        if rows is not None:
            _write_vehicles(conn, post_id, rows)


def add_vehicles(post_id: int, entries: list[dict]) -> int:
    """Replace this post's vehicle rows with `entries`; returns count stored.

    Replace, not append: analysis can legitimately run again (a re-check, a
    resubmitted batch, a provider fallback), and appending would duplicate
    every vehicle each time. An empty list is authoritative — it means the
    post genuinely holds no vehicles — so it still marks the post extracted.
    """
    gate(post_id, entries)
    rows = _vehicle_rows(post_id, entries)
    with write_tx() as conn:
        _write_vehicles(conn, post_id, rows)
    return len(entries)


GATE_LOG = DATA_DIR / "logs" / "gate.log"


def gate(post_id: int, entries: list[dict]) -> list[str]:
    """The grounding gate (carhunt/gates.py) for EVERY lane, against the post's
    text plus its photo OCR. Read outside any write transaction."""
    if not entries:
        return []
    from .gates import check_entries
    with _reader() as conn:
        row = conn.execute("SELECT text FROM posts WHERE id = ?", (post_id,)).fetchone()
        media = conn.execute(
            "SELECT ocr_text, ocr_at, COALESCE(ocr_engine, '') FROM media WHERE post_id = ?",
            (post_id,)).fetchall()
    doubtful = ("+disagree", "+unverified")
    trusted = [m[0] for m in media if m[0] and not m[2].endswith(doubtful)]
    shaky = [m[0] for m in media if m[0] and m[2].endswith(doubtful)]
    text = "\n".join(x for x in [(row["text"] if row else None), *trusted] if x)
    changes = check_entries(entries, text, photos_unread=any(m[1] is None for m in media),
                            doubtful_text="\n".join(shaky))
    if changes:
        try:
            GATE_LOG.parent.mkdir(parents=True, exist_ok=True)
            fields = sorted({c.split("=")[0].split(":")[0] for c in changes})
            with open(GATE_LOG, "a", encoding="utf-8") as f:
                f.write(f"{datetime.datetime.now().isoformat(timespec='seconds')} "
                        f"post {post_id}: {len(changes)} change(s) {fields}\n")
        except OSError:
            pass
    return changes


_INSERT_VEHICLE = (
    "INSERT INTO vehicles (post_id, make, model, color, year, chassis, "
    "chassis_norm, plate, plate_norm, status, location, contact, "
    "description, extracted_at, model_key, low_confidence) "
    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)")


def _vehicle_rows(post_id: int, entries: list[dict]) -> list[tuple]:
    """Row tuples, built OUTSIDE the write lock (normalising is not free).

    `low_confidence` is carried through: before 2026-11 every re-ingest wrote
    it as NULL, silently erasing the doubt a human or a second reader recorded.
    """
    from .aliases import model_key_for
    from .normalize import norm_chassis, norm_plate

    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    return [
        (post_id, e.get("make"), e.get("model"), e.get("color"),
         str(e.get("year") or "") or None,
         e.get("chassis"), norm_chassis(e.get("chassis") or "") or None,
         e.get("plate"), norm_plate(e.get("plate") or "") or None,
         e.get("status"), e.get("location"), e.get("contact"),
         e.get("description"), now,
         model_key_for(e.get("make"), e.get("model"), e.get("description")),
         e.get("low_confidence") or None)
        for e in entries]


def _write_vehicles(conn, post_id: int, rows: list[tuple]) -> None:
    conn.execute("DELETE FROM vehicles WHERE post_id = ?", (post_id,))
    conn.executemany(_INSERT_VEHICLE, rows)
    conn.execute("UPDATE posts SET extracted = 1 WHERE id = ?", (post_id,))




# One car can be recorded many times: the same recovery list is reposted
# across groups, and one chassis in this corpus appears in 73 separate posts.
# Every record is KEPT — a family searching their chassis must see every
# sighting, with its own date, source and link. But a headline count of
# records answers "how much did we scrape", not "how many cars are
# catalogued", which is the number a reader actually wants. So both are
# reported, and this is the single definition both the dashboard and the
# exported page use so they cannot drift apart.
DISTINCT_VEHICLES_SQL = """
    SELECT
      (SELECT COUNT(DISTINCT chassis_norm) FROM vehicles
         WHERE chassis_norm IS NOT NULL AND chassis_norm != '')
    + (SELECT COUNT(DISTINCT plate_norm) FROM vehicles
         WHERE (chassis_norm IS NULL OR chassis_norm = '')
           AND plate_norm IS NOT NULL AND plate_norm != '')
"""
# rows carrying neither a chassis nor a plate cannot be deduplicated at all,
# so they are counted separately rather than silently inflating either figure
UNIDENTIFIED_SQL = """
    SELECT COUNT(*) FROM vehicles
     WHERE (chassis_norm IS NULL OR chassis_norm = '')
       AND (plate_norm IS NULL OR plate_norm = '')
"""


def registry_stats() -> dict:
    """Coverage figures for the published page."""
    with _reader() as conn:
        posts = conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
        analyzed = conn.execute(
            "SELECT COUNT(*) FROM posts WHERE score IS NOT NULL").fetchone()[0]
        photos = conn.execute("SELECT COUNT(*) FROM media").fetchone()[0]
        vehicles = conn.execute("SELECT COUNT(*) FROM vehicles").fetchone()[0]
        distinct = conn.execute(DISTINCT_VEHICLES_SQL).fetchone()[0]
        unidentified = conn.execute(UNIDENTIFIED_SQL).fetchone()[0]
        rows = conn.execute(
            "SELECT source, COUNT(*) AS n FROM posts GROUP BY source ORDER BY n DESC"
        ).fetchall()
    return {
        "posts": posts,
        "analyzed": analyzed,
        "photos": photos,
        "vehicles": vehicles,              # records / sightings — all kept
        "distinct_vehicles": distinct,     # deduplicated by identifier
        "unidentified": unidentified,      # no chassis and no plate
        "sources": [{"name": r["source"], "posts": r["n"]} for r in rows],
    }


def post_with_media(post_id: int) -> dict | None:
    with _reader() as conn:
        row = conn.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()
        if not row:
            return None
        item = dict(row)
        item["media"] = [
            m["path"] for m in conn.execute(
                "SELECT path FROM media WHERE post_id = ?", (post_id,)).fetchall()
        ]
    return item


def all_vehicles() -> list[dict]:
    """Every vehicle record joined with its source post."""
    with _reader() as conn:
        rows = conn.execute(
            "SELECT v.*, p.source, p.url, p.seen_at, "
            "  EXISTS (SELECT 1 FROM media m WHERE m.post_id = p.id) AS from_photo "
            "FROM vehicles v JOIN posts p ON p.id = v.post_id ORDER BY v.id"
        ).fetchall()
    return [dict(r) for r in rows]


def content_hash(text: str | None) -> str | None:
    """Stable hash of a post's text, ignoring whitespace/punctuation noise.

    The same كشف list is cross-posted to many groups; hashing lets us reuse
    one verdict instead of paying to analyze every copy.
    """
    import hashlib
    import re
    if not text:
        return None
    normalized = re.sub(r"[^0-9A-Za-z؀-ۿ]", "", text)
    if len(normalized) < 40:
        return None  # too short to be confident it's the same post
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:32]


def verdict_for_hash(chash: str, exclude_post_id: int) -> dict | None:
    """An existing scored verdict for identical content, if any."""
    if not chash:
        return None
    with _reader() as conn:
        # only reuse from another TEXT-ONLY post; a verdict formed with images
        # says nothing about a different post's images
        row = conn.execute(
            "SELECT p.verdict FROM posts p WHERE p.content_hash = ? AND p.id != ? "
            "AND p.score IS NOT NULL AND p.verdict IS NOT NULL "
            "AND NOT EXISTS (SELECT 1 FROM media m WHERE m.post_id = p.id) LIMIT 1",
            (chash, exclude_post_id),
        ).fetchone()
    if not row:
        return None
    try:
        return json.loads(row["verdict"])
    except json.JSONDecodeError:
        return None


def set_content_hash(post_id: int, chash: str | None) -> None:
    with write_tx() as conn:
        conn.execute("UPDATE posts SET content_hash = ? WHERE id = ?", (chash, post_id))


def unanalyzed(limit: int = 500, newest_first: bool = False) -> list[dict]:
    """Posts stored but not yet scored (e.g. collected before the API key was set).

    `newest_first` is for a lane that cannot keep up with intake (the free
    tier clears ~800/day against ~3,000 arriving): oldest-first would never
    reach a post collected this month.
    """
    order = "DESC" if newest_first else "ASC"
    with _reader() as conn:
        rows = conn.execute(
            f"SELECT * FROM posts WHERE score IS NULL ORDER BY id {order} LIMIT ?",
            (limit,)).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["media"] = [
                m["path"]
                for m in conn.execute(
                    "SELECT path FROM media WHERE post_id = ?", (row["id"],)
                ).fetchall()
            ]
            result.append(item)
    return result

