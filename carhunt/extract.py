"""Extract individual vehicle records from stored posts, so every car in a
كشف/حصر list (text OR image) becomes a searchable database row.

Usage:
    python -m carhunt.extract          # process all posts without vehicle rows
"""
import json
import re

import anthropic

from . import db
from .analyzer import MAX_POST_IMAGES, _image_block
from .config import load_config, max_output_tokens

SYSTEM = """You extract vehicle records from Sudanese social-media posts about
lost, spotted, recovered, or for-sale vehicles (war-related). Posts are in
Arabic and/or English, as text and/or photos of typed or handwritten lists.
Read Arabic-Indic numerals (٠١٢٣٤٥٦٧٨٩) as digits.

Return ONLY a JSON array (no markdown fences). One object per distinct
vehicle mentioned, in this shape (use null for unknown fields):
[{"make": "Toyota", "model": "Corolla", "color": "white",
  "year": "2015", "chassis": "1122334", "plate": "12345 خ1",
  "status": "spotted|for_sale|seeking_owner|recovered|unknown",
  "location": "Omdurman", "contact": "0912345678",
  "description": "any other identifying detail, in the post's language"}]

Rules:
- Keep chassis/plate EXACTLY as written (digits may be partial fragments).
- A single post may contain dozens of vehicles (numbered lists) — extract all.
- The post's contact number applies to every vehicle in that post's list
  unless a vehicle has its own.
- Common Sudanese Arabic vehicle words: عربة/عربات (vehicle), شاسي/شاسيه
  (chassis), لوحة (plate), مضلع (Hilux double-cab), بوكس (pickup),
  ركشة (rickshaw), تكتك (tuktuk), امجاد (Amjad van), دفار (Land Cruiser
  pickup), حافلة (bus).
- If the post contains no vehicles, return []."""


def _parse_array(text: str) -> list[dict]:
    text = text.strip()
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if not match:
        return []
    data = json.loads(match.group(0))
    return [e for e in data if isinstance(e, dict)]


def extract_post(client: anthropic.Anthropic, model: str,
                 post: dict) -> list[dict] | None:
    """Vehicles found in the post, or None if extraction failed.

    None and [] must stay distinct: [] means "no vehicles here" and is final,
    None means "we don't know" and must be retried.
    """
    content: list[dict] = []
    if post.get("text"):
        content.append({"type": "text", "text": f"Post text:\n{post['text']}"})
    for path in (post.get("media") or [])[:MAX_POST_IMAGES]:
        block = _image_block(path)
        if block:
            content.append(block)
    if not content:
        return []
    content.append({"type": "text", "text": "Extract all vehicle records as a JSON array."})

    response = client.beta.messages.create(
        model=model,
        max_tokens=max_output_tokens(),
        system=SYSTEM,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        messages=[{"role": "user", "content": content}],
    )
    if response.stop_reason == "refusal":
        print(f"  post {post['id']}: extraction refused")
        return None  # failure — distinct from "no vehicles found"
    text_out = "".join(b.text for b in response.content if b.type == "text")
    try:
        return _parse_array(text_out)
    except json.JSONDecodeError as e:
        print(f"  post {post['id']}: unparseable extraction ({e})")
        return None


def main() -> None:
    cfg = load_config()
    db.init()
    pending = db.posts_without_vehicles()
    if not pending:
        print("All posts already extracted.")
        return
    client = anthropic.Anthropic()
    # pure extraction (no matching) is cheap-model work
    model = cfg["anthropic"].get("screen_model") or cfg["anthropic"]["model"]
    print(f"Extracting vehicles from {len(pending)} post(s)...")
    total = 0
    for post in pending:
        try:
            entries = extract_post(client, model, post)
        except anthropic.APIError as e:
            print(f"  post {post['id']}: API error {e}; will retry next run")
            continue
        if entries is None:
            # refusal or malformed output — leave extracted=0 so it retries
            continue
        db.add_vehicles(post["id"], entries)
        total += len(entries)
        print(f"  post {post['id']}: {len(entries)} vehicle(s)")
    print(f"Done — {total} vehicle record(s) added.")


if __name__ == "__main__":
    main()
