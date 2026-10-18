"""Match standing watches against the registry, and say who to contact.

A watch is left by a visitor whose search returned nothing — which is ~95% of
searches (Clarity: 21 of 22 sessions). The form on mycarsd.online posts to
Netlify Forms, and the data stays there until something pulls it down:

    python scripts\\fetch_watches.py        # API -> data/watches.json
    python scripts\\check_watches.py        # then this

or, with no Netlify token, export the CSV from
https://app.netlify.com/projects/mycarsd/forms and either save it as
`data/watches.csv` or pass its path:

    python scripts\\check_watches.py some\\export.csv
    python scripts\\check_watches.py --seen data\\watches_seen.json

RUN IT AFTER EVERY INGEST. That is the entire point: the registry gained
~95,000 sightings in one day, so a number that missed yesterday can match
today, and nobody finds out unless this is run.

MATCHING lives in `carhunt/watches.py` and is shared with the dashboard, so the
two can never disagree. Do not re-implement it here.

CONTACT DETAILS ARE PERSONAL DATA. This prints phone numbers so a human can
act on them; it writes nothing back to the database and nothing to the
published site. Never paste this output anywhere public. And before contacting
anyone, read the extortion section of the project notes: the finder is told
where the sighting is, never put in touch with a holder.
"""
import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from carhunt import db, watches as watchlib


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("csv", type=pathlib.Path, nargs="?", default=None,
                    help="a Netlify Forms CSV export. Optional — by default "
                         "reads data/watches.json, else data/watches.csv.")
    ap.add_argument("--seen", type=pathlib.Path, default=None, metavar="FILE",
                    help="remember which watch/post pairs were already "
                         "reported, so a daily run only shows what is NEW. "
                         "Without it every run re-reports every old hit and "
                         "the new one gets lost in the noise.")
    args = ap.parse_args()

    if args.csv is not None:
        watchlib.WATCHES_CSV = args.csv
        watchlib.WATCHES_JSON = pathlib.Path("\0nonexistent")  # force the CSV

    seen = set()
    if args.seen and args.seen.exists():
        seen = set(tuple(x) for x in json.loads(args.seen.read_text("utf-8")))

    watches = watchlib.load_watches()
    if not watches:
        sys.exit("No watches found. Fetch them first:\n"
                 "    python scripts\\fetch_watches.py\n"
                 "or save a Netlify CSV export as data/watches.csv.")

    db.init()
    conn = db.connect()
    hits = watchlib.scan(conn, watches)
    # Raw-text hits come from the last scripts\raw_sweep.py run (it takes a
    # minute; the dashboard reads the same file, so the two agree).
    raw = watchlib.load_raw_hits()
    for ident, rhs in raw.items():
        have = {h["post_id"] for h in hits.get(ident, [])}
        hits.setdefault(ident, []).extend(
            dict(h, desc=f"[{h.get('kind', 'unclear')}]")
            for h in rhs if h["post_id"] not in have)
    # Hits a human already read and judged not a sighting (own post, other car)
    reviewed = watchlib.load_reviewed()
    hidden = 0
    for ident in hits:
        keep = [h for h in hits[ident] if (ident, h["post_id"]) not in reviewed]
        hidden += len(hits[ident]) - len(keep)
        hits[ident] = keep
    print(f"{len(watches)} watch(es) against the registry "
          f"[source: {watchlib.source_note()}]")
    if raw:
        swept = json.loads(watchlib.RAW_HITS_JSON.read_text("utf-8"))
        print(f"raw-text hits: from the sweep of {swept['generated_at']}\n")
    else:
        print("raw-text hits: none loaded — run scripts\\raw_sweep.py\n")

    total_hits = new_hits = 0
    done = set()
    for w in watches:
        ident = w["identifier"]
        if ident in done:          # the same number registered twice
            continue
        done.add(ident)
        found = hits.get(ident, [])
        if not found:
            continue
        total_hits += 1
        fresh = [h for h in found if (ident, h["post_id"]) not in seen]
        if not fresh:
            continue
        new_hits += 1
        print("=" * 68)
        print(f"WATCH {ident!r}  contact={w['contact'] or '(none given)'}"
              f"{'  note=' + w['note'] if w['note'] else ''}"
              f"{'  left ' + w['created_at'] if w['created_at'] else ''}")
        for h in fresh[:10]:
            url = conn.execute("SELECT url FROM posts WHERE id=?",
                               (h["post_id"],)).fetchone()
            print(f"   MATCH ({h['why']}) post {h['post_id']}  {h['desc']}")
            print(f"      {url['url'] if url else ''}")
        if len(fresh) > 10:
            print(f"   ... and {len(fresh) - 10} more")
        # Mark EVERY fresh pair seen, not just the ten printed. Marking only
        # the printed ones made each run dribble out the next ten of the same
        # match — a chassis with 130 sightings would have "found something new"
        # thirteen days running and buried an actually new hit.
        for h in fresh:
            seen.add((ident, h["post_id"]))

    print("\n" + "=" * 68)
    print(f"watches checked : {len(watches)}")
    print(f"watches matching: {total_hits}   NEW since last run: {new_hits}")
    if hidden:
        print(f"hidden          : {hidden} hit(s) already reviewed as not "
              f"sightings ({watchlib.REVIEWED_JSON.name})")
    if new_hits:
        print("\nContact these people. Tell them WHERE the sighting is; never "
              "put them in touch with a holder, and never take payment.")
    if args.seen:
        args.seen.parent.mkdir(parents=True, exist_ok=True)
        args.seen.write_text(json.dumps(sorted(seen)), encoding="utf-8")
        print(f"\nremembered {len(seen)} reported pair(s) in {args.seen}")


if __name__ == "__main__":
    main()
