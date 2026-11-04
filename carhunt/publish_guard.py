"""Last check before anything is published: scan what will actually ship.

Why this exists. On 2026-09-09 the owner corrected a "Traffic Department"
number: it is a PERSONAL number and must not be published. The correction was
written into the notes, `grep` over the code came back clean, and the number
stayed live anyway -- as a contact on 11 sighting rows inside `detail.bin`.
grep cannot see into a gzip, so the check that "proved" it gone looked in the
wrong place. This module looks where visitors look: the decompressed payloads
and the built HTML.

What it refuses (each is a list in config.yaml, never in code -- this file is
published on GitHub and a personal number must not be):

* `publish.forbidden_contacts` -- numbers that must never ship, in any field,
  in any spelling (Arabic-Indic digits, spaces/dashes, a +249 / 0 prefix).
* Unfilled template tokens (`__STATS__` ...) in any built HTML.

    python -m carhunt.publish_guard              # scans site/ (the export output)
    python -m carhunt.publish_guard site/deploy  # scans what netlify_deploy.py uploads

Exit status is non-zero on any finding, so a deploy can stop on it.
"""
from __future__ import annotations

import gzip
import pathlib
import re
import sys

from .config import PROJECT_ROOT, load_config

# One character in, one character out: folding keeps every index valid, so a
# match found in the folded text can be cut out of the original at the same span.
_DIGIT_FOLD = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_SEP = r"[\s\-.]*"
_TOKEN = re.compile(r"__[A-Z][A-Z_]*__")


def _significant(number: str) -> str:
    """'0123456789', '+249 123 456 789' -> '123456789'."""
    d = re.sub(r"\D", "", str(number).translate(_DIGIT_FOLD))
    if d.startswith("249") and len(d) > 9:
        d = d[3:]
    return d.lstrip("0")


def forbidden_patterns(cfg: dict | None = None) -> list[re.Pattern]:
    """One regex per forbidden number, tolerant of every way it gets written."""
    cfg = cfg if cfg is not None else load_config()
    out = []
    for n in ((cfg.get("publish") or {}).get("forbidden_contacts") or []):
        sig = _significant(n)
        if len(sig) < 7:          # too short to match safely; refuse to guess
            continue
        body = _SEP.join(sig)
        out.append(re.compile(rf"(?<!\d)(?:\+?249{_SEP}|0{_SEP})?{body}(?!\d)"))
    return out


def mask(text, patterns, replacement: str = "[رقم محذوف]"):
    """Remove every forbidden number from `text`, keeping everything else."""
    if not text or not patterns:
        return text
    s = str(text)
    folded = s.translate(_DIGIT_FOLD)
    spans = sorted({m.span() for p in patterns for m in p.finditer(folded)})
    if not spans:
        return text
    out, last = [], 0
    for a, b in spans:
        if a < last:
            continue
        out.append(s[last:a])
        out.append(replacement)
        last = b
    out.append(s[last:])
    return "".join(out)


def contains(text, patterns) -> bool:
    if not text or not patterns:
        return False
    folded = str(text).translate(_DIGIT_FOLD)
    return any(p.search(folded) for p in patterns)


def scan_payload(name: str, blob: bytes, patterns) -> list[str]:
    """Findings for one gzip'd JSON payload, decompressed in memory."""
    try:
        text = gzip.decompress(blob).decode("utf-8")
    except Exception as exc:  # a payload we cannot read is itself a finding
        return [f"{name}: cannot decompress ({exc})"]
    folded = text.translate(_DIGIT_FOLD)
    found = []
    for p in patterns:
        n = len(p.findall(folded))
        if n:
            found.append(f"{name}: forbidden number present {n}x")
    return found


def scan_html(name: str, html: str, patterns) -> list[str]:
    found = []
    left = sorted(set(_TOKEN.findall(html)))
    if left:
        found.append(f"{name}: unfilled template tokens {left}")
    if contains(html, patterns):
        found.append(f"{name}: forbidden number present")
    return found


def scan_dir(directory: pathlib.Path, patterns=None) -> list[str]:
    patterns = forbidden_patterns() if patterns is None else patterns
    found = []
    for f in sorted(directory.glob("*.bin")):
        found += scan_payload(f.name, f.read_bytes(), patterns)
    for f in sorted(directory.glob("*.html")):
        found += scan_html(f.name, f.read_text(encoding="utf-8", errors="replace"), patterns)
    for f in sorted(directory.glob("*.txt")) + sorted(directory.glob("*.json")):
        if contains(f.read_text(encoding="utf-8", errors="replace"), patterns):
            found.append(f"{f.name}: forbidden number present")
    return found


def main(argv: list[str]) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    directory = pathlib.Path(argv[0]) if argv else PROJECT_ROOT / "site"
    if not directory.is_absolute():
        directory = PROJECT_ROOT / directory
    patterns = forbidden_patterns()
    if not patterns:
        print("publish_guard: WARNING no publish.forbidden_contacts configured")
    found = scan_dir(directory, patterns)
    for line in found:
        print("publish_guard: REFUSED", line)
    if not found:
        print(f"publish_guard: clean ({directory}, {len(patterns)} forbidden number(s) checked)")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
