"""Verify a wave of subagent verdict files before anything reaches the database.

Three checks, each of which exists because a softer one let something through:

  COVERAGE   every post id in the batch must come back. An agent can stop
             early and still report success.
  GROUNDING  every identifier must appear in that post's own text or its OCR
             text; anything else is nulled. 106 fabricated identifiers were
             caught this way and ZERO were self-reported.
  SHORTFALL  vehicles emitted vs distinct chassis-like numbers in the source.
             Coverage is per POST, so a single mega-list post returning 401 of
             684 vehicles scores a clean 1/1 while dropping 283. That is the
             shape of the loss that cost this project ~130,000 records. This
             check FLAGS ONLY — a bare list of tracking codes with no vehicle
             words in it is legitimately zero vehicles, so a human decides.
"""
import json, pathlib, re, sys, unicodedata
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
from ingest_local_verdicts import strip_ungrounded

AR = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
digits = lambda s: re.sub(r"\D", "", str(s or "").translate(AR))

def check(tag: str, vdir: pathlib.Path) -> dict:
    batch_p = pathlib.Path(f"data/local_batches/batch_{tag}.json")
    vf = vdir / f"verdicts_{tag}.json"
    out = {"tag": tag, "ok": False}
    if not vf.exists():
        out["error"] = "no verdict file"; return out
    try:
        data = json.loads(vf.read_text(encoding="utf-8"))
    except Exception as e:
        out["error"] = f"INVALID JSON: {str(e)[:80]}"; return out
    posts = {p["id"]: p for p in json.loads(batch_p.read_text(encoding="utf-8"))}
    got = {e["id"] for e in data}
    out["missing"] = sorted(set(posts) - got)
    out["vehicles"] = out["ungrounded"] = out["ocr_only"] = 0
    out["short"] = []
    for e in data:
        v = e.get("verdict", e)
        p = posts.get(e["id"], {})
        ptxt, ocr = p.get("text") or "", p.get("ocr_text") or ""
        vs = v.get("vehicles") or []
        out["vehicles"] += len(vs)
        for veh in vs:
            if isinstance(veh, dict) and veh.get("chassis"):
                d = digits(veh["chassis"])
                if d and d not in digits(ptxt) and d in digits(ocr):
                    out["ocr_only"] += 1
        src = unicodedata.normalize("NFKC", ptxt + chr(10) + ocr).translate(AR)
        distinct = len(set(re.findall(r"\b\d{5,17}\b", src)))
        if distinct >= 8 and len(vs) < distinct * 0.6:
            out["short"].append((e["id"], distinct, len(vs)))
        out["ungrounded"] += len(strip_ungrounded(v, ptxt + chr(10) + ocr))
    out["ok"] = not out["missing"]
    return out

if __name__ == "__main__":
    # First argument is the wave prefix (w1, w2, ...) — it was hardcoded to w1
    # in the first version, which would have silently reported "no verdict
    # file" for every later wave.
    args = sys.argv[1:]
    wave = "w1"
    if args and re.fullmatch(r"w\d+", args[0]):
        wave, args = args[0], args[1:]
    tags = args
    tot = {"vehicles": 0, "ungrounded": 0, "ocr_only": 0}
    print(f"wave {wave}")
    print(f"{'batch':<8}{'cover':>7}{'vehicles':>10}{'ungrnd':>8}{'ocr-only':>10}  flags")
    for t in tags:
        r = check(t, pathlib.Path(f"data/local_verdicts/{wave}_{t}"))
        if r.get("error"):
            print(f"{t:<8}  {r['error']}"); continue
        for k in tot: tot[k] += r[k]
        cover = "OK" if r["ok"] else f"MISS {len(r['missing'])}"
        flags = f"{len(r['short'])} short" if r["short"] else ""
        print(f"{t:<8}{cover:>7}{r['vehicles']:>10}{r['ungrounded']:>8}{r['ocr_only']:>10}  {flags}")
        for s in r["short"]:
            print(f"          ! post {s[0]}: {s[1]} numbers in source, {s[2]} vehicles")
    print(f"\nTOTAL vehicles {tot['vehicles']} | ungrounded nulled {tot['ungrounded']} "
          f"| chassis ONLY from OCR {tot['ocr_only']}")
