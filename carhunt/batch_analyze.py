"""Score the backlog through the Message Batches API at 50% of normal cost.

Same models and prompts as the live path — only the delivery is asynchronous
(results usually within an hour, guaranteed within 24). Live monitoring stays
real-time; this is for draining large queues cheaply.

Usage:
    python -m carhunt.batch_analyze submit            # queue unscored posts
    python -m carhunt.batch_analyze submit --limit 2000
    python -m carhunt.batch_analyze status            # check open batches
    python -m carhunt.batch_analyze collect           # write finished results
    python -m carhunt.batch_analyze run               # submit, wait, collect

Posts are screened with the free gates first, so only genuinely ambiguous
posts consume budget. Screen-tier requests go to the cheap model without
reference photos; anything the screen tier escalates is picked up on the next
pass through the normal analyzer.
"""
import argparse
import json
import sys
import time

import anthropic
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from anthropic.types.messages.batch_create_params import Request

from . import db
from .analyzer import MAX_POST_IMAGES, Analyzer, _image_block, _parse_verdict
from .config import DATA_DIR, load_config, max_output_tokens
from .prefilter import identifier_hit, is_relevant

STATE = DATA_DIR / "batches.json"
CHUNK = 250  # requests per batch upload (images make payloads large)


def _load_state() -> dict:
    if STATE.exists():
        return json.loads(STATE.read_text(encoding="utf-8"))
    return {"batches": []}


def _save_state(state: dict) -> None:
    STATE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _build_request(analyzer: Analyzer, post: dict) -> Request | None:
    """One batch request for a post, mirroring the screen tier."""
    blocks = [{"type": "text", "text": "POST under evaluation:"}]
    if post.get("text"):
        blocks.append({"type": "text", "text": f"Post text:\n{post['text']}"})
    for path in (post.get("media") or [])[:2]:  # screen tier: 2 images max
        block = _image_block(path)
        if block:
            blocks.append(block)
    if len(blocks) == 1:
        return None
    return Request(
        custom_id=f"post-{post['id']}",
        params=MessageCreateParamsNonStreaming(
            model=analyzer.screen_model or analyzer.model,
            max_tokens=max_output_tokens(),
            system=[{"type": "text", "text": analyzer.system,
                     "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": blocks}],
        ),
    )


def submit(cfg: dict, limit: int) -> None:
    analyzer = Analyzer(cfg)
    client = anthropic.Anthropic()
    car = cfg["car"]
    pending = db.unanalyzed(limit=limit)
    if not pending:
        print("Nothing queued.")
        return

    requests, free = [], 0
    for post in pending:
        # free gates first — never pay for what code can settle
        hit = identifier_hit(post.get("text"), car)
        if hit:
            db.save_analysis(post["id"], {
                "score": 95, "is_car_post": True, "model_match": True,
                "tier": "identifier_scan",
                "reasons": f"DETERMINISTIC IDENTIFIER MATCH: {hit}"})
            print(f"  !! post {post['id']}: {hit}")
            free += 1
            continue
        if not is_relevant(post.get("text"), post.get("media")):
            db.save_analysis(post["id"], {
                "score": 0, "is_car_post": False, "tier": "prefiltered",
                "reasons": "No vehicle vocabulary, chassis-like number or image."})
            free += 1
            continue
        chash = db.content_hash(post.get("text"))
        if chash:
            db.set_content_hash(post["id"], chash)
            prior = db.verdict_for_hash(chash, post["id"])
            if prior:
                v = dict(prior)
                v["tier"] = "duplicate"
                v["vehicles_copied_from_duplicate"] = True
                db.save_analysis(post["id"], v)
                free += 1
                continue
        req = _build_request(analyzer, post)
        if req is None:
            db.save_analysis(post["id"], {
                "score": 0, "is_car_post": False, "tier": "empty",
                "reasons": "Empty post."})
            free += 1
            continue
        requests.append(req)

    print(f"{free} posts settled for free; {len(requests)} need the model.")
    if not requests:
        return

    # batches accept up to 100k requests; chunk conservatively
    # Requests embed base64 images, so keep each upload small: a few hundred
    # per batch stays well inside the 256 MB cap and survives flaky uploads.
    state = _load_state()
    failed = 0
    for start in range(0, len(requests), CHUNK):
        chunk = requests[start:start + CHUNK]
        for attempt in range(3):
            try:
                batch = client.messages.batches.create(requests=chunk)
                state["batches"].append({"id": batch.id, "count": len(chunk),
                                         "status": batch.processing_status})
                _save_state(state)
                print(f"submitted batch {batch.id} "
                      f"({start + len(chunk)}/{len(requests)} requests)", flush=True)
                break
            except anthropic.APIStatusError as e:
                _save_state(state)
                sys.exit(f"\nSubmit stopped ({e.status_code}): {e.message}\n"
                         "Remaining posts stay queued — rerun 'submit' later.")
            except (anthropic.APIConnectionError, anthropic.APITimeoutError) as e:
                if attempt == 2:
                    failed += len(chunk)
                    print(f"  chunk at {start} failed after 3 tries ({e}); "
                          "left queued for a later run", flush=True)
                else:
                    time.sleep(5 * (attempt + 1))
    _save_state(state)
    if failed:
        print(f"{failed} requests could not be uploaded — rerun 'submit' to retry them.")
    print("Results are usually ready within an hour; run 'collect' to write them.")


def status(client: anthropic.Anthropic) -> list[dict]:
    state = _load_state()
    open_batches = []
    for entry in state["batches"]:
        batch = client.messages.batches.retrieve(entry["id"])
        entry["status"] = batch.processing_status
        counts = batch.request_counts
        print(f"{entry['id']}: {batch.processing_status} "
              f"(succeeded {counts.succeeded}, errored {counts.errored}, "
              f"processing {counts.processing})")
        if batch.processing_status != "ended":
            open_batches.append(entry)
    _save_state(state)
    return open_batches


def collect(cfg: dict) -> None:
    client = anthropic.Anthropic()
    analyzer = Analyzer(cfg)
    state = _load_state()
    remaining = []
    escalate: list[int] = []
    for entry in state["batches"]:
        batch = client.messages.batches.retrieve(entry["id"])
        if batch.processing_status != "ended":
            print(f"{entry['id']}: still {batch.processing_status}, skipping")
            remaining.append(entry)
            continue
        written = errors = 0
        for result in client.messages.batches.results(entry["id"]):
            post_id = int(result.custom_id.split("-", 1)[1])
            if result.result.type != "succeeded":
                errors += 1
                continue
            msg = result.result.message
            text_out = "".join(b.text for b in msg.content if b.type == "text")
            try:
                verdict = _parse_verdict(text_out)
            except Exception as e:
                print(f"  post {post_id}: unparseable ({e})")
                errors += 1
                continue
            # A batch request only runs the CHEAP SCREEN tier. Screen verdicts
            # are not trustworthy on fine detail (they have been observed
            # inventing VIN plate text), so anything interesting must be
            # re-checked by the full model with the reference photos before it
            # counts. Leave those posts unscored so the deep pass picks them up.
            if analyzer._needs_deep_pass(verdict):
                escalate.append(post_id)
                continue
            verdict["tier"] = "batch_screen"
            db.save_analysis(post_id, verdict)
            written += 1
        print(f"{entry['id']}: wrote {written} verdicts, {errors} errors, "
              f"{len(escalate)} escalated so far")
    state["batches"] = remaining
    _save_state(state)

    if escalate:
        print(f"\n{len(escalate)} posts flagged by the screen tier — re-checking "
              f"each with the full model and reference photos...")
        for post_id in escalate:
            post = db.post_with_media(post_id)
            if not post:
                continue
            verdict = analyzer.analyze(post["text"], post["media"])
            verdict["tier"] = "deep_after_batch"
            db.save_analysis(post_id, verdict)
            score = verdict.get("score") or 0
            if score >= cfg["matching"]["alert_threshold"]:
                print(f"  🚨 CONFIRMED post {post_id}: score={score} "
                      f"{verdict.get('reasons', '')[:300]}")
            else:
                print(f"  screen false-positive cleared: post {post_id} "
                      f"-> {score}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["submit", "status", "collect", "run"])
    parser.add_argument("--limit", type=int, default=5000)
    args = parser.parse_args()

    cfg = load_config()
    db.init()
    if args.command == "submit":
        submit(cfg, args.limit)
    elif args.command == "status":
        status(anthropic.Anthropic())
    elif args.command == "collect":
        collect(cfg)
    elif args.command == "run":
        submit(cfg, args.limit)
        client = anthropic.Anthropic()
        while status(client):
            print("waiting 60s...")
            time.sleep(60)
        collect(cfg)


if __name__ == "__main__":
    main()
