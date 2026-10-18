"""Gemini models through VERTEX AI — the lane the free-trial credit can pay for.

Added 2026-09-01, and the reason is a billing rule, not a technical preference:

  **Since March 2026 the $300 Google Cloud free-trial credit explicitly does
  NOT cover Gemini API usage in AI Studio.** Billing can be linked correctly,
  the console can show a paid account, and the API will still answer with
  `GenerateRequestsPerDayPerProjectPerModel-FreeTier` at 20 requests/day —
  measured on this project, six probes over five minutes. AI Studio's paid
  tier wants a *Prepay* plan and prepaid credits bought with real money.
  The grandfather clause covers credits granted before the change; this
  account's credit was issued 2026-08-28, so it does not apply.

  **Vertex AI is an ordinary Google Cloud service and the credit DOES cover
  it**, and it serves the same Gemini models. So the models, the prompt, the
  JSON contract and every downstream gate stay exactly as benchmarked; only
  the endpoint and the authentication change.

Two differences from carhunt/gemini.py, both of which break the request
rather than degrade it:

  * AUTH IS OAUTH, NOT AN API KEY. Vertex authenticates with a short-lived
    bearer token from Application Default Credentials. There is no key to put
    in config.yaml. Set it up once, interactively:

        gcloud auth application-default login
        gcloud services enable aiplatform.googleapis.com --project <PROJECT>

    The token expires roughly hourly, so this client refreshes it before
    every call rather than caching one for the length of a multi-hour backlog
    run — a run that dies at hour two with a 401 has to be resumed by hand.
  * MODEL IDS ARE PREFIXED `google/`. On the OpenAI-compatible endpoint the
    id is `google/gemini-3.7-flash`, not `gemini-3.7-flash`. An unprefixed id
    404s once per post for the length of the backlog, which is exactly the
    failure the AI Studio lane already taught us to check for first with
    --list-models.

Region: `global` is the default and is the right choice for throughput —
model availability per region varies and a regional endpoint can 404 a model
that `global` serves.
"""
import os

from .fallback import _to_openai_content

try:
    import openai
except ImportError:  # optional dependency
    openai = None

try:
    import google.auth
    import google.auth.transport.requests
except ImportError:  # optional dependency
    google = None

SCOPE = "https://www.googleapis.com/auth/cloud-platform"


def _endpoint(project: str, location: str) -> str:
    """OpenAI-compatible Vertex endpoint. `global` has no region prefix."""
    host = ("aiplatform.googleapis.com" if location == "global"
            else f"{location}-aiplatform.googleapis.com")
    return (f"https://{host}/v1/projects/{project}"
            f"/locations/{location}/endpoints/openapi")


def _content(blocks: list[dict]) -> list[dict]:
    """Anthropic blocks -> Vertex's (OpenAI-shaped) content array."""
    out = _to_openai_content(blocks)
    for part in out:
        if part.get("type") == "image_url":
            part["image_url"].pop("detail", None)
    return out


class VertexClient:
    """Same three-method interface the backlog runner needs: available/complete/last_usage."""

    name = "vertex"

    def __init__(self, cfg: dict):
        vx = (cfg.get("vertex") or {})
        self.enabled = bool(vx.get("enabled", True)) and openai is not None and google is not None
        self.model = vx.get("model", "gemini-3.7-flash")
        self.screen_model = vx.get("screen_model", self.model)
        self.max_output = int(vx.get("max_output_tokens", 60000))
        self.temperature = vx.get("temperature")
        self.effort = vx.get("effort")
        self.location = vx.get("location", "global")
        self.timeout = float(vx.get("timeout", 900))
        self.max_retries = int(vx.get("max_retries", 3))
        self.project = (os.environ.get("GOOGLE_CLOUD_PROJECT")
                        or vx.get("project"))
        self._creds = None
        self._auth_error: str | None = None
        if self.enabled:
            try:
                creds, adc_project = google.auth.default(scopes=[SCOPE])
                self._creds = creds
                self.project = self.project or adc_project
            except Exception as exc:  # no ADC configured yet
                self._auth_error = str(exc)
        self.last_usage: dict | None = None

    @property
    def available(self) -> bool:
        return bool(self._creds and self.project)

    def why_unavailable(self) -> str:
        if openai is None:
            return "the openai package is not installed"
        if google is None:
            return "google-auth is not installed (pip install google-auth)"
        if self._auth_error:
            return (f"Application Default Credentials are not set up "
                    f"({self._auth_error.splitlines()[0]}). Run: "
                    f"gcloud auth application-default login")
        if not self.project:
            return ("no project id — set vertex.project in config.yaml or "
                    "GOOGLE_CLOUD_PROJECT in the environment")
        return "unknown"

    def _token(self) -> str:
        """A fresh bearer token. Refreshed per call — see the module docstring."""
        if not self._creds.valid:
            self._creds.refresh(google.auth.transport.requests.Request())
        return self._creds.token

    def _client(self):
        return openai.OpenAI(
            api_key=self._token(),
            base_url=_endpoint(self.project, self.location),
            max_retries=self.max_retries,
            timeout=self.timeout,
        )

    def list_models(self) -> list[str]:
        """Vertex's OpenAI shim does not implement /models; probe instead."""
        out = []
        for m in ("gemini-3.7-flash", "gemini-3.6-flash", "gemini-3.5-flash",
                  "gemini-3.5-flash-lite", "gemini-3.1-flash-lite"):
            try:
                self.complete('Return JSON {"ok":true}',
                              [{"type": "text", "text": "ok"}],
                              deep=False, model=m)
                out.append(f"OK    {m}")
            except Exception as exc:
                out.append(f"ERR   {m}: {str(exc)[:110]}")
        return out

    def complete(self, system: str, blocks: list[dict], deep: bool,
                 model: str | None = None, detail: str | None = None,
                 effort: str | None = None) -> str:
        """Return raw model text for the same prompt Claude would have seen."""
        model = model or (self.model if deep else self.screen_model)
        wire = model if model.startswith("google/") else f"google/{model}"
        kwargs = {}
        if self.temperature is not None:
            kwargs["temperature"] = self.temperature
        if self.effort is not None:
            kwargs["reasoning_effort"] = self.effort
        response = self._client().chat.completions.create(
            model=wire,
            max_tokens=self.max_output,
            response_format={"type": "json_object"},
            messages=[
                # system first and byte-identical every call, for the implicit
                # prefix cache — it is 38.6M of the backlog's 43.8M input
                # tokens, so this placement is most of the bill.
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
        if choice.finish_reason == "length" or not text.strip():
            raise RuntimeError(
                f"{model} returned no usable verdict "
                f"(finish_reason={choice.finish_reason}, "
                f"output_tokens={usage.completion_tokens}, "
                f"limit={self.max_output}) — reasoning shares the output "
                f"budget; use --skip-over-chars for mega lists")
        return text
