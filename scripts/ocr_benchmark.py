"""Benchmark FREE local OCR (RapidOCR) against Google Vision as ground truth.

Run with the OCR venv, not the main interpreter:

    .venv-ocr\\Scripts\\python scripts\\ocr_benchmark.py --n 60

Ground truth: images Google Vision already read (media.ocr_text). The metric
is what this project actually uses OCR for — identifiers — so we score
IDENTIFIER TOKENS, not prose: runs of 5+ characters that contain a digit,
after Arabic-Indic digits are folded to ASCII. Recall = share of Vision's
tokens the local engine reproduced exactly. "Extra" = local tokens Vision
did not have (Vision misses things too, so this is an upper bound on
fabrication, not a measurement of it).

The sample is stratified: half the images have Arabic-Indic digits in
Vision's text (the كشف lists people photograph), half have only ASCII digits,
because the default model cannot read ٠-٩ at all and the Arabic one may read
Latin VINs badly.

Read-only against the database.
"""
import argparse
import random
import re
import sqlite3
import sys
import time
from pathlib import Path

import psutil
from rapidocr import LangDet, LangRec, ModelType, OCRVersion, RapidOCR

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
DB = Path(__file__).resolve().parent.parent / "data" / "carhunt.db"
FOLD = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
TOK = re.compile(r"[0-9A-Za-z٠-٩۰-۹]+")
ARABIC_DIGIT = re.compile(r"[٠-٩۰-۹]")


def id_tokens(text: str) -> set[str]:
    out = set()
    for t in TOK.findall(text or ""):
        t = t.translate(FOLD).upper()
        if len(t) >= 5 and any(c.isdigit() for c in t):
            out.add(t)
    return out


# The DETECTOR is what decides whether numbers are found at all. The default
# (PP-OCRv6) and the v5 detectors box the Arabic words of a list line and skip
# the Arabic-Indic number at its left end entirely: 0/15 on a clean list photo.
# The older multilingual v3 detector boxes the numbers: 11/15 on the same photo.
MULTI_DET = {"Det.ocr_version": OCRVersion.PPOCRV4,  # registry files v3-multi here
             "Det.model_type": ModelType.MOBILE,
             "Det.lang_type": LangDet.MULTI}


def engines() -> dict:
    return {
        "latin": RapidOCR(params=dict(MULTI_DET)),
        "arabic": RapidOCR(params={**MULTI_DET,
                                   "Rec.lang_type": LangRec.ARABIC,
                                   "Rec.ocr_version": OCRVersion.PPOCRV5,
                                   "Rec.model_type": ModelType.MOBILE}),
    }


UPSCALE_BELOW = 0   # set by --upscale: images narrower than this are doubled


def read(engine, path: str) -> str:
    img = path
    if UPSCALE_BELOW:
        from PIL import Image
        import numpy as np
        im = Image.open(path).convert("RGB")
        if im.width < UPSCALE_BELOW:
            im = im.resize((im.width * 2, im.height * 2), Image.LANCZOS)
        img = np.array(im)[:, :, ::-1]   # RapidOCR expects BGR arrays
    res = engine(img)
    return "\n".join(res.txts or ()) if res is not None and res.txts else ""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=60, help="images PER stratum")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--upscale", type=int, default=0, metavar="PX",
                    help="double images narrower than PX before reading")
    args = ap.parse_args()
    global UPSCALE_BELOW
    UPSCALE_BELOW = args.upscale

    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    rows = conn.execute("SELECT path, ocr_text FROM media WHERE ocr_text IS NOT NULL"
                        " AND ocr_text <> '' AND ocr_error IS NULL").fetchall()
    arabic, ascii_ = [], []
    for path, text in rows:
        toks = id_tokens(text)
        if len(toks) >= 2 and Path(path).exists():
            (arabic if ARABIC_DIGIT.search(text) else ascii_).append((path, toks))
    rnd = random.Random(args.seed)
    sample = {"arabic-digit images": rnd.sample(arabic, min(args.n, len(arabic))),
              "ascii-digit images": rnd.sample(ascii_, min(args.n, len(ascii_)))}
    print(f"pool: {len(arabic)} arabic-digit, {len(ascii_)} ascii-digit images "
          f"with 2+ identifier tokens\n")

    eng = engines()
    proc = psutil.Process()
    peak = proc.memory_info().rss
    for stratum, items in sample.items():
        stats = {k: [0, 0, 0, 0.0, 0] for k in (*eng, "both")}  # hit, truth, extra, secs, tailhit
        for path, truth in items:
            got = {}
            for name, e in eng.items():
                t0 = time.perf_counter()
                got[name] = id_tokens(read(e, path))
                stats[name][3] += time.perf_counter() - t0
                peak = max(peak, proc.memory_info().rss)
            got["both"] = got["latin"] | got["arabic"]
            for name, toks in got.items():
                stats[name][0] += len(truth & toks)
                stats[name][1] += len(truth)
                stats[name][2] += len(toks - truth)
                # what matching needs: the last 6 characters, anywhere in a token
                blob = " ".join(toks)
                stats[name][4] += sum(t[-6:] in blob for t in truth)
        print(f"== {stratum} (n={len(items)})")
        for name, (hit, tot, extra, secs, tail) in stats.items():
            per = f"{secs / len(items):.2f}s/img" if name != "both" else \
                  f"{(stats["latin"][3] + stats['arabic'][3]) / len(items):.2f}s/img"
            print(f"   {name:8} recall {hit}/{tot} = {hit / max(tot, 1):.0%}   "
                  f"tail-6 {tail / max(tot, 1):.0%}   extra tokens {extra}   {per}")
        print()
    print(f"peak RAM {peak / 2**20:.0f} MB")


if __name__ == "__main__":
    main()
