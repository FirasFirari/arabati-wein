"""Standing watches: where they live, and whether any of them has matched.

A watch is left by a visitor whose search returned nothing. The form on
mycarsd.online posts to **Netlify Forms**, which is the only place the data
exists — it never reaches this database, and nothing about it is published back
to the site.

Getting it here needs one of:
  * `python scripts\\fetch_watches.py`  — pulls the Netlify API into
    `data/watches.json` (needs a token, see that script), or
  * a manual CSV export from
    https://app.netlify.com/projects/mycarsd/forms saved as `data/watches.csv`.

This module is the SINGLE definition of the matching rule. `check_watches.py`
(the human-readable report, with contact details) and `dashboard.py` (the
identifier/status rows) both import it, so the two can never drift apart and
tell a visitor different things.

CONTACT DETAILS ARE PERSONAL DATA. `load_watches` returns them because the
report needs them to tell a human who to call; `status_rows` deliberately
leaves them out, so the dashboard shows no phone numbers.
"""
import csv
import json
import pathlib
import re

from .config import DATA_DIR
from .normalize import is_plate_shaped, norm_chassis, plate_variants

WATCHES_JSON = DATA_DIR / "watches.json"
WATCHES_CSV = DATA_DIR / "watches.csv"

# Netlify's CSV header casing has changed before; match loosely.
_IDENT_KEYS = ("identifier", "chassis or plate", "chassis")
_CONTACT_KEYS = ("contact", "phone", "whatsapp")
_NOTE_KEYS = ("note", "description")
_WHEN_KEYS = ("created_at", "date", "submitted at")


def _pick(row: dict, names: tuple) -> str:
    for key, value in row.items():
        if key and key.strip().lower() in names:
            return (value or "").strip()
    return ""


def load_watches() -> list[dict]:
    """Every standing watch we hold locally, newest first.

    Prefers `data/watches.json` (the API pull, which carries a real timestamp)
    and falls back to `data/watches.csv` (the manual export). Returns [] when
    neither exists — that is the normal state before anyone has fetched, and
    the caller should say so rather than imply nobody has registered.
    """
    rows: list[dict] = []
    if WATCHES_JSON.exists():
        try:
            rows = json.loads(WATCHES_JSON.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            rows = []
    elif WATCHES_CSV.exists():
        with WATCHES_CSV.open(encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.DictReader(fh))

    out = []
    for r in rows:
        ident = _pick(r, _IDENT_KEYS)
        if not ident:
            continue
        out.append({
            "identifier": ident,
            "contact": _pick(r, _CONTACT_KEYS),
            "note": _pick(r, _NOTE_KEYS),
            "created_at": _pick(r, _WHEN_KEYS),
        })
    out.sort(key=lambda w: w["created_at"], reverse=True)
    return out


def match_reason(qn: str, qp, chassis_norm: str, plate_norm: str):
    """Why this vehicle row answers this watch, or None.

    `qn` is the chassis reading of the query, `qp` the set of plate digit runs
    to accept (a string is treated as a one-element set).

    Mirrors the published page: exact chassis, then suffix/fragment
    containment with length floors, and only if none of that fires, the plate
    runs. The floors are asymmetric on purpose — a SHORT query against a longer
    stored chassis is the normal case (a family knows the last digits), so it
    is allowed from 4 characters, while a short STORED value matching against a
    longer query needs 5 or 6 before it counts as evidence. Below those floors,
    digit fragments matched pure noise.

    PLATES ARE MATCHED RUN BY RUN, never as one flattened number. A Sudanese
    plate reads "31305 خ 1" — a serial plus a region marker carrying its own
    digit — and gluing those into "313051" matches no stored plate at all.
    That exact bug shipped once on the page and made every plate typed with its
    region marker fail. `plate_variants` keeps the runs apart.

    If the page's rules change, change this with them or a visitor will be
    told something the site itself would not show them.
    """
    cn = chassis_norm or ""
    if qn and cn:
        if qn == cn:
            return "exact chassis"
        q_shorter = len(qn) <= len(cn)
        short, long_ = (qn, cn) if q_shorter else (cn, qn)
        if long_.endswith(short) and len(short) >= (4 if q_shorter else 5):
            return f"chassis suffix {short}"
        # A mid-string fragment must carry at least 5 DIGITS. Measured
        # 2026-10-27: the 8-character VIN prefix "JMYSTCY4" (one digit) is the
        # make+model code shared by every car of that model, and it raised 23
        # false hits for one watch; every numeric fragment that ever fired had
        # 6 digits, so none of those is lost.
        if (short in long_ and len(short) >= (4 if q_shorter else 6)
                and sum(ch.isdigit() for ch in short) >= 5):
            return f"chassis fragment {short}"
    runs = {qp} if isinstance(qp, str) else (qp or set())
    if plate_norm and plate_norm in runs:
        return f"exact plate {plate_norm}"
    # A plate written ALONE on a list line ("41019 / خ #") is filed by the كشف
    # parser as that row's chassis: a searchable number beats the right column.
    # So a plate query must find its serial there too, exactly and never as a
    # suffix, or the 2026-10-08 list entry for watch "41019 خ 9" stays hidden.
    if not qn and cn and len(cn) >= 4 and cn in runs:
        return f"plate {cn} (filed as chassis)"
    return None


def readings(identifier: str) -> tuple[str, set]:
    """(chassis reading, plate runs) for one watch identifier.

    A plate-shaped identifier has NO chassis reading. `norm_chassis` would glue
    "41019 خ 9" into "410199", and as a chassis suffix that matched a passport
    whose machine-readable line ends ...M2410199. See `is_plate_shaped`.
    """
    runs = plate_variants(identifier)
    if is_plate_shaped(identifier):
        return "", runs
    return norm_chassis(identifier), runs


def scan(conn, watches: list[dict]) -> dict[str, list[dict]]:
    """Match every watch against the registry in ONE pass over `vehicles`.

    Keyed by identifier, each value a list of {post_id, why, desc}.

    One streaming pass rather than a query per watch: the suffix and fragment
    rules cannot use the chassis index, so per-watch queries would mean a full
    scan each. Rows are consumed as they arrive instead of fetchall() — the
    table is past 640,000 rows and materialising it costs hundreds of MB.
    """
    if not watches:
        return {}
    prepared = [(w["identifier"], *readings(w["identifier"])) for w in watches]
    hits: dict[str, list[dict]] = {ident: [] for ident, _, _ in prepared}

    cur = conn.execute(
        "SELECT post_id, chassis_norm, plate_norm, make, model, year, color "
        "FROM vehicles WHERE chassis_norm IS NOT NULL OR plate_norm IS NOT NULL")
    for post_id, cn, pn, make, model, year, color in cur:
        for ident, qn, qp in prepared:
            why = match_reason(qn, qp, cn, pn)
            if why:
                desc = " ".join(str(x) for x in (color, year, make, model) if x)
                hits[ident].append(
                    {"post_id": post_id, "why": why, "desc": desc})
    return hits


# Phone numbers are the bulk of the long digit runs in raw post text, and a
# watched 6-digit chassis is very often the tail of somebody's mobile number.
# Sudan (09/01 + 8), Egypt (01x + 8), Gulf (05 + 8), with or without the
# country code. Only applied to RAW text — extracted rows are already labelled.
_PHONE = re.compile(
    r"^(?:0[19]\d{8}|249[19]\d{8}|01[0125]\d{8}|201[0125]\d{8}"
    r"|05\d{8}|9665\d{8}|9715\d{8})$")
_RAW_TOKEN = re.compile(r"[0-9A-Za-z٠-٩۰-۹]+")


def raw_reason(qn: str, qp, token: str):
    """`match_reason` for one token of raw post text, with stricter floors.

    Raw text has no labels: a number may be a price, a phone, a date or a
    chassis. So a watch needs at least 5 characters before raw text can answer
    it (4-digit tails matched noise), phone-shaped tokens never count, and a
    plate only counts on an exact digit run of 5+.

    Two floors are raised from `match_reason`, both learned on the first run:
    a token must carry a digit (the word "DELETE" matched inside the test
    watch "TEST-DELETE-ME-0001"), and a token SHORTER than the query needs 6
    characters, not 5 — 5-digit runs in raw text are mostly plate serials, and
    they kept landing on the tails of unrelated 7-digit chassis.
    """
    if (len(token) < 5 or not any(ch.isdigit() for ch in token)
            or (token.isdigit() and _PHONE.match(token))):
        return None
    if qn and len(qn) >= 5:
        # people drop the leading zero when they retype a number in a post
        if token == qn or token == qn.lstrip("0"):
            return "exact chassis"
        if len(token) >= len(qn) or len(token) >= 6:
            why = match_reason(qn, None, token, None)
            if why:
                return why
    if token.isdigit() and len(token) >= 5:
        runs = {qp} if isinstance(qp, str) else (qp or set())
        if token in runs:
            return f"exact plate {token}"
    return None


_RAW_SQL = ("SELECT p.id, p.text, (SELECT group_concat(m.ocr_text, ' ') FROM media m"
            " WHERE m.post_id = p.id AND m.ocr_text IS NOT NULL) FROM posts p ")


def raw_posts(conn, since_post_id: int = 0, extra_ids=None):
    """(post_id, text, ocr) for every post newer than `since_post_id`, plus
    the older posts in `extra_ids` — those whose photos were OCR'd since the
    last sweep (scripts/local_ocr_sweep.py reads old photos, newest first)."""
    yield from conn.execute(_RAW_SQL + "WHERE p.id > ?", (since_post_id,))
    old = sorted(i for i in (extra_ids or ()) if i <= since_post_id)
    for i in range(0, len(old), 500):
        chunk = old[i:i + 500]
        yield from conn.execute(
            _RAW_SQL + f"WHERE p.id IN ({','.join('?' * len(chunk))})", chunk)


def scan_raw(conn, watches: list[dict], since_post_id: int = 0,
             extra_ids=None) -> dict[str, list[dict]]:
    """Match watches against RAW post text and photo OCR, not just `vehicles`.

    `vehicles` only grows when a scoring lane runs, and those lanes sat idle
    from 2026-09-13 while ~100,000 posts piled up unread, so every watch was
    being checked against a registry weeks stale. Raw text is current the
    moment the collector stores a post, and includes Facebook's own image
    captions ("May be an image of text that says ..."), which is free OCR.

    Tokens are matched one at a time, never a flattened blob: these posts are
    lists of numbers, and joining entries invents sequences that are not there
    (the same rule as `prefilter.identifier_hit`).

    Same return shape as `scan`; hits carry `why` prefixed "raw text:" and a
    `kind` from `post_kind`, because the commonest raw hit is the OWNER'S OWN
    "my car is missing" post — the identifier is right, but it is not a
    sighting.
    """
    if not watches:
        return {}
    by_ident = {w["identifier"]: w for w in watches}   # duplicates collapse
    prepared = [(ident, *readings(ident)) for ident in by_ident]
    prepared = [p for p in prepared if len(p[1]) >= 5 or
                any(len(r) >= 5 for r in p[2])]
    hits: dict[str, list[dict]] = {ident: [] for ident in by_ident}
    if not prepared:
        return hits

    for post_id, text, ocr in raw_posts(conn, since_post_id, extra_ids):
        blob = f"{text or ''} {ocr or ''}"
        tokens = {norm_chassis(t) for t in _RAW_TOKEN.findall(blob)}
        for ident, qn, qp in prepared:
            for token in tokens:
                why = raw_reason(qn, qp, token)
                if why:
                    hits[ident].append({"post_id": post_id,
                                        "why": f"raw text: {why}",
                                        "desc": "", "kind": post_kind(blob)})
                    break
    return hits


_LOST_WORDS = ("مفقود", "مفقوده", "مفقودة", "سرقت", "مسروق", "مسروقة", "منهوب",
               "منهوبة", "اتسرقت", "انسرقت", "اتنهبت")
_FOUND_WORDS = ("لقيناها", "لقينا", "موجود", "موجوده", "موجودة", "حصر", "كشف",
                "عثور", "استلام", "الحوش", "حوش", "ساحة", "ساحه", "المضبوطات")


def post_kind(text: str) -> str:
    """Crude label for a raw hit: 'found list', 'lost report' or 'unclear'.

    Keyword-only, so treat it as a sorting hint, never a verdict. Found words
    win over lost words because recovery lists are titled with both ("cars
    that were lost — we found them").
    """
    if any(w in text for w in _FOUND_WORDS):
        return "found list"
    if any(w in text for w in _LOST_WORDS):
        return "lost report"
    return "unclear"


def status_rows(conn) -> list[dict]:
    """One row per standing watch: what was registered, and where it stands.

    No contact details — this feeds the dashboard.

    Raw-text hits are READ from `RAW_HITS_JSON`, written by
    `scripts/raw_sweep.py`, never computed here — the raw scan takes about a
    minute and the dashboard must not pay that on every refresh.
    """
    watches = load_watches()
    hits = scan(conn, watches)
    raw = load_raw_hits()
    reviewed = load_reviewed()
    rows = []
    seen_idents = set()
    for w in watches:
        ident = w["identifier"]
        if ident in seen_idents:
            continue
        seen_idents.add(ident)
        every = hits.get(ident, []) + raw.get(ident, [])
        n_reviewed = len({h["post_id"] for h in every
                          if (ident, h["post_id"]) in reviewed})
        found = [h for h in hits.get(ident, [])
                 if (ident, h["post_id"]) not in reviewed]
        raw_found = [h for h in raw.get(ident, [])
                     if (ident, h["post_id"]) not in reviewed]
        sightings = [h for h in raw_found if h.get("kind") != "lost report"]
        if found:
            status, why = "MATCH", found[0]["why"]
        elif sightings:
            status, why = "RAW TEXT HIT", f"{sightings[0]['why']} ({sightings[0].get('kind')})"
        elif raw_found:
            status, why = "own lost post only", raw_found[0]["why"]
        else:
            status, why = "no match yet", ""
        rows.append({
            "identifier": ident,
            "note": w["note"],
            "created_at": w["created_at"],
            # posts still waiting for a human; the owner's own lost post is
            # not one, it only proves the number was typed right
            "hits": len({h["post_id"] for h in found + sightings}),
            "reviewed": n_reviewed,
            "status": status,
            "why": why,
        })
    return rows


REVIEWED_JSON = DATA_DIR / "watch_reviewed.json"


def load_reviewed() -> set[tuple[str, int]]:
    """(identifier, post_id) pairs a human read and judged NOT a sighting.

    The rule only sees digits. It cannot tell the owner's own comment from a
    found list, or a pickup's 5-digit tail from a RAV4's chassis. Once someone
    has read the post, the verdict goes in `data/watch_reviewed.json` (one
    object per pair: identifier, post_id, verdict, note) and the dashboard and
    report stop calling it a match.

    Per POST, never per number: a new post carrying the same number is shown
    again, because that one may be the real sighting.
    """
    try:
        rows = json.loads(REVIEWED_JSON.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    return {(r["identifier"], int(r["post_id"])) for r in rows
            if r.get("identifier") and r.get("post_id") is not None}


RAW_HITS_JSON = DATA_DIR / "raw_watch_hits.json"


def load_raw_hits() -> dict[str, list[dict]]:
    """The last raw-text sweep's hits, keyed by identifier ({} if none yet)."""
    try:
        return json.loads(RAW_HITS_JSON.read_text(encoding="utf-8")).get("hits", {})
    except (OSError, json.JSONDecodeError, AttributeError):
        return {}


def source_note() -> str:
    """How the local copy got here, for the dashboard to caption itself."""
    if WATCHES_JSON.exists():
        return "Netlify API pull"
    if WATCHES_CSV.exists():
        return "manual CSV export"
    return "NOT FETCHED — run scripts/fetch_watches.py"
