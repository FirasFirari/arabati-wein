"""Load config.yaml from the project root.

`config.yaml` is private (gitignored): it holds API keys, Telegram secrets and
the profile of the car this deployment is looking for. `config.example.yaml`
documents every key. Nothing in the code base may hardcode an identifier from
the car profile — read it from `load_config()["car"]` so a public copy of the
code carries nobody's chassis or plate.
"""
import pathlib

import yaml

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.yaml"
DATA_DIR = PROJECT_ROOT / "data"
MEDIA_DIR = DATA_DIR / "media"
REFERENCE_PHOTO_DIR = PROJECT_ROOT / "reference" / "photos"


def load_config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    REFERENCE_PHOTO_DIR.mkdir(parents=True, exist_ok=True)
    return cfg





def max_output_tokens(default: int = 64000) -> int:
    """Output-token ceiling for a single verdict, on either provider.

    One knob, because the number is a property of the WORK, not the provider:
    a كشف list with 1,400 chassis numbers needs roughly 40 output tokens per
    vehicle object to write back, so ~56,000 tokens. This was hardcoded to
    16,000 in three call sites, and the 2026-08-24 audit found 36 posts the
    deep tier had read correctly and then been cut off mid-JSON while writing
    — post 10362 returned 16 vehicles out of 1,402 numbers. Set
    `fallback.max_output_tokens` in config.yaml to change it.
    """
    try:
        cfg = load_config()
    except Exception:
        return default
    return int((cfg.get("fallback") or {}).get("max_output_tokens") or default)
