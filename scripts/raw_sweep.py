"""Hourly $0 sweep: the target car and every standing watch, against RAW text.

Why this exists: everything else that checks identifiers reads the `vehicles`
table, which only grows when a scoring lane runs. Those lanes stopped on
2026-09-13 with ~100,000 posts unread, so the target check and all 23 watches
were being answered from a registry weeks out of date. Raw post text (plus
photo OCR and Facebook's own image captions, which arrive inside the text) is
current the moment the collector stores a post, and searching it costs nothing.

What one run does:
  1. (--fetch) pull new watch registrations from Netlify -> data/watches.json
  2. target scan: `prefilter.identifier_hit` over every post not yet swept
  3. watch scan: `watches.scan_raw` — incremental, or a full pass whenever the
     watch list changed (a new watch must be checked against all history)
  4. write data/raw_watch_hits.json (no contacts; the dashboard reads it)
  5. anything NEW goes to data/logs/raw_sweep.log, data/raw_sweep_NEW.txt and a
     Windows pop-up. "New" is remembered in data/raw_sweep_seen.json.

It only READS the database, so it is safe beside the collector and the scorer.
It does nothing at all while data/PAUSED exists (see scripts/carhunt_control.ps1).

A raw hit is a LEAD, never a sighting: check the post by eye. The commonest
raw hit is the owner's own "my car is missing" post, labelled 'lost report'.

    python scripts\\raw_sweep.py --fetch
    python scripts\\raw_sweep.py --full        # ignore saved progress
"""
import argparse
import datetime
import json
import pathlib
import subprocess
import sys

# scheduled under pythonw: never flash a console window for a child process
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from carhunt import db, prefilter, watches as watchlib
from carhunt.config import DATA_DIR, PROJECT_ROOT, load_config

PAUSE_FLAG = DATA_DIR / "PAUSED"
STATE = DATA_DIR / "raw_sweep_state.json"
SEEN = DATA_DIR / "raw_sweep_seen.json"
NEW_REPORT = DATA_DIR / "raw_sweep_NEW.txt"
LOG = DATA_DIR / "logs" / "raw_sweep.log"


def now() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log(msg: str) -> None:
    line = f"[{now()}] {msg}"
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def load_json(path: pathlib.Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def toast(title: str, body: str) -> None:
    """Windows pop-up through PowerShell's own app id; silent on failure."""
    esc = lambda s: s.replace("'", "''").replace("&", "and").replace("<", "")
    ps = (
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications,"
        " ContentType = WindowsRuntime] > $null;"
        "$t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent("
        "[Windows.UI.Notifications.ToastTemplateType]::ToastText02);"
        "$x = $t.GetElementsByTagName('text');"
        f"$x[0].AppendChild($t.CreateTextNode('{esc(title)}')) > $null;"
        f"$x[1].AppendChild($t.CreateTextNode('{esc(body)}')) > $null;"
        "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("
        "'{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe')"
        ".Show([Windows.UI.Notifications.ToastNotification]::new($t))")
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                       capture_output=True, timeout=30,
                       creationflags=NO_WINDOW)
    except Exception:
        pass


LINK_WINDOW_HOURS = 2
LINK_MAX_MISSING = 0.10


def link_health(conn) -> None:
    """Warn when new Facebook posts are being stored WITHOUT their link.

    From late August to 2026-10-08, 87-90% of Facebook posts were saved with no
    url. Facebook leaves a post's own permalink as a placeholder until it is
    hovered, and nothing checked. A lead with no link can only be traced back
    by hand, if at all. This looks at what the collector stored in the last
    two hours, so a re-run with --full cannot trip it on old history.
    """
    cutoff = (datetime.datetime.now(datetime.timezone.utc)
              - datetime.timedelta(hours=LINK_WINDOW_HOURS)).isoformat()
    total, missing = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(url IS NULL), 0) FROM posts "
        "WHERE source LIKE 'facebook:%' AND seen_at >= ?", (cutoff,)).fetchone()
    if total < 20:
        return            # too few to judge; the collector may be paused
    share = missing / total
    if share > LINK_MAX_MISSING:
        log(f"WARNING links: {missing}/{total} Facebook posts in the last "
            f"{LINK_WINDOW_HOURS}h were saved WITHOUT a link ({share:.0%})")
        toast("CarHunt: posts saved without links",
              f"{missing} of {total} new Facebook posts have no link. "
              f"The link reveal in fb_harvest.py has stopped working.")
    else:
        log(f"links ok: {total - missing}/{total} new Facebook posts have a link")


def target_scan(conn, car: dict, since: int, extra_ids=None) -> list[dict]:
    hits = []
    for post_id, text, ocr in watchlib.raw_posts(conn, since, extra_ids):
        why = prefilter.identifier_hit(f"{text or ''} {ocr or ''}", car)
        if why:
            hits.append({"post_id": post_id, "why": why})
    return hits


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch", action="store_true",
                    help="pull new watch registrations from Netlify first")
    ap.add_argument("--full", action="store_true",
                    help="ignore saved progress and sweep every post")
    args = ap.parse_args()

    if PAUSE_FLAG.exists():
        print("paused (data/PAUSED exists) — nothing done")
        return

    if args.fetch:
        r = subprocess.run([sys.executable, str(PROJECT_ROOT / "scripts" / "fetch_watches.py")],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", cwd=PROJECT_ROOT, creationflags=NO_WINDOW)
        if r.returncode != 0:
            log(f"watch fetch FAILED (sweeping the copy on disk): {r.stderr.strip()[-200:]}")

    state = {} if args.full else load_json(STATE, {})
    seen = {tuple(x) for x in load_json(SEEN, [])}
    conn = db.connect()
    max_id = conn.execute("SELECT COALESCE(MAX(id), 0) FROM posts").fetchone()[0]

    # Older posts whose photos were OCR'd since the last run (the local OCR
    # sweep works through ~140k old photos). ocr_at is UTC "...Z"; indexed.
    ocr_mark = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    reocr = {r[0] for r in conn.execute(
        "SELECT DISTINCT post_id FROM media WHERE ocr_at > ? AND ocr_text <> ''",
        (state.get("ocr_mark", "9999"),))}

    # --- the target car ---
    car = load_config()["car"]
    t_hits = target_scan(conn, car, state.get("target_last_id", 0), reocr)

    # --- standing watches ---
    watches = watchlib.load_watches()
    sig = sorted({w["identifier"] for w in watches})
    stored = load_json(watchlib.RAW_HITS_JSON, {})
    # Only a watch that was not in the last sweep needs ALL history; the rest
    # continue incrementally. Until 2026-10-20 any change to the watch list
    # rescanned every watch against every post, and at 61 watches that pass
    # outgrew the task's 30-minute limit, so from the first new registration
    # on, every hourly run was killed before it wrote anything.
    known = set(stored.get("watch_sig") or []) if not args.full else set()
    merged = {k: v for k, v in (stored.get("hits") or {}).items() if k in known} if known else {}
    since = state.get("watch_last_id", 0) if known else 0
    fresh: dict = {}
    old_watches = [w for w in watches if w["identifier"] in known]
    new_watches = [w for w in watches if w["identifier"] not in known]
    if old_watches:
        fresh.update(watchlib.scan_raw(conn, old_watches, since_post_id=since,
                                       extra_ids=reocr if since else None))
    if new_watches:
        log(f"{len(new_watches)} new watch(es): scanning all history for them")
        fresh.update(watchlib.scan_raw(conn, new_watches, since_post_id=0))
    for ident, hs in fresh.items():
        have = {h["post_id"] for h in merged.get(ident, [])}
        merged.setdefault(ident, []).extend(h for h in hs if h["post_id"] not in have)

    watchlib.RAW_HITS_JSON.write_text(json.dumps(
        {"generated_at": now(), "last_post_id": max_id, "watch_sig": sig,
         "hits": merged}, ensure_ascii=False, indent=1), encoding="utf-8")
    STATE.write_text(json.dumps({"target_last_id": max_id, "watch_last_id": max_id,
                                "ocr_mark": ocr_mark}),
                     encoding="utf-8")

    # --- what is new since the last run ---
    new_lines, new_target, new_watch = [], 0, 0
    new_watch_hits = []   # (ident, hit) pairs to notify
    for h in t_hits:
        if ("TARGET", h["post_id"]) not in seen:
            seen.add(("TARGET", h["post_id"]))
            new_target += 1
            new_lines.append(f"TARGET  post {h['post_id']}  {h['why']}")
    reviewed = watchlib.load_reviewed()
    for ident, hs in merged.items():
        for h in hs:
            pair = (ident, h["post_id"])
            if pair not in seen and pair not in reviewed:
                seen.add((ident, h["post_id"]))
                if h.get("kind") != "lost report":
                    new_watch += 1
                new_watch_hits.append((ident, h))
                new_lines.append(f"WATCH {ident}  post {h['post_id']}  "
                                 f"{h['why']}  [{h.get('kind')}]")
    SEEN.write_text(json.dumps(sorted(seen), ensure_ascii=False), encoding="utf-8")

    swept = max_id - min(state.get("target_last_id", 0), since)
    log(f"swept up to post {max_id} ({swept} new posts + {len(reocr)} with "
        f"newly OCR'd photos): target hits {len(t_hits)} "
        f"(new {new_target}), watch hits new {len(new_lines) - new_target}")
    if new_lines:
        with NEW_REPORT.open("a", encoding="utf-8") as fh:
            fh.write(f"\n=== {now()} ===\n" + "\n".join(new_lines) + "\n")
        for line in new_lines:
            log("  NEW " + line)
    # --- tell people: operator on Telegram, registrant by email or a
    # ready-to-tap WhatsApp draft (carhunt/notify.py). Never fatal.
    try:
        from carhunt import notify
        by_ident = {}
        for w in watches:
            by_ident.setdefault(w["identifier"], []).append(w)
        for ident, h in new_watch_hits:
            for w in by_ident.get(ident, []):
                res = notify.notify_watch_hit(w, h, conn)
                log(f"  notified watch {ident} post {h['post_id']}: {res}")
        if new_target:
            notify.notify_target_hits(new_target)
    except Exception as e:
        log(f"notify failed: {e}")
    link_health(conn)
    if new_target:
        toast("CarHunt: TARGET identifier in a post",
              f"{new_target} new post(s). Check data\\raw_sweep_NEW.txt — verify by eye.")
    elif new_watch:
        toast("CarHunt: standing-watch lead",
              f"{new_watch} new raw-text lead(s). Run scripts\\check_watches.py for contacts.")


if __name__ == "__main__":
    main()
