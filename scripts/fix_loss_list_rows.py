"""Relabel stored rows that came from a list of owners' LOSS reports.

Some brokers publish the reports owners send them: "if you found any of these
cars, message me privately and I will hand you the owner's number". The layout
is a recovery list's, so every lane filed the cars as found / spotted / seeking
owner, and the site showed missing cars as sightings. Ingest now relabels such
a post in the gate (`gates.is_loss_list`); this applies the same rule to the
rows already in the registry: status -> unknown, description prefixed with
«بلاغ فقدان من صاحب العربة، ليست مشاهدة».

    python scripts\fix_loss_list_rows.py            # dry run: counts only
    python scripts\fix_loss_list_rows.py --apply    # backup, then rewrite

The original status and description of every row go to
data/loss_list_rows_backup_<date>.json first. Safe to run again: a relabelled
row no longer needs changing.
"""
import collections
import datetime
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from carhunt import db                                        # noqa: E402
from carhunt.config import DATA_DIR                           # noqa: E402
from carhunt.gates import is_loss_list, mark_loss_report      # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def plan(conn) -> list[tuple[dict, dict]]:
    """(stored row, relabelled row) for every row that needs changing."""
    posts = [r[0] for r in conn.execute("SELECT id, text FROM posts WHERE text LIKE '%مالك%'")
             if is_loss_list(r[1])]
    out = []
    for i in range(0, len(posts), 500):
        chunk = posts[i:i + 500]
        for r in conn.execute(
                "SELECT id, post_id, status, description FROM vehicles WHERE post_id IN (%s)"
                % ",".join("?" * len(chunk)), chunk):
            old = {"id": r[0], "post_id": r[1], "status": r[2], "description": r[3]}
            new = dict(old)
            if mark_loss_report(new):
                out.append((old, new))
    return out


def main() -> int:
    apply = "--apply" in sys.argv
    conn = db.connect()
    todo = plan(conn)
    conn.close()
    by = collections.Counter(o["status"] for o, _ in todo)
    print(f"{len(todo)} row(s) in {len({o['post_id'] for o, _ in todo})} loss-list post(s) "
          f"to relabel; stored status now: {dict(by)}")
    if not apply or not todo:
        return 0
    stamp = datetime.date.today().isoformat()
    backup = DATA_DIR / f"loss_list_rows_backup_{stamp}.json"
    if backup.exists():
        backup = DATA_DIR / f"loss_list_rows_backup_{stamp}_{datetime.datetime.now():%H%M%S}.json"
    backup.write_text(json.dumps([o for o, _ in todo], ensure_ascii=False, indent=1), encoding="utf-8")
    print("backup:", backup)
    done = 0
    for i in range(0, len(todo), 200):                        # short transactions: lanes are writing
        with db.write_tx() as w:
            for old, new in todo[i:i + 200]:
                cur = w.execute(
                    "UPDATE vehicles SET status = ?, description = ? "
                    "WHERE id = ? AND status IS ? AND description IS ?",   # re-ingested meanwhile: leave it
                    (new["status"], new["description"], old["id"], old["status"], old["description"]))
                done += cur.rowcount
    print(f"relabelled {done} row(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
