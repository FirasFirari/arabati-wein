"""OpenAI fallback for when the Claude API is unavailable.

Claude stays primary; this runs only when a Claude call fails for a reason a
retry won't fix (spend limit, rate limit, outage). Same system prompt and the
same JSON contract, so verdicts stay comparable across providers — the
`provider` field on each verdict records which one answered.

Enable by setting OPENAI_API_KEY (or fallback.api_key in config.yaml).

Written against the GPT-5.6 API, which differs from GPT-4o in ways that are
not optional (all four verified live, 2026-08-23):
  * `max_tokens` is REJECTED — it must be `max_completion_tokens`. The old
    code sent `max_tokens`, so this fallback could never once have answered.
  * `reasoning_effort` bills thinking as output tokens at the output rate.
    Screening runs at "none" (0 reasoning tokens measured); the deep pass
    gets "low", which is enough for a photo comparison.
  * JSON mode makes the JSON contract structural rather than hopeful — an
    unparseable verdict costs a whole wasted call, and the analyzer's system
    prompt already demands a bare JSON object.
  * image `detail` is a real cost lever: measured on this project's own
    media, "low" is ~215 input tokens per image against ~810 for "high", so
    screening looks at thumbnails and only the deep pass looks closely.
Stay on Chat Completions: the July 2026 reasoning-token over-billing bug
(usage.output_tokens re-summed up to ~9x) is specific to the Responses API.
"""
import os

try:
    import openai
except ImportError:  # optional dependency
    openai = None


def _to_openai_content(blocks: list[dict], detail: str = "high") -> list[dict]:
    """Convert Anthropic content blocks to OpenAI's chat format."""
    out = []
    for block in blocks:
        if block["type"] == "text":
            out.append({"type": "text", "text": block["text"]})
        elif block["type"] == "image":
            src = block["source"]
            out.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:{src['media_type']};base64,{src['data']}",
                    "detail": detail,
                },
            })
    return out


class OpenAIFallback:
    """Thin wrapper exposing the one call the analyzer needs."""

    def __init__(self, cfg: dict):
        fb = (cfg.get("fallback") or {})
        self.enabled = bool(fb.get("enabled", True)) and openai is not None
        self.model = fb.get("model", "gpt-5.6-sol")
        self.screen_model = fb.get("screen_model", "gpt-5.6-luna")
        self.effort = fb.get("effort", "low")
        self.screen_effort = fb.get("screen_effort", "none")
        self.max_output = int(fb.get("max_output_tokens", 8000))
        key = os.environ.get("OPENAI_API_KEY") or fb.get("api_key")
        self.client = openai.OpenAI(api_key=key) if (self.enabled and key) else None
        # token usage of the most recent call, so a caller running a whole
        # backlog can report real spend instead of an estimate
        self.last_usage: dict | None = None

    @property
    def available(self) -> bool:
        return self.client is not None

    def complete(self, system: str, blocks: list[dict], deep: bool,
                 model: str | None = None, detail: str | None = None,
                 effort: str | None = None) -> str:
        """Return raw model text for the same prompt Claude would have seen.

        The overrides exist because tier and image detail are not the same
        decision: reading digits off a photo needs "high" detail but not an
        expensive model — measured, the cheap tier at high detail returns the
        same identifiers as the dear one on 5 posts out of 6, at 1/19th the
        price.
        """
        model = model or (self.model if deep else self.screen_model)
        detail = detail or ("high" if deep else "low")
        effort = effort or (self.effort if deep else self.screen_effort)
        # system first and byte-identical every call: OpenAI caches a repeated
        # prefix automatically at a tenth of the input price, and this one is
        # comfortably over the 1024-token minimum
        response = self.client.chat.completions.create(
            model=model,
            max_completion_tokens=self.max_output,
            reasoning_effort=effort,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": _to_openai_content(blocks, detail)},
            ],
        )
        usage = response.usage
        cached = getattr(getattr(usage, "prompt_tokens_details", None),
                         "cached_tokens", 0) or 0
        self.last_usage = {
            "model": model,
            "input": usage.prompt_tokens,
            "cached_input": cached,
            "output": usage.completion_tokens,
        }
        choice = response.choices[0]
        text = choice.message.content or ""
        # A truncated answer is half a JSON object, which surfaces downstream
        # as a baffling parse error; say what actually happened so the post is
        # left queued for a retry rather than written off.
        if choice.finish_reason == "length" or not text.strip():
            raise RuntimeError(
                f"{model} returned no usable verdict "
                f"(finish_reason={choice.finish_reason}, "
                f"output_tokens={usage.completion_tokens}, "
                f"limit={self.max_output}) — raise fallback.max_output_tokens")
        return text
