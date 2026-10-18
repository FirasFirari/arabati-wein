"""Progress, spend and yield of the Google Vision OCR sweep. Safe to run anytime.

Reads the database, not the log, so it is accurate even if the run was
restarted, resumed, or is being run from another window.
"""
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from carhunt import db

PRICE = 1.50 / 1000
FREE = 1000
VIN_TOKEN = re.compile(r"[A-HJ-NPR-Z0-9]{14,}")


def main() -> None:
    conn = db.connect()
    total = conn.execute("SELECT COUNT(*) FROM media").fetchone()[0]
    done = conn.execute(
        "SELECT COUNT(*) FROM media WHERE ocr_at IS NOT NULL").fetchone()[0]
    withtext = conn.execute(
        "SELECT COUNT(*) FROM media WHERE ocr_text IS NOT NULL "
        "AND ocr_text <> ''").fetchone()[0]
    errs = conn.execute(
        "SELECT COUNT(*) FROM media WHERE ocr_error IS NOT NULL").fetchone()[0]
    # Two very different things live in ocr_error and stacking them into one
    # number reads as alarm. "not a real image" is the magic-byte sniff
    # rejecting an HTML error page saved as .jpg — expected, free, nothing to
    # do. Everything else is a real failure that --retry-errors should re-send.
    dead = conn.execute(
        "SELECT COUNT(*) FROM media WHERE ocr_error LIKE '%not a real image%'"
    ).fetchone()[0]
    retryable = errs - dead
    blank = done - withtext - errs

    # Billed calls are per distinct FILE, not per media row — the sweep fills
    # every row sharing a path from one call, so counting rows overstates cost.
    files = conn.execute(
        "SELECT COUNT(DISTINCT path) FROM media WHERE ocr_at IS NOT NULL"
    ).fetchone()[0]
    spent = max(files - FREE, 0) * PRICE

    # Rate, from the timestamps the sweep writes.
    row = conn.execute(
        "SELECT MIN(ocr_at) a, MAX(ocr_at) b FROM media WHERE ocr_at IS NOT NULL"
    ).fetchone()
    rate = eta = None
    if row and row["a"] and row["b"] and row["a"] != row["b"]:
        fmt = "%Y-%m-%dT%H:%M:%SZ"
        try:
            span = time.mktime(time.strptime(row["b"], fmt)) - \
                   time.mktime(time.strptime(row["a"], fmt))
            if span > 0:
                rate = done / span
                eta = (total - done) / rate / 60 if rate else None
        except ValueError:
            pass

    pct = done / total * 100 if total else 0
    bar = "#" * int(pct / 2.5) + "." * (40 - int(pct / 2.5))
    print(f"\nGOOGLE VISION OCR SWEEP")
    print(f"[{bar}] {pct:5.1f}%")
    print(f"  images done     : {done:,} / {total:,}")
    print(f"  billed files    : {files:,}   (~${spent:.2f}, first {FREE} free)")
    print(f"  with text       : {withtext:,}")
    print(f"  blank (no text) : {blank:,}")
    print(f"  dead files      : {dead:,}   (not images; sniffed, never billed)")
    print(f"  retryable errors: {retryable:,}"
          + ("   <- run --retry-errors when the sweep ends" if retryable else ""))
    if rate:
        print(f"  rate            : {rate:.1f} img/s"
              + (f" | ~{eta:.0f} min left" if eta and eta > 0 else " | finishing"))

    vins = sum(1 for r in conn.execute(
        "SELECT ocr_text FROM media WHERE ocr_text <> ''")
        if VIN_TOKEN.search(r[0].upper()))
    print(f"  VIN-length tokens found in {vins:,} images")
    if retryable:
        e = conn.execute(
            "SELECT ocr_error FROM media WHERE ocr_error IS NOT NULL "
            "AND ocr_error NOT LIKE '%not a real image%' LIMIT 1").fetchone()
        if e:
            print(f"  sample failure  : {str(e[0])[:110]}")
    print()


if __name__ == "__main__":
    main()
