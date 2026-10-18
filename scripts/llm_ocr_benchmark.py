"""Benchmark FREE-tier vision LLMs as OCR, against Google Vision's reads.

    python scripts\\llm_ocr_benchmark.py --models gemma-4-31b-it gemini-3.5-flash-lite

Same sample and same scoring as scripts/ocr_benchmark.py (RapidOCR), so the
numbers line up: identifier tokens (5+ chars containing a digit, Arabic-Indic
folded to ASCII), recall against Vision, tail-6 recall, and "extra" tokens.

EXTRA TOKENS MATTER MORE HERE THAN FOR CLASSIC OCR. A classic engine misreads;
a language model can INVENT a plausible number. An extra token is either a
Vision miss or a fabrication — this script prints a sample of them per model
so a human can look at the photo and decide, instead of trusting a count.

Every request is a free-tier call on the project's Gemini key. 60 photos x
N models; --n keeps it small. Read-only against the database.
"""
import argparse
import base64
import json
import random
import re
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from openai import OpenAI

from carhunt.config import load_config

DB = Path(__file__).resolve().parent.parent / "data" / "carhunt.db"
OUT = Path(__file__).resolve().parent.parent / "data" / "ocr_bench"
FOLD = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
TOK = re.compile(r"[0-9A-Za-z٠-٩۰-۹]+")
ARABIC_DIGIT = re.compile(r"[٠-٩۰-۹]")
BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"

PROMPT = (
    "Transcribe ALL text visible in this image exactly as written, line by "
    "line, top to bottom. Copy every number digit by digit; keep Arabic-Indic "
    "digits (٠١٢٣٤٥٦٧٨٩) as Arabic-Indic and Latin digits as Latin. Do not "
    "translate, correct, summarise, complete or guess. If a character is "
    "unreadable write ? in its place. Output only the transcription.")


def id_tokens(text: str) -> set[str]:
    out = set()
    for t in TOK.findall(text or ""):
        t = t.translate(FOLD).upper()
        if len(t) >= 5 and any(c.isdigit() for c in t):
            out.add(t)
    return out


def sample(n: int, seed: int) -> dict:
    """Identical to ocr_benchmark.py's selection, so results are comparable."""
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    rows = conn.execute("SELECT path, ocr_text FROM media WHERE ocr_text IS NOT NULL"
                        " AND ocr_text <> '' AND ocr_error IS NULL").fetchall()
    arabic, ascii_ = [], []
    for path, text in rows:
        toks = id_tokens(text)
        if len(toks) >= 2 and Path(path).exists():
            (arabic if ARABIC_DIGIT.search(text) else ascii_).append((path, toks))
    rnd = random.Random(seed)
    return {"arabic-digit images": rnd.sample(arabic, min(n, len(arabic))),
            "ascii-digit images": rnd.sample(ascii_, min(n, len(ascii_)))}


def transcribe(client, model: str, path: str) -> tuple[str, str | None]:
    b64 = base64.b64encode(Path(path).read_bytes()).decode()
    for attempt in range(3):
        try:
            r = client.chat.completions.create(
                model=model, temperature=0,
                messages=[{"role": "user", "content": [
                    {"type": "text", "text": PROMPT},
                    {"type": "image_url",
                     "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}]}])
            return r.choices[0].message.content or "", None
        except Exception as e:
            msg = str(e)
            if "429" in msg and attempt < 2:
                time.sleep(20)
                continue
            return "", msg[:400]
    return "", "retries exhausted"


BATCH_PROMPT = (
    "You are given {k} separate images, numbered in order. For EACH image, "
    "transcribe ALL visible text exactly as written, line by line. Copy every "
    "number digit by digit; keep Arabic-Indic digits as Arabic-Indic and Latin "
    "digits as Latin. Do not translate, correct, complete or guess; write ? for "
    "an unreadable character. Never move text between images. Reply with ONLY a "
    "JSON array of {k} strings, one transcription per image, in order.")


def transcribe_batch(client, model: str, paths: list[str]) -> tuple[list[str], str | None]:
    content = [{"type": "text", "text": BATCH_PROMPT.format(k=len(paths))}]
    for i, p in enumerate(paths, 1):
        b64 = base64.b64encode(Path(p).read_bytes()).decode()
        content += [{"type": "text", "text": f"Image {i}:"},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}]
    try:
        r = client.chat.completions.create(model=model, temperature=0,
                                           messages=[{"role": "user", "content": content}])
        txt = (r.choices[0].message.content or "").strip()
        txt = re.sub(r"^```(?:json)?|```$", "", txt).strip()
        out = json.loads(txt)
        if not isinstance(out, list) or len(out) != len(paths):
            return [""] * len(paths), f"bad shape: {len(out) if isinstance(out, list) else type(out)}"
        return [str(x) for x in out], None
    except Exception as e:
        return [""] * len(paths), str(e)[:400]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="+", required=True)
    ap.add_argument("--n", type=int, default=30, help="images PER stratum")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--rpm", type=float, default=12)
    ap.add_argument("--batch", type=int, default=1, help="images per request")
    args = ap.parse_args()

    key = load_config()["gemini"]["api_key"]
    client = OpenAI(api_key=key, base_url=BASE_URL, timeout=90, max_retries=0)
    strata = sample(args.n, args.seed)
    OUT.mkdir(parents=True, exist_ok=True)
    gap = 60.0 / args.rpm

    for model in args.models:
        print(f"######## {model}")
        raw = {}
        for stratum, items in strata.items():
            hit = tot = extra = tail = errs = 0
            secs = 0.0
            extras = []
            pre = {}
            if args.batch > 1:
                for i in range(0, len(items), args.batch):
                    group = [p for p, _ in items[i:i + args.batch]]
                    t0 = time.perf_counter()
                    texts, err = transcribe_batch(client, model, group)
                    secs += time.perf_counter() - t0
                    if err:
                        print(f"   BATCH ERROR {err[:200]}")
                    for p, t in zip(group, texts):
                        pre[p] = (t, err)
                    time.sleep(max(0.0, gap - (time.perf_counter() - t0)))
            for path, truth in items:
                t0 = time.perf_counter()
                if args.batch > 1:
                    text, err = pre[path]
                else:
                    text, err = transcribe(client, model, path)
                secs += time.perf_counter() - t0
                raw[path] = {"text": text, "error": err}
                if err:
                    errs += 1
                    print(f"   ERROR {err[:160]}")
                    if "429" in err:
                        print("   quota hit — stopping this model")
                        break
                got = id_tokens(text)
                hit += len(truth & got)
                tot += len(truth)
                extra += len(got - truth)
                blob = " ".join(got)
                tail += sum(t[-6:] in blob for t in truth)
                extras += [(Path(path).name, x) for x in sorted(got - truth)[:3]]
                if args.batch == 1:
                    time.sleep(max(0.0, gap - (time.perf_counter() - t0)))
            print(f"== {stratum} (n={len(items)}, errors {errs})")
            print(f"   recall {hit}/{tot} = {hit / max(tot, 1):.0%}   tail-6 "
                  f"{tail / max(tot, 1):.0%}   extra tokens {extra}   "
                  f"{secs / max(len(items), 1):.1f}s/img")
            print(f"   sample extras: {extras[:8]}")
        (OUT / f"{model}{'-b' + str(args.batch) if args.batch > 1 else ''}.json").write_text(json.dumps(raw, ensure_ascii=False, indent=1),
                                           encoding="utf-8")
        print()


if __name__ == "__main__":
    main()
