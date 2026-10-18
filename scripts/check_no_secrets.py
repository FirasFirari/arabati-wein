"""Refuse to commit secrets or the family's identifiers.

    python scripts\\check_no_secrets.py            # scan every tracked file
    python scripts\\check_no_secrets.py --staged   # scan what is about to be committed
    python scripts\\check_no_secrets.py --paths a.py b.md

Installed as .git/hooks/pre-commit (see scripts/install_hooks.py). Exits 1 on
the first hit and prints file:line with the match masked, never the value.

What it looks for:
  * key shapes: OpenAI/Moonshot `sk-…`, Google `AIza…`, AI Studio `AQ.…`,
    Netlify `nfp_…`, Resend `re_…`, JWTs `eyJ…`, Telegram bot tokens
    `<digits>:<35 chars>`, Anthropic `sk-ant-…`, Apify `apify_api_…`
  * the configured car's full VIN and its 7-char tail, and the plate serial
    (read from config.yaml at run time — this file embeds none of them)
  * Sudanese phone numbers (+249 / 09x / 01x + 8 digits)
  * file names that must never be tracked at all
"""
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

KEY_PATTERNS = [
    ("openai/moonshot key", re.compile(r"\bsk-(?:proj-|ant-)?[A-Za-z0-9_-]{20,}")),
    ("google api key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("ai studio key", re.compile(r"\bAQ\.[A-Za-z0-9_-]{20,}")),
    ("netlify token", re.compile(r"\bnfp_[A-Za-z0-9]{20,}")),
    ("resend key", re.compile(r"\bre_[A-Za-z0-9]{20,}")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}")),
    ("telegram bot token", re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b")),
    ("apify token", re.compile(r"\bapify_api_[A-Za-z0-9]{20,}")),
    ("sudanese phone", re.compile(r"(?<!\d)(?:\+?249|0)[19]\d{8}(?!\d)")),
]
FORBIDDEN_NAMES = re.compile(
    r"(^|/)(config\.yaml|HANDOFF\.md|OPERATIONS\.md|SWEEP\.md|facebook_posts\.md|"
    r".*\.session(-journal)?|.*token.*\.txt|.*key.*\.txt|.*\.db(-wal|-shm)?|"
    r"scripts/(build_\d+\.py|verdicts_\d+\.json|veh_\d+\.json)|data/.*|reference/.*|private/.*)$")
SKIP_SUFFIXES = {".bin", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".pyc", ".db", ".gz"}
# the scanner may name the patterns it looks for
SELF = {"scripts/check_no_secrets.py"}
# synthetic example numbers used in prompts, docs and tests — nobody's
EXAMPLES = {"0912345678", "249912345678", "+249912345678", "0123456789"}


def car_patterns():
    """Patterns for the configured car, if config.yaml is present."""
    pats = []
    cfg = ROOT / "config.yaml"
    if not cfg.exists():
        return pats
    try:
        import yaml
        car = (yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}).get("car") or {}
    except Exception:
        return pats
    vin = (car.get("vin") or "").strip()
    if len(vin) >= 11:
        pats.append(("target VIN", re.compile(re.escape(vin), re.I)))
        tail = vin[-7:]
        ar = tail.translate(str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩"))
        pats.append(("target chassis tail", re.compile(rf"(?<![0-9]){re.escape(tail)}(?![0-9])|{ar}")))
    serial = re.search(r"\d{4,6}", car.get("plate") or "")
    if serial:
        s = serial.group(0)
        ar = s.translate(str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩"))
        pats.append(("target plate serial", re.compile(rf"(?<![0-9]){s}(?![0-9])|{ar}")))
    return pats


def git_files(staged: bool) -> list[str]:
    cmd = ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"] if staged else ["git", "ls-files"]
    out = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    return [l.strip() for l in out.stdout.splitlines() if l.strip()]


def staged_text(rel: str):
    out = subprocess.run(["git", "show", f":{rel}"], cwd=ROOT, capture_output=True)
    return out.stdout if out.returncode == 0 else None


def mask(s: str) -> str:
    return s[:4] + "…" + s[-2:] if len(s) > 8 else "…"


def main(argv) -> int:
    staged = "--staged" in argv
    if "--paths" in argv:
        files = argv[argv.index("--paths") + 1:]
    else:
        files = git_files(staged)
    patterns = KEY_PATTERNS + car_patterns()
    hits = 0
    for rel in files:
        rel = rel.replace("\\", "/")
        if FORBIDDEN_NAMES.search(rel):
            print(f"FORBIDDEN FILE  {rel}  (must never be tracked)")
            hits += 1
            continue
        if pathlib.Path(rel).suffix.lower() in SKIP_SUFFIXES or rel in SELF:
            continue
        data = staged_text(rel) if staged else None
        if data is None:
            p = ROOT / rel
            if not p.is_file():
                continue
            data = p.read_bytes()
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            for label, pat in patterns:
                m = pat.search(line)
                if m and m.group(0) in EXAMPLES:
                    continue
                if m:
                    print(f"{rel}:{lineno}  {label}: {mask(m.group(0))}")
                    hits += 1
                    break
    if hits:
        print(f"\n{hits} problem(s). Nothing may be committed until they are gone.")
        return 1
    print(f"clean: {len(files)} file(s) scanned")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
