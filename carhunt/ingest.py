"""Feed posts from any source (Facebook sweeps, screenshots, manual finds)
into the database and analyzer.

Usage:
    # Add one post (images can be local paths or https URLs — URLs are downloaded)
    python -m carhunt.ingest add --source facebook:696207072195117 \
        --ref fb:696207072195117:POSTID --url https://... \
        --text "..." --image https://scontent... --image photo.jpg

    # Add many posts at once from a JSON file:
    #   [{"source": "...", "ref": "...", "url": "...", "text": "...", "images": [...]}, ...]
    python -m carhunt.ingest batch posts.json

    # Analyze everything collected but not yet scored (e.g. before API key was set)
    python -m carhunt.ingest reanalyze
"""
import argparse
import hashlib
import json
import sys
import urllib.request

from . import db
from .analyzer import Analyzer, sniff_media_type
from .config import MEDIA_DIR, load_config
from .pipeline import analyze_post


def _fetch_image(url: str) -> str | None:
    """Download an image URL into data/media; returns the local path."""
    name = hashlib.sha256(url.encode()).hexdigest()[:20] + ".jpg"
    dest = MEDIA_DIR / name
    if dest.exists():
        return str(dest)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read()
    except Exception as e:
        print(f"  image download failed ({url[:80]}...): {e}")
        return None
    # CDN links often expire and return an HTML login page — don't store that
    if sniff_media_type(body[:16]) is None:
        print(f"  not an image, discarded ({url[:60]}...)")
        return None
    dest.write_bytes(body)
    return str(dest)


def _resolve_images(images: list[str]) -> list[str]:
    paths = []
    for img in images:
        if img.startswith("http"):
            path = _fetch_image(img)
            if path:
                paths.append(path)
        else:
            paths.append(img)
    return paths


def _analyze_and_report(analyzer: Analyzer | None, cfg: dict, post_id: int,
                        text: str | None, media_paths: list[str]) -> dict | None:
    if analyzer is None:
        print(f"  post {post_id} stored (not analyzed — run 'reanalyze' later)")
        return None
    verdict = analyze_post(analyzer, cfg, post_id, text, media_paths)
    score = verdict.get("score")
    marker = "🚨" if (score or 0) >= cfg["matching"]["alert_threshold"] else " "
    print(f"{marker} post {post_id}: score={score} "
          f"{verdict.get('reasons', verdict.get('error', ''))}")
    return verdict


def _make_analyzer(cfg: dict, skip: bool) -> Analyzer | None:
    if skip:
        return None
    try:
        return Analyzer(cfg)
    except Exception as e:
        print(f"Analyzer unavailable ({e}) — storing without scores.")
        return None


def add_one(cfg: dict, analyzer: Analyzer | None, source: str, ref: str,
            url: str | None, text: str | None, images: list[str]) -> None:
    if db.has_post(ref):
        print(f"  {ref} already stored, skipping")
        return
    media_paths = _resolve_images(images)
    post_id = db.add_post(source, ref, url, text, media_paths)
    if post_id is not None:
        _analyze_and_report(analyzer, cfg, post_id, text, media_paths)


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    p_add = sub.add_parser("add")
    p_add.add_argument("--source", required=True)
    p_add.add_argument("--ref", required=True, help="stable unique id for dedup")
    p_add.add_argument("--url")
    p_add.add_argument("--text")
    p_add.add_argument("--image", action="append", default=[],
                       help="local path or https URL; repeatable")
    p_add.add_argument("--no-analyze", action="store_true")

    p_batch = sub.add_parser("batch")
    p_batch.add_argument("json_file")
    p_batch.add_argument("--no-analyze", action="store_true")

    sub.add_parser("reanalyze")

    args = parser.parse_args()
    cfg = load_config()
    db.init()

    if args.command == "add":
        analyzer = _make_analyzer(cfg, args.no_analyze)
        add_one(cfg, analyzer, args.source, args.ref, args.url, args.text, args.image)

    elif args.command == "batch":
        with open(args.json_file, encoding="utf-8") as f:
            posts = json.load(f)
        analyzer = _make_analyzer(cfg, args.no_analyze)
        print(f"Ingesting {len(posts)} posts...")
        for p in posts:
            add_one(cfg, analyzer, p["source"], p["ref"], p.get("url"),
                    p.get("text"), p.get("images", []))

    elif args.command == "reanalyze":
        analyzer = _make_analyzer(cfg, skip=False)
        if analyzer is None:
            sys.exit("Set ANTHROPIC_API_KEY first.")
        done = 0
        consecutive_errors = 0
        attempts: dict[int, int] = {}
        while True:  # drain in batches until nothing is left unscored
            pending = db.unanalyzed()
            # ANY post left unscored comes back on the next pass — refusals and
            # unparseable verdicts too, not just API errors. Drop a post once
            # it has had its chances so the loop can never spin on it.
            pending = [p for p in pending if attempts.get(p["id"], 0) < 3]
            if not pending:
                break
            print(f"Analyzing batch of {len(pending)} stored posts (done so far: {done})...")
            for post in pending:
                attempts[post["id"]] = attempts.get(post["id"], 0) + 1
                verdict = _analyze_and_report(
                    analyzer, cfg, post["id"], post["text"], post["media"])
                done += 1
                # consecutive API-level failures mean the provider is down or
                # capped: stop the whole run rather than burning through the queue
                if verdict and verdict.get("error", "").startswith(
                        ("api_error", "rate_limited", "connection_error")):
                    consecutive_errors += 1
                    if consecutive_errors >= 10:
                        sys.exit(
                            f"\nAborting after {consecutive_errors} consecutive API "
                            f"errors — last: {verdict['error'][:200]}\n"
                            "Posts stay queued; rerun 'reanalyze' once the API works.")
                else:
                    consecutive_errors = 0
        stuck = [pid for pid, n in attempts.items() if n >= 3]
        print(f"Reanalyze complete — {done} attempts.")
        if stuck:
            print(f"{len(stuck)} posts still unscored after 3 tries "
                  f"(refusals or unparseable output): {stuck[:10]}")


if __name__ == "__main__":
    main()
