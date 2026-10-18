"""SQLite storage for collected posts and match verdicts."""
import datetime
import json
import sqlite3

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
"""


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def init() -> None:
    with connect() as conn:
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
                    "CREATE INDEX IF NOT EXISTS idx_posts_hash ON posts(content_hash)"):
            try:
                conn.execute(ddl)
            except sqlite3.OperationalError:
                pass  # column already exists


def has_post(source_ref: str) -> bool:
    with connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM posts WHERE source_ref = ?", (source_ref,)
        ).fetchone()
    return row is not None


def has_text_prefix(source: str, prefix: str) -> bool:
    """Is a LONGER post from this source already stored that starts with `prefix`?"""
    with connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM posts WHERE source = ? AND length(text) > ? "
            "AND substr(text, 1, ?) = ? LIMIT 1",
            (source, len(prefix), len(prefix), prefix)).fetchone()
    return row is not None


def fill_url(source_ref: str, url: str) -> bool:
    """Set the url of a stored post that has none. True if a row changed."""
    with connect() as conn:
        cur = conn.execute(
            "UPDATE posts SET url = ? WHERE source_ref = ? AND url IS NULL",
            (url, source_ref))
    return cur.rowcount > 0


def add_post(source: str, source_ref: str, url: str | None, text: str | None,
             media_paths: list[str] | None = None) -> int | None:
    """Insert a post; returns its id, or None if already stored."""
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with connect() as conn:
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
    with connect() as conn:
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
    with connect() as conn:
        conn.execute(
            "UPDATE posts SET score = ?, verdict = ? WHERE id = ?",
            (verdict.get("score"), json.dumps(verdict, ensure_ascii=False), post_id),
        )


def save_analysis(post_id: int, verdict: dict) -> None:
    """Store the match verdict AND any vehicle records it extracted.

    Only a verdict that actually produced a vehicles list is authoritative:
    a failed call (score None) must never wipe rows or mark the post done.
    """
    set_verdict(post_id, verdict)
    if verdict.get("vehicles_copied_from_duplicate"):
        return  # duplicate content: vehicles already recorded on the original
    if verdict.get("score") is None:
        return  # failed analysis — leave existing rows and extraction state
    vehicles = verdict.get("vehicles")
    if isinstance(vehicles, list):
        add_vehicles(post_id, [v for v in vehicles if isinstance(v, dict)])


def add_vehicles(post_id: int, entries: list[dict]) -> int:
    """Replace this post's vehicle rows with `entries`; returns count stored.

    Replace, not append: analysis can legitimately run again (a re-check, a
    resubmitted batch, a provider fallback), and appending would duplicate
    every vehicle each time. An empty list is authoritative — it means the
    post genuinely holds no vehicles — so it still marks the post extracted.
    """
    from .aliases import model_key_for
    from .normalize import norm_chassis, norm_plate

    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with connect() as conn:
        conn.execute("DELETE FROM vehicles WHERE post_id = ?", (post_id,))
        for e in entries:
            conn.execute(
                "INSERT INTO vehicles (post_id, make, model, color, year, chassis, "
                "chassis_norm, plate, plate_norm, status, location, contact, "
                "description, extracted_at, model_key) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (post_id, e.get("make"), e.get("model"), e.get("color"),
                 str(e.get("year") or "") or None,
                 e.get("chassis"), norm_chassis(e.get("chassis") or "") or None,
                 e.get("plate"), norm_plate(e.get("plate") or "") or None,
                 e.get("status"), e.get("location"), e.get("contact"),
                 e.get("description"), now,
                 model_key_for(e.get("make"), e.get("model"), e.get("description"))),
            )
        conn.execute("UPDATE posts SET extracted = 1 WHERE id = ?", (post_id,))
    return len(entries)


def posts_without_vehicles(limit: int = 500) -> list[dict]:
    """Posts not yet run through vehicle extraction."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM posts WHERE extracted = 0 ORDER BY id LIMIT ?", (limit,)
        ).fetchall()
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
    with connect() as conn:
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
    with connect() as conn:
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
    with connect() as conn:
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
    with connect() as conn:
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
    with connect() as conn:
        conn.execute("UPDATE posts SET content_hash = ? WHERE id = ?", (chash, post_id))


def unanalyzed(limit: int = 500, newest_first: bool = False) -> list[dict]:
    """Posts stored but not yet scored (e.g. collected before the API key was set).

    `newest_first` is for a lane that cannot keep up with intake (the free
    tier clears ~800/day against ~3,000 arriving): oldest-first would never
    reach a post collected this month.
    """
    order = "DESC" if newest_first else "ASC"
    with connect() as conn:
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


def top_candidates(limit: int = 20, min_score: int = 40) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM posts WHERE score >= ? ORDER BY score DESC, seen_at DESC "
            "LIMIT ?",
            (min_score, limit),
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["verdict"] = json.loads(row["verdict"]) if row["verdict"] else None
            item["media"] = [
                m["path"]
                for m in conn.execute(
                    "SELECT path FROM media WHERE post_id = ?", (row["id"],)
                ).fetchall()
            ]
            result.append(item)
    return result
