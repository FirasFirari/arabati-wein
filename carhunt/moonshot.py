"""Moonshot AI (Kimi) as a scoring provider, for when Claude is the blocked budget.

Moonshot speaks the OpenAI Chat Completions wire format, so this reuses the
fallback module's Anthropic->OpenAI block converter and exposes exactly the
same three things `openai_backlog.py` touches: `.available`, `.complete()` and
`.last_usage`. Swap it in for `Analyzer.fallback` and the whole backlog runner
— grounding, identifier re-check, spend meter, resumability — works unchanged.

Verified against the platform docs on 2026-08-28. Where Moonshot differs from
OpenAI it differs in ways that break the request, not in ways that degrade
quietly:

  * Base URL must be set explicitly: `https://api.moonshot.ai/v1` for the
    international platform. Mainland China is a SEPARATE platform at
    `https://api.moonshot.cn/v1` with its own keys and RMB billing — a key
    from one returns 401 on the other, which is the first thing to check when
    a fresh key "does not work".
  * `max_tokens` is deprecated in favour of `max_completion_tokens`.
  * There is no `reasoning_effort`. The `effort` argument is accepted and
    ignored so the call sites do not have to branch.
  * There is no image `detail` parameter. On OpenAI, detail="low" was the
    cost lever that made cheap screening of photos viable (~215 tokens vs
    ~810). Here every image is billed at full resolution, so DO NOT send
    photos through a screening pass expecting them to be cheap — route image
    posts straight to the deep tier, which is what the backlog runner
    already does for its own (better) reason: a chassis stamp is unreadable
    in a thumbnail.
  * Prefix caching is automatic — no cache object to create, no parameter.
    Cache-hit input on kimi-k2.6 is $0.16/M against $0.95/M fresh, so the
    system prompt going first and byte-identical every call is worth real
    money across a 3,600-post backlog.
  * Rate limits are tier-gated by CUMULATIVE RECHARGE, not usage: Tier 0 ($1)
    allows 3 requests per minute and 1 concurrent request, which turns this
    backlog into a 20-hour run. Tier 1 ($10) allows 200 RPM / 50 concurrent.
    429s are therefore expected rather than exceptional, so the client
    retries them itself.

Model choice (docs, 2026-08-28): `kimi-k2.6` takes text and images at
$0.95/$4.00 per 1M; `kimi-k3` is the flagship at $3.00/$15.00. The
`moonshot-v1-*` and `kimi-k2.5` families are SUNSET on 2026-08-31 — three days
from this writing — so nothing here may depend on them.
"""
import os

from .fallback import _to_openai_content

try:
    import openai
except ImportError:  # optional dependency
    openai = None

INTERNATIONAL_BASE_URL = "https://api.moonshot.ai/v1"
CHINA_BASE_URL = "https://api.moonshot.cn/v1"


def _content(blocks: list[dict]) -> list[dict]:
    """Anthropic blocks -> Moonshot's (OpenAI-shaped) content array.

    Same conversion as the OpenAI path minus the `detail` key: Moonshot has no
    such parameter, and an unrecognised field inside `image_url` is at best
    ignored and at worst a 400 on a request carrying ten base64 photos.
    """
    out = _to_openai_content(blocks)
    for part in out:
        if part.get("type") == "image_url":
            part["image_url"].pop("detail", None)
    return out


class MoonshotClient:
    """Thin wrapper exposing the one call the analyzer and backlog runner need."""

    name = "moonshot"

    def __init__(self, cfg: dict):
        ms = (cfg.get("moonshot") or {})
        self.enabled = bool(ms.get("enabled", True)) and openai is not None
        self.model = ms.get("model", "kimi-k2.6")
        self.screen_model = ms.get("screen_model", "kimi-k2.6")
        self.max_output = int(ms.get("max_output_tokens", 16000))
        # Omitted from the request unless config sets it. kimi-k2.6 REJECTS
        # every value but 1 — `invalid temperature: only 1 is allowed for this
        # model`, a 400 on all 20 posts of the first benchmark — so there is
        # no temperature lever here to turn transcription deterministic, and
        # sending the sane-looking 0.0 fails the whole run.
        self.temperature = ms.get("temperature")
        # kimi-k2.6 runs with THINKING ON by default and bills the reasoning as
        # output tokens. Measured on this project's own posts: a 120-character
        # post with one vehicle in it cost ~1,900 output tokens and $0.009 —
        # roughly 10x the ~150 tokens the verdict JSON itself needs. Set
        # moonshot.thinking to false to turn it off; None leaves the model's
        # own default alone. Whether it is worth paying for is a question for
        # the benchmark, not for taste: --benchmark measures recall both ways.
        self.thinking = ms.get("thinking")
        self.base_url = ms.get("base_url") or INTERNATIONAL_BASE_URL
        key = os.environ.get("MOONSHOT_API_KEY") or ms.get("api_key")
        self.client = None
        if self.enabled and key:
            self.client = openai.OpenAI(
                api_key=key, base_url=self.base_url,
                # 429 is a normal operating condition on the lower tiers, not
                # an error worth losing a post over; the SDK backs off for us.
                max_retries=int(ms.get("max_retries", 6)),
                timeout=float(ms.get("timeout", 600)),
            )
        self.last_usage: dict | None = None

    @property
    def available(self) -> bool:
        return self.client is not None

    def list_models(self) -> list[str]:
        """Ask the account what it can actually call.

        Worth doing before a long run: model ids move (the whole platform
        renamed from Moonshot to Kimi and the v1 family is being retired this
        month), and a typo'd model id fails 3,600 times in a row.
        """
        return sorted(m.id for m in self.client.models.list().data)

    def complete(self, system: str, blocks: list[dict], deep: bool,
                 model: str | None = None, detail: str | None = None,
                 effort: str | None = None) -> str:
        """Return raw model text for the same prompt Claude would have seen.

        `detail` and `effort` are accepted for signature compatibility with
        OpenAIFallback and ignored — Moonshot has neither parameter.
        """
        model = model or (self.model if deep else self.screen_model)
        # system first and byte-identical every call, for the automatic
        # prefix cache; this prompt is comfortably long enough to matter
        kwargs = {}
        if self.temperature is not None:
            kwargs["temperature"] = self.temperature
        if self.thinking is not None:
            # k2.6 replaced the older `enable_thinking` with `thinking`, and
            # returns its reasoning in a `reasoning` field rather than
            # `reasoning_content`. Passed through extra_body because it is a
            # Moonshot extension the OpenAI SDK knows nothing about.
            kwargs["extra_body"] = {
                "chat_template_kwargs": {"thinking": bool(self.thinking)}}
        response = self.client.chat.completions.create(
            model=model,
            max_completion_tokens=self.max_output,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": _content(blocks)},
            ],
            **kwargs,
        )
        usage = response.usage
        cached = (getattr(getattr(usage, "prompt_tokens_details", None),
                          "cached_tokens", None)
                  or getattr(usage, "cached_tokens", None) or 0)
        self.last_usage = {
            "model": model,
            "input": usage.prompt_tokens,
            "cached_input": cached,
            "output": usage.completion_tokens,
        }
        choice = response.choices[0]
        text = choice.message.content or ""
        # A truncated answer is half a JSON object, which surfaces downstream
        # as a baffling parse error. Say what actually happened so the post is
        # left queued for a retry rather than written off — this is the exact
        # failure that silently cost this project ~130,000 vehicle records
        # when a 16,000-token cap cut كشف lists off mid-write.
        if choice.finish_reason == "length" or not text.strip():
            raise RuntimeError(
                f"{model} returned no usable verdict "
                f"(finish_reason={choice.finish_reason}, "
                f"output_tokens={usage.completion_tokens}, "
                f"limit={self.max_output}) — raise moonshot.max_output_tokens")
        return text
