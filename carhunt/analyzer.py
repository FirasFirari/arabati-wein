"""Match incoming posts against the car profile using the Claude API."""
import base64
import json
import pathlib
import re

import anthropic

from .config import REFERENCE_PHOTO_DIR, max_output_tokens
from .fallback import OpenAIFallback

MEDIA_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
}

MAX_POST_IMAGES = 10

VERDICT_INSTRUCTIONS = """\
Respond with ONLY a JSON object, no markdown fences, in this exact shape:
{
  "is_car_post": true/false,          // is this post offering/showing a car at all?
  "model_match": true/false,          // could it be the same make/model/generation?
  "score": 0-100,                     // likelihood this is THE car described in the profile
  "reasons": "short explanation in English",
  "extracted": {
    "described_vehicle": "make/model/year/color as stated or visible, else null",
    "plate": "any visible or mentioned plate number, else null",
    "vin": "any visible or mentioned chassis/VIN, else null",
    "location": "any location mentioned, else null",
    "price": "any price mentioned, else null",
    "contact": "any phone/contact mentioned, else null"
  },
  "vehicles": [
    {"make": "Toyota", "model": "Corolla", "color": "white", "year": "2015",
     "chassis": "1122334", "plate": "12345 خ1",
     "status": "spotted|for_sale|seeking_owner|recovered|unknown",
     "location": "Omdurman", "contact": "0912345678",
     "description": "other identifying details, in the post's language"}
  ]
}
"vehicles" rules: one object per DISTINCT vehicle mentioned or shown in the
post (a numbered كشف/حصر list may contain dozens — include them ALL; use
null for unknown fields). Keep chassis/plate EXACTLY as written, partial
fragments included.
A BARE, UNLABELLED number in a كشف/حصر list line — "659311 بوكسي",
"٧٦١٤٣٣٧", "دفار صغير 6605009" — is a CHASSIS number, and belongs in
"chassis". These lists are published by recovery offices and inventory
vehicles by chassis; the plate is exactly what a thief changes. Use "plate"
ONLY where the post says so (رقم اللوحة، لوحة، نمرة) or the format is
plainly a plate (digits plus an Arabic letter group, e.g. "12345 خ1").
Never leave an identifier only inside "description" — every number you read
must ALSO appear in "chassis" or "plate". If you cannot tell which it is,
put it in "chassis": leaving it out of both is the one outcome that loses
it, because the registry is searched by chassis. The post's contact number applies to every vehicle in
its list unless a vehicle has its own. If the post contains no vehicles,
"vehicles" is []. No comments in the JSON output.
Write "make" and "model" in canonical English regardless of Arabic spelling
or local nickname — Sudanese posts vary wildly: كورولا/كورلا/كرلا/كريلا are
all Toyota Corolla; سحلية/منفوخة = Hyundai Avante; دبدوب/فيرنا = Hyundai
Accent; مضلع = Toyota Hilux double-cab; دفار = Land Cruiser pickup; كلك =
Hyundai Getz; بومه = Mitsubishi Lancer; حرجلي = older Toyota Camry;
اتوز = Hyundai Atos; مورنينق = Kia Picanto/Morning. Preserve the post's
original wording in "description".

CRITICAL — these Sudanese words name a BODY TYPE, not a model, and are often
written with the make separately ("شريحه تايوتا", "شريحه نيسان"). NEVER map
them to a sedan, and never guess Corolla just because the make is Toyota:
  شريحة / شريحه  = van, minibus or flatbed (Toyota's is the HIACE;
                   "شريحه نيسان" is a Nissan van — not a Corolla)
  بوكس / بوكسي   = pickup
  حافلة / باص    = bus
  لوري / قلاب    = truck / tipper
  ركشة / تكتك / دقو ورا = three-wheeler (rickshaw / tuktuk)
  امجاد          = Amjad van
If the body type is given but the model is not, set "model" to the body type
in English (e.g. "van (شريحة)") and leave the specific model null. Do NOT
invent a model name.
Scoring guide: 0-20 not a car post or clearly a different vehicle; 21-39 same
model class but nothing specific; 40-69 same model+color+era with at least one
suggestive detail; 70-100 specific identifiers align (VIN fragment, plate,
matching distinguishing marks). Plates are often swapped and cars repainted, so
weigh unique physical marks and the VIN far above the plate."""


def _build_system(car: dict) -> str:
    features = "\n".join(f"- {f}" for f in car.get("distinguishing_features", []))
    return f"""You help a family locate their car, which was taken from them \
during the war in Sudan. Cars like it are resold through social media posts in \
Arabic and English. You examine one post at a time (text and/or photos) and \
judge how likely it shows this exact vehicle.

TARGET VEHICLE PROFILE:
- Make/model: {car['make']} {car['model']} ({car.get('model_code', '')}), made in {car.get('made_in', '?')}
- Year: {car['year']} (production {car.get('production_date', '?')})
- Color: {car['color']}
- Chassis/VIN: {car['vin']}
- Engine: {car.get('engine', '?')}
- Plate at time of loss: {car.get('plate', 'unknown')} (registered {car.get('registered_at', '?')})
- Distinguishing features:
{features}

Reference photos of the vehicle and its documents may be provided first,
labeled REFERENCE. Everything after the marker POST is the social-media post
under evaluation. Read Arabic text carefully, including Arabic-Indic numerals
(٠١٢٣٤٥٦٧٨٩). Be rigorous: a white 2013-2016 Corolla alone is common; only
specific identifiers justify a high score.

{VERDICT_INSTRUCTIONS}"""


def sniff_media_type(head: bytes) -> str | None:
    """Real image type from magic bytes — extensions lie.

    Facebook CDN links can return an HTML login page; saved as .jpg it would
    otherwise be sent to the API as a JPEG and rejected.
    """
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return "image/webp"
    return None


def _image_block(path: str | pathlib.Path, cache: bool = False) -> dict | None:
    p = pathlib.Path(path)
    if not p.is_file():
        return None
    raw = p.read_bytes()
    media_type = sniff_media_type(raw[:16])
    if media_type is None:
        return None  # not a real image (HTML error page, video, corrupt file)
    data = base64.standard_b64encode(raw).decode("utf-8")
    block = {
        "type": "image",
        "source": {"type": "base64", "media_type": media_type, "data": data},
    }
    if cache:
        block["cache_control"] = {"type": "ephemeral"}
    return block


def _parse_verdict(text: str) -> dict:
    text = text.strip()
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError(f"No JSON object in model response: {text[:200]}")
    verdict = json.loads(match.group(0))
    verdict["score"] = int(verdict.get("score", 0))
    return verdict


class Analyzer:
    def __init__(self, cfg: dict):
        self.model = cfg["anthropic"]["model"]
        # cheap screening pass; anything interesting escalates to self.model
        self.screen_model = cfg["anthropic"].get("screen_model")
        self.client = anthropic.Anthropic()
        self.system = _build_system(cfg["car"])
        self.use_reference_photos = bool(
            (cfg.get("matching") or {}).get("use_reference_photos", True))
        self.reference_blocks = (self._load_reference_blocks()
                                 if self.use_reference_photos else [])
        self.fallback = OpenAIFallback(cfg)

    def _load_reference_blocks(self) -> list[dict]:
        paths = sorted(
            p for p in REFERENCE_PHOTO_DIR.iterdir()
            if p.suffix.lower() in MEDIA_TYPES
        ) if REFERENCE_PHOTO_DIR.is_dir() else []
        blocks: list[dict] = []
        if paths:
            blocks.append({"type": "text", "text": "REFERENCE photos of the target vehicle:"})
            for p in paths:
                block = _image_block(p)
                if block:
                    blocks.append(block)
        # Cache the stable prefix (system + reference photos) across requests.
        if blocks:
            blocks[-1]["cache_control"] = {"type": "ephemeral"}
        return blocks

    def analyze(self, text: str | None, image_paths: list[str]) -> dict:
        """Score one post. Returns the verdict dict (score always present).

        Two tiers: a cheap screen pass (no reference photos) handles the bulk
        stream; anything scoring >= 25, containing a Corolla, or unparseable
        escalates to the full model with reference-photo comparison.
        """
        post_blocks: list[dict] = [{"type": "text", "text": "POST under evaluation:"}]
        if text:
            post_blocks.append({"type": "text", "text": f"Post text:\n{text}"})
        for path in image_paths[:MAX_POST_IMAGES]:
            block = _image_block(path)
            if block:
                post_blocks.append(block)
        if len(post_blocks) == 1:
            return {"score": 0, "is_car_post": False,
                    "reasons": "Empty post (no text or readable images)."}

        if self.screen_model:
            # the screen pass only needs a couple of images to tell whether a
            # post is worth the deep pass; escalation gets the full set
            screen_blocks = [b for b in post_blocks if b["type"] == "text"]
            screen_blocks += [b for b in post_blocks if b["type"] == "image"][:2]
            verdict = self._call(self.screen_model, screen_blocks, with_refs=False)
            if not self._needs_deep_pass(verdict):
                verdict["tier"] = "screen"
                return verdict
        verdict = self._call(self.model, post_blocks, with_refs=True)
        verdict["tier"] = "deep"
        return verdict

    def _needs_deep_pass(self, verdict: dict) -> bool:
        if verdict.get("error"):
            # API errors would fail on the deep model too; parse errors deserve a retry
            return "unparseable_verdict" in verdict["error"]
        if (verdict.get("score") or 0) >= 25 or verdict.get("model_match"):
            return True
        from .aliases import model_keys_in
        for v in verdict.get("vehicles") or []:
            if not isinstance(v, dict):
                continue
            blob = f"{v.get('make') or ''} {v.get('model') or ''} {v.get('description') or ''}"
            if "toyota corolla" in model_keys_in(blob):
                return True
        return False

    def _fallback(self, content: list[dict], with_refs: bool, claude_error: str) -> dict:
        """Try OpenAI when Claude cannot answer; otherwise report the error."""
        if not (self.fallback and self.fallback.available):
            return {"score": None, "error": claude_error}
        try:
            text_out = self.fallback.complete(self.system, content, deep=with_refs)
        except Exception as e:
            return {"score": None,
                    "error": f"{claude_error} | fallback also failed: {e}"}
        try:
            verdict = _parse_verdict(text_out)
        except (ValueError, json.JSONDecodeError) as e:
            return {"score": None,
                    "error": f"{claude_error} | fallback unparseable: {e}"}
        verdict["provider"] = "openai"
        verdict["claude_error"] = claude_error[:200]
        return verdict

    def _call(self, model: str, post_blocks: list[dict], with_refs: bool) -> dict:
        content = (self.reference_blocks + post_blocks) if with_refs else post_blocks
        system = [{"type": "text", "text": self.system,
                   "cache_control": {"type": "ephemeral"}}]
        # STREAMED, then collected whole. max_output_tokens() is 64,000 (a كشف
        # list needs ~56k to write back), and the SDK refuses a non-streaming
        # request that large before sending it: "Streaming is required for
        # operations that may take longer than 10 minutes". That ValueError is
        # not an APIStatusError, so it escaped every handler below and killed
        # every live verdict — 192 tracebacks in monitor.log by 2026-10-08.
        try:
            if with_refs:
                with self.client.beta.messages.stream(
                    model=model, max_tokens=max_output_tokens(), system=system,
                    betas=["server-side-fallback-2026-07-01"],
                    fallbacks="default",
                    messages=[{"role": "user", "content": content}],
                ) as stream:
                    response = stream.get_final_message()
            else:
                with self.client.messages.stream(
                    model=model, max_tokens=max_output_tokens(), system=system,
                    messages=[{"role": "user", "content": content}],
                ) as stream:
                    response = stream.get_final_message()
        except anthropic.RateLimitError as e:
            return self._fallback(content, with_refs, f"rate_limited: {e}")
        except anthropic.APIStatusError as e:
            return self._fallback(
                content, with_refs, f"api_error {e.status_code}: {e.message}")
        except anthropic.APIConnectionError as e:
            return self._fallback(content, with_refs, f"connection_error: {e}")

        if response.stop_reason == "refusal":
            detail = response.stop_details.explanation if response.stop_details else ""
            return {"score": None, "error": f"model_refusal: {detail}"}

        text_out = "".join(b.text for b in response.content if b.type == "text")
        try:
            return _parse_verdict(text_out)
        except (ValueError, json.JSONDecodeError) as e:
            return {"score": None, "error": f"unparseable_verdict: {e}",
                    "raw": text_out[:1000]}
