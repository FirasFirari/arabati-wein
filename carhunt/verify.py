"""Verify new standing-watch hits by eye — Claude Code's eye — before anyone is told.

Until 2026-10-27 every raw-sweep hit went straight to the operator's Telegram
with "verify by eye before anything else". Measured that day: 175 hit pairs
across 82 watches, ONE real lead. The rest were a pickup's plate under a
Sportage heading, a Morning's plate matching a chassis tail, a VIN prefix
shared by every car of that model, owners' own comments, copies of one list.
So the alert was almost always noise, and the real check was always done by
hand in a Claude Code session: read the post around the number, compare make
/ model / plate region / list heading with the watch, decide.

This module does that check unattended:

  1. `scripts/raw_sweep.py` QUEUES every new (watch, post) pair here instead of
     alerting (`data/verify_queue.json`).
  2. `run()` groups the queue by watch + the text around the matched number
     (41 copies of one list line are one question, not 41), builds an evidence
     pack per group — excerpts, list heading, how many copies, whether the FULL
     identifier appears anywhere in text or OCR — and asks a headless
     `claude -p` for a structured verdict. No tools, no contact details.
  3. `own post` / `different car` (confident) -> written to
     `data/watch_reviewed.json` like a hand verdict (reviewed_by "claude-auto"),
     so the dashboard and report stop calling it a match.
     `possible sighting` -> stays visible as MATCH, the operator gets the lead
     card with the WhatsApp DRAFT (nothing is sent to the registrant).
     `unclear` / low confidence -> stays visible, flagged "needs your eye".
  4. ONE Telegram message per run with every verdict. Nothing before that.

The headless call strips ANTHROPIC_API_KEY so it runs on the claude.ai login
(subscription), never on a billed key. `verify:` in config.yaml sets the model.

    python -m carhunt.verify                     # process the queue
    python -m carhunt.verify --dry-run           # ask, print, write nothing
    python -m carhunt.verify --replay IDENT POST_ID [--show-evidence]
"""
import datetime
import json
import os
import re
import shutil
import subprocess
import sys

from . import notify, watches as watchlib
from .config import DATA_DIR, PROJECT_ROOT, load_config

QUEUE = DATA_DIR / "verify_queue.json"
LOG = DATA_DIR / "logs" / "verify.log"
VERDICTS = DATA_DIR / "verify_verdicts.jsonl"
AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
VERDICTS_OK = ("possible sighting", "own post", "different car", "unclear")
NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
MAX_ATTEMPTS = 3


def log(msg: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S} {msg}\n")


def _cfg() -> dict:
    v = load_config().get("verify") or {}
    return {"model": v.get("model") or "opus",
            "max_groups_per_run": int(v.get("max_groups_per_run") or 12),
            "timeout_s": int(v.get("timeout_s") or 300),
            "min_confidence": float(v.get("min_confidence") or 0.6)}


# ------------------------------------------------------------------ queue --
def load_queue() -> list[dict]:
    try:
        return json.loads(QUEUE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []


def save_queue(items: list[dict]) -> None:
    QUEUE.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")


def enqueue(pairs) -> int:
    """`pairs` = iterable of (identifier, hit dict). Returns how many were new."""
    items = load_queue()
    have = {(i["identifier"], int(i["post_id"])) for i in items}
    reviewed = watchlib.load_reviewed()
    added = 0
    for ident, h in pairs:
        key = (ident, int(h["post_id"]))
        if key in have or key in reviewed:
            continue
        items.append({"identifier": ident, "post_id": int(h["post_id"]),
                      "why": h.get("why") or "", "kind": h.get("kind") or "",
                      "desc": h.get("desc") or "",
                      "queued_at": datetime.datetime.now().isoformat(timespec="seconds"),
                      "attempts": 0})
        have.add(key)
        added += 1
    save_queue(items)
    return added


# --------------------------------------------------------------- evidence --
def _token_for(item: dict) -> str:
    """The number the rule matched on, read back from the `why` string."""
    why = item.get("why") or ""
    m = re.search(r"(?:suffix|fragment|plate)\s+(\S+)", why)
    if m:
        return m.group(1).strip("()")
    qn, qp = watchlib.readings(item["identifier"])
    return qn or (sorted(qp, key=len)[-1] if qp else "")


def _norm(text: str) -> str:
    return (text or "").translate(AR_DIGITS)


def _positions(blob: str, token: str) -> list[int]:
    if not token:
        return []
    b = _norm(blob).upper()
    out = []
    for t in {token.upper(), token.upper().lstrip("0")}:
        if len(t) >= 4:
            out += [m.start() for m in re.finditer(re.escape(t), b)]
    return sorted(set(out))


def _context_key(blob: str, token: str) -> str:
    pos = _positions(blob, token)
    if not pos:
        return ""
    b = _norm(blob)
    s = b[max(0, pos[0] - 30):pos[0] + len(token) + 15]
    return re.sub(r"\s+", " ", s).strip()


def _heading(blob: str, pos: int) -> str:
    """The last line before `pos` that is mostly words, not numbers: on a
    كشف list that is the block heading ('إسبورتاج لوحة', 'كورولا')."""
    for line in reversed(blob[:pos].split("\n")[:-1][-40:]):
        line = line.strip(" /-–•\t")
        if not line:
            continue
        letters = len(re.findall(r"[A-Za-z؀-ۿ]", line))
        digits = len(re.findall(r"\d", _norm(line)))
        if letters >= 3 and letters > digits:
            return line[:80]
    return ""


def _count_anywhere(conn, needle: str) -> dict:
    """How many posts / OCR'd photos carry `needle` verbatim (Latin digits,
    or the same digits written in Arabic numerals)."""
    if not needle or len(needle) < 5:
        return {}
    ar = needle.translate(str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩"))
    posts = conn.execute("SELECT count(*) FROM posts WHERE text LIKE ? OR text LIKE ?",
                         (f"%{needle}%", f"%{ar}%")).fetchone()[0]
    ocr = conn.execute("SELECT count(*) FROM media WHERE ocr_text LIKE ? OR ocr_text LIKE ?",
                       (f"%{needle}%", f"%{ar}%")).fetchone()[0]
    return {"needle": needle, "posts_with_it": posts, "photos_with_it": ocr}


def _post(conn, post_id: int) -> dict:
    row = conn.execute("SELECT id, source, url, text, seen_at FROM posts WHERE id = ?",
                       (post_id,)).fetchone()
    if not row:
        return {}
    ocr = conn.execute("SELECT group_concat(ocr_text, '\n---\n') FROM media WHERE post_id = ?"
                       " AND ocr_text IS NOT NULL AND ocr_text <> ''", (post_id,)).fetchone()[0]
    return {"id": row[0], "source": row[1], "url": row[2], "text": row[3] or "",
            "seen_at": row[4], "ocr": ocr or ""}


def evidence(conn, group: list[dict], watch: dict) -> dict:
    """Everything a reader needs, nothing they must not see (no contact)."""
    rep = group[0]
    post = _post(conn, rep["post_id"])
    token = _token_for(rep)
    excerpts, where = [], "post text"
    for field in ("text", "ocr"):
        blob = post.get(field) or ""
        pos = _positions(blob, token)
        if pos:
            where = "post text" if field == "text" else "photo OCR"
            for p in pos[:3]:
                excerpts.append({"heading_above": _heading(blob, p),
                                 "excerpt": _norm(blob[max(0, p - 350):p + len(token) + 350])})
            break
    if not excerpts:
        where = "not found verbatim (matched after normalisation)"
        excerpts.append({"heading_above": "", "excerpt": _norm(post.get("text", "")[:900])})
    qn, qp = watchlib.readings(watch["identifier"])
    checks = []
    full = qn or (sorted(qp, key=len)[-1] if qp else "")
    if full:
        checks.append(_count_anywhere(conn, full))
        if qn and len(qn) >= 11:                       # a VIN: its serial tail
            checks.append(_count_anywhere(conn, qn[-6:]))
    note = watch.get("note") or ""
    for run_ in re.findall(r"\d{5,}", _norm(note)):
        if run_ not in full and full not in run_ and not re.fullmatch(r"(19|20)\d\d", run_):
            c = _count_anywhere(conn, run_)
            if c:
                c["from"] = "a number in the registrant's note"
                checks.append(c)
    return {
        "watch": {"identifier": watch["identifier"], "note": note,
                  "registered": (watch.get("created_at") or "")[:10],
                  "chassis_reading": qn, "plate_serials": sorted(qp)},
        "rule": {"why": rep.get("why"), "matched_token": token,
                 "digits_in_token": sum(ch.isdigit() for ch in token),
                 "keyword_kind": rep.get("kind"), "stored_vehicle_desc": rep.get("desc")},
        "post": {"id": post.get("id"), "source": post.get("source"), "url": post.get("url"),
                 "seen_at": (post.get("seen_at") or "")[:10], "text_length": len(post.get("text", "")),
                 "first_200_chars": _norm(post.get("text", "")[:200]), "token_found_in": where,
                 "excerpts": excerpts},
        "copies": {"posts_in_this_group": len(group),
                   "post_ids": [g["post_id"] for g in group][:50]},
        "registry_wide_checks": [c for c in checks if c],
    }


PROMPT = """You are checking ONE automated hit for a missing-car watch in Sudan. A visitor registered an identifier (chassis number or plate) on a public lost-car site; a digit-matching rule found that number inside a scraped Facebook/Telegram post. Your job: read the evidence like a careful human and say whether this post could be a SIGHTING of the watched car, or is noise.

Vocabulary (choose exactly one):
- "possible sighting": the full identifier (or the plate serial with the same or an unreadable region) appears in a found / impound / recovery list (حصر, كشف, موجودات, لقيناها, الحوش, ساحة), or a post describing a car someone HAS, and nothing in the post contradicts the watch (make, model, colour, year, plate region).
- "own post": the number appears in the OWNER'S side of the story — a lost report (مفقودة, سرقت, بلاغ), a comment where someone lists their own missing car, a reply handing the chassis to a broker who asked "send us your chassis", a list of بلاغات. Right number, not a sighting.
- "different car": a concrete contradiction. Typical: the token sits under a PLATE heading or beside a region marker (خ١ … خ٩, لوحة) while the watch is a chassis; the token is only the tail or prefix of a longer number belonging to another make; only a VIN make/model prefix matches and the serial differs (a sister car); the post's make/model/colour/year disagree with the watcher's note; the plate region digit differs; Facebook alt-text of a screenshot glued phone status-bar digits onto the first list entry.
- "unclear": you cannot tell from the excerpt (no model named, region unreadable, number only partially shown).

Rules of judgement:
1. A matched token with fewer digits than the identifier is weak unless the make/model also agrees.
2. Lists are copied across dozens of groups; copies are not independent evidence.
3. `registry_wide_checks` tells you whether the FULL identifier appears anywhere in the corpus. If it appears nowhere and only a fragment matched here, lean "different car" or "unclear", never "possible sighting".
4. Be conservative about "possible sighting": say it only when the evidence supports it; "unclear" is an acceptable answer and a human will look.
5. Never speculate about people. Do not mention or infer contact details.

Answer with JSON: {"verdict": one of the four strings, "confidence": 0.0-1.0, "reason": at most 300 characters, in English, quoting the decisive Arabic words or numbers from the excerpt}.

EVIDENCE (JSON):
"""

SCHEMA = json.dumps({
    "type": "object",
    "properties": {"verdict": {"type": "string", "enum": list(VERDICTS_OK)},
                   "confidence": {"type": "number"},
                   "reason": {"type": "string"}},
    "required": ["verdict", "confidence", "reason"]})


def ask_claude(ev: dict, cfg: dict) -> dict:
    """One headless `claude -p` call, prompt on stdin, structured JSON back.

    Prompt goes over STDIN: the launcher is claude.cmd, so an argv prompt would
    pass through cmd.exe's 8 KB limit. The API key is stripped from the
    environment so the call runs on the claude.ai login (subscription), and
    the nested-session guard variables are dropped so it also works when
    launched from inside an interactive Claude Code session.
    """
    exe = shutil.which("claude")
    if not exe:                                     # Task Scheduler PATH can be shorter
        cand = os.path.join(os.environ.get("APPDATA", ""), "npm", "claude.cmd")
        exe = cand if os.path.exists(cand) else None
    if not exe:
        raise RuntimeError("claude CLI not on PATH")
    env = {k: v for k, v in os.environ.items()
           if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
                        "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")}
    prompt = PROMPT + json.dumps(ev, ensure_ascii=False, indent=1)
    r = subprocess.run([exe, "-p", "--output-format", "json", "--model", cfg["model"],
                        "--tools", "", "--json-schema", SCHEMA, "--no-session-persistence"],
                       input=prompt, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=env, cwd=PROJECT_ROOT, timeout=cfg["timeout_s"],
                       creationflags=NO_WINDOW)
    if r.returncode != 0:
        raise RuntimeError(f"claude rc={r.returncode}: {r.stderr.strip()[-300:]}")
    out = json.loads(r.stdout)
    so = out.get("structured_output")
    if not so:
        m = re.search(r"\{.*\}", out.get("result") or "", re.S)
        so = json.loads(m.group(0)) if m else None
    if not so or so.get("verdict") not in VERDICTS_OK:
        raise RuntimeError(f"no usable verdict in: {(out.get('result') or '')[:200]}")
    so["confidence"] = float(so.get("confidence") or 0)
    so["reason"] = (so.get("reason") or "")[:400]
    so["model"] = cfg["model"]
    so["cost_usd_reported"] = out.get("total_cost_usd")
    return so


# ------------------------------------------------------------------ apply --
def _append_reviewed(entries: list[dict]) -> None:
    path = watchlib.REVIEWED_JSON
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        rows = []
    have = {(r.get("identifier"), r.get("post_id")) for r in rows}
    rows += [e for e in entries if (e["identifier"], e["post_id"]) not in have]
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def _record(group: list[dict], verdict: dict, ev: dict, applied: str) -> None:
    VERDICTS.parent.mkdir(parents=True, exist_ok=True)
    first = ev["post"]["excerpts"][0]["excerpt"][:300] if ev["post"]["excerpts"] else ""
    with VERDICTS.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"at": datetime.datetime.now().isoformat(timespec="seconds"),
                             "identifier": group[0]["identifier"],
                             "post_ids": [g["post_id"] for g in group],
                             "why": group[0].get("why"), "verdict": verdict,
                             "applied": applied, "context": first},
                            ensure_ascii=False) + "\n")


MARK = {"possible sighting": "✅ POSSIBLE SIGHTING", "own post": "✗ own post",
        "different car": "✗ different car", "unclear": "❓ unclear — needs your eye"}


def run(conn, watches=None, dry_run: bool = False) -> dict:
    """Process the queue. Returns counts: groups, leads, reviewed, unclear, failed, skipped."""
    cfg = _cfg()
    items = load_queue()
    stats = {"groups": 0, "leads": 0, "reviewed": 0, "unclear": 0, "failed": 0, "skipped": 0}
    if not items:
        return stats
    watches = watches or watchlib.load_watches()
    by_ident: dict = {}
    for w in watches:
        by_ident.setdefault(w["identifier"], w)
    reviewed = watchlib.load_reviewed()
    items = [i for i in items if (i["identifier"], int(i["post_id"])) not in reviewed]

    # group: same watch, same words around the number -> one question
    groups: dict = {}
    for it in items:
        post = _post(conn, it["post_id"])
        ctx = _context_key(post.get("text", "") + " " + post.get("ocr", ""), _token_for(it))
        groups.setdefault((it["identifier"], ctx or f"post:{it['post_id']}"), []).append(it)
    order = sorted(groups.values(), key=lambda g: min(x["post_id"] for x in g))
    todo, later = order[:cfg["max_groups_per_run"]], order[cfg["max_groups_per_run"]:]
    stats["skipped"] = sum(len(g) for g in later)

    lines = []
    remaining = [x for g in later for x in g]
    today = datetime.date.today().isoformat()
    for group in todo:
        ident = group[0]["identifier"]
        watch = by_ident.get(ident) or {"identifier": ident, "note": "", "contact": ""}
        try:
            ev = evidence(conn, group, watch)
            verdict = ask_claude(ev, cfg)
        except Exception as e:                      # keep for next run, 3 strikes
            log(f"FAILED {ident} post {group[0]['post_id']}: {e}")
            for g in group:
                g["attempts"] = int(g.get("attempts") or 0) + 1
            if group[0]["attempts"] >= MAX_ATTEMPTS:
                lines.append(f"⚠ verification failed {MAX_ATTEMPTS}x · {ident} · post "
                             f"{group[0]['post_id']} ({len(group)} copies) · check by hand")
                stats["failed"] += 1
            else:
                remaining += group
            continue
        stats["groups"] += 1
        v, conf = verdict["verdict"], verdict["confidence"]
        copies = f" (+{len(group) - 1} copies)" if len(group) > 1 else ""
        url = ev["post"].get("url") or "no saved link"
        if v in ("own post", "different car") and conf >= cfg["min_confidence"]:
            applied, shown = "reviewed", v
            if not dry_run:
                _append_reviewed([{"identifier": ident, "post_id": g["post_id"], "verdict": v,
                                   "why_it_hit": g.get("why"),
                                   "note": f"auto ({verdict['model']}, conf {conf:.2f}): {verdict['reason']}",
                                   "reviewed": today, "reviewed_by": "claude-auto"} for g in group])
            stats["reviewed"] += len(group)
        elif v == "possible sighting":
            applied, shown = "lead", v
            stats["leads"] += 1
            if not dry_run:
                hit = {"post_id": group[0]["post_id"], "why": group[0].get("why"),
                       "kind": group[0].get("kind")}
                try:
                    res = notify.notify_watch_hit(
                        watch, hit, conn, contact_registrant=False,
                        verdict_note=f"Claude ({verdict['model']}, conf {conf:.2f}): {verdict['reason']}")
                    log(f"lead card sent for {ident} post {hit['post_id']}: {res}")
                except Exception as e:
                    log(f"lead card failed: {e}")
        else:                                       # unclear, or not confident enough
            applied, shown = "unclear", "unclear"
            stats["unclear"] += 1
        lines.append(f"{MARK[shown]} · {ident} · post {group[0]['post_id']}{copies} · "
                     f"{v} conf {conf:.2f}\n   {verdict['reason']}\n   {url}")
        log(f"{applied}: {ident} post {group[0]['post_id']} x{len(group)} -> {v} ({conf:.2f}) "
            f"{verdict['reason'][:120]}")
        if not dry_run:
            _record(group, verdict, ev, applied)

    if not dry_run:
        save_queue(remaining)
    if lines:
        head = f"CarHunt verification · {stats['groups']} hit group(s) read"
        if dry_run:
            head += " · DRY RUN"
        if stats["skipped"]:
            head += f" · {stats['skipped']} more queued for the next run"
        text = head + "\n\n" + "\n\n".join(lines)
        if dry_run:
            print(text)
        else:
            ok = notify.send_telegram(text)
            log(f"summary telegram {'sent' if ok else 'NOT sent'}: {stats}")
    return stats


# -------------------------------------------------------------------- CLI --
def _main(argv) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    import argparse
    from . import db
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="ask and print; write nothing, send nothing")
    ap.add_argument("--replay", nargs=2, metavar=("IDENT", "POST_ID"),
                    help="verify this one pair now (ignores the queue and the reviewed file)")
    ap.add_argument("--show-evidence", action="store_true")
    a = ap.parse_args(argv)
    conn = db.connect()
    if a.replay:
        ident, pid = a.replay[0], int(a.replay[1])
        all_w = watchlib.load_watches()
        watch = next((w for w in all_w if w["identifier"] == ident),
                     {"identifier": ident, "note": "", "contact": ""})
        hit = next((h for h in watchlib.load_raw_hits().get(ident, []) if int(h["post_id"]) == pid), None)
        if not hit:
            hits = watchlib.scan(conn, [watch])
            hit = next((h for h in hits.get(ident, []) if int(h["post_id"]) == pid),
                       {"post_id": pid, "why": "replay", "kind": ""})
        item = {"identifier": ident, "post_id": pid, "why": hit.get("why"),
                "kind": hit.get("kind"), "desc": hit.get("desc")}
        ev = evidence(conn, [item], watch)
        if a.show_evidence:
            print(json.dumps(ev, ensure_ascii=False, indent=1))
        print(json.dumps(ask_claude(ev, _cfg()), ensure_ascii=False, indent=1))
        return 0
    print(run(conn, dry_run=a.dry_run))
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
