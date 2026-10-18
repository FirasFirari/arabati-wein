"""Google Gemini (AI Studio) as a scoring provider — the FREE-TIER lane.

Chosen 2026-08-31 because the Sonnet subagent waves were consuming the whole
Claude Code weekly subscription cap. AI Studio's free tier allows on the
order of 1,000+ requests/day on the Flash models at $0, which covers most of
the daily intake if the quality holds. QUALITY IS THE GATE, not the price:
this client must pass `openai_backlog.py --benchmark` + `audit_verdicts.py`
against known-good Sonnet extractions before a single row is written, exactly
the process that admitted Kimi for text and rejected it for images.

Gemini speaks the OpenAI Chat Completions wire format at
`https://generativelanguage.googleapis.com/v1beta/openai/`, so like
carhunt/moonshot.py this reuses the fallback module's block converter and
exposes the same three things `openai_backlog.py` touches: `.available`,
`.complete()` and `.last_usage`.

Where Gemini differs, verified against ai.google.dev docs 2026-08-31:

  * KEY AND TIER ARE PER-PROJECT. A key from a Google Cloud project with
    billing linked bills at PAID rates; a key made in AI Studio on a
    project with no billing stays on the free tier and simply 429s at the
    quota. The google_vision key in config.yaml belongs to a BILLED project
    — do not reuse it here expecting free. Make a free key at
    https://aistudio.google.com/apikey on a project without billing.
  * FREE-TIER LIMITS ARE THE OPERATING CONDITION: ~5-15 requests/minute and
    a daily cap on Flash models. This client throttles itself to
    `gemini.rpm` requests/minute across all worker threads, and a 429 after
    retries just leaves the post queued — the runner is resumable, so the
    backlog drains at quota speed across days. That is the deal: $0, slower.
  * Reasoning ("thinking") is on by default for the Flash 3.x models and its
    tokens SHARE the output budget, like Kimi — so the Kimi rule applies:
    use --skip-over-chars 8000 and leave mega كشف lists to the local
    subagent pipeline until measured otherwise.
  * Temperature is left at the model default DELIBERATELY. Google documents
    the Gemini 3 thinking models as tuned for their default; forcing 0.0
    is the kind of "obviously right" knob that has to earn its place in a
    benchmark first, not be assumed.
  * `reasoning_effort` is accepted by the compat layer on thinking models.
    The `effort` argument arrives from call sites for OpenAI compatibility;
    it is forwarded only if `gemini.effort` is set in config, otherwise the
    model default stands (quality first).
"""
import os
import threading
import time

from .fallback import _to_openai_content

try:
    import openai
except ImportError:  # optional dependency
    openai = None

BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"


def _content(blocks: list[dict]) -> list[dict]:
    """Anthropic blocks -> Gemini's (OpenAI-shaped) content array.

    Same conversion as the OpenAI path minus the `detail` key: Gemini has no
    such parameter and unknown fields inside image_url are not worth the risk
    on a request carrying base64 photos.
    """
    out = _to_openai_content(blocks)
    for part in out:
        if part.get("type") == "image_url":
            part["image_url"].pop("detail", None)
    return out


class _Throttle:
    """Cross-thread requests-per-minute gate.

    The free tier enforces RPM server-side with 429s; hitting it constantly
    turns the SDK's retry budget into the run's clock. Spacing request STARTS
    at 60/rpm seconds keeps the run just under the ceiling instead.
    """

    def __init__(self, rpm: float):
        self.interval = 60.0 / max(rpm, 0.1)
        self._lock = threading.Lock()
        self._next_at = 0.0

    def wait(self) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                if now >= self._next_at:
                    self._next_at = now + self.interval
                    return
                delay = self._next_at - now
            time.sleep(min(delay, 2.0))


class GeminiClient:
    """Thin wrapper exposing the one call the analyzer and backlog runner need."""

    name = "gemini"

    def __init__(self, cfg: dict):
        gm = (cfg.get("gemini") or {})
        self.enabled = bool(gm.get("enabled", True)) and openai is not None
        self.model = gm.get("model", "gemini-3-flash")
        self.screen_model = gm.get("screen_model", self.model)
        self.max_output = int(gm.get("max_output_tokens", 60000))
        self.temperature = gm.get("temperature")  # omitted unless set
        self.effort = gm.get("effort")            # omitted unless set
        self.base_url = gm.get("base_url") or BASE_URL
        self._throttle = _Throttle(float(gm.get("rpm", 8)))
        key = os.environ.get("GEMINI_API_KEY") or gm.get("api_key")
        self.client = None
        if self.enabled and key:
            self.client = openai.OpenAI(
                api_key=key, base_url=self.base_url,
                # 429 is the free tier talking, not an error; back off and
                # retry. A post that still fails stays queued for tomorrow.
                max_retries=int(gm.get("max_retries", 6)),
                timeout=float(gm.get("timeout", 900)),
            )
        self.last_usage: dict | None = None

    @property
    def available(self) -> bool:
        return self.client is not None

    def list_models(self) -> list[str]:
        """Ask the key what it can actually call — ids move between previews."""
        return sorted(m.id for m in self.client.models.list().data)

    def complete(self, system: str, blocks: list[dict], deep: bool,
                 model: str | None = None, detail: str | None = None,
                 effort: str | None = None) -> str:
        """Return raw model text for the same prompt Claude would have seen.

        `detail` is accepted for signature compatibility and ignored.
        `effort` from the call site is likewise ignored unless config sets
        gemini.effort — the free tier makes reasoning tokens free, and
        quality is the constraint here, not spend.
        """
        model = model or (self.model if deep else self.screen_model)
        kwargs = {}
        if self.temperature is not None:
            kwargs["temperature"] = self.temperature
        if self.effort is not None:
            kwargs["reasoning_effort"] = self.effort
        self._throttle.wait()
        response = self.client.chat.completions.create(
            model=model,
            max_tokens=self.max_output,
            response_format={"type": "json_object"},
            messages=[
                # system first and byte-identical every call: Gemini caches
                # prefixes implicitly on 2.5+/3.x models, and on a paid key
                # cached input is billed at a quarter rate.
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
        # as a baffling parse error. Say what actually happened so the post
        # is left queued for a retry rather than written off — the exact
        # failure shape that silently cost ~130,000 vehicle records once.
        if choice.finish_reason == "length" or not text.strip():
            raise RuntimeError(
                f"{model} returned no usable verdict "
                f"(finish_reason={choice.finish_reason}, "
                f"output_tokens={usage.completion_tokens}, "
                f"limit={self.max_output}) — reasoning shares the output "
                f"budget; use --skip-over-chars for mega lists")
        return text
