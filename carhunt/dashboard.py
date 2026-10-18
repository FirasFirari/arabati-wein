"""Live operations dashboard.

Serves a self-refreshing page at http://localhost:8770 showing what the
system is doing right now: service health, collection totals, the queue, the
newest posts as they land, and anything scoring high enough to matter.

Reads the database and heartbeat files directly, so it always reflects the
real state rather than a snapshot. Standard library only.

Usage:
    python -m carhunt.dashboard            # then open the printed URL
    python -m carhunt.dashboard --port 9000
"""
import argparse
import json
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import db, health
from . import watches as watchlib
from .config import DATA_DIR, load_config

SERVICES = [
    ("channel_monitor", "Telegram channels"),
    ("intake_bot", "Intake bot"),
    ("fb_service", "Facebook harvester"),
]
STALE = 300  # seconds before a heartbeat counts as unhealthy

# Watch matching needs a full pass over `vehicles` — the suffix and fragment
# rules cannot use the chassis index, and against 647k rows while the ingest
# services are writing it measured THIRTY-FIVE SECONDS.
#
# It therefore must never run inside _snapshot(): that holds the snapshot lock,
# so every poll from every open tab would queue behind it and the page would
# freeze for half a minute at each refresh — the exact hang that the snapshot
# cache was added to cure. A background thread owns it instead, with its own
# connection, and _snapshot() only ever reads the last value it published.
#
# Freshness costs nothing here: watches arrive a few a week and the registry
# only moves when an extraction run finishes.
_WATCH_TTL = 300.0
_watch_cache: dict = {
    "rows": [], "source": "", "error": "", "computing": True}


def _watch_signature(conn) -> tuple:
    """Cheap fingerprint of everything the answer depends on.

    The scan is only worth paying for when the watch list or the registry has
    actually moved. Extraction is hand-cranked and can sit still for days, so
    without this the thread would burn 35 seconds of SQLite contention every
    interval to recompute an identical answer.
    """
    n = conn.execute("SELECT COUNT(*) FROM vehicles").fetchone()[0]
    stamps = []
    for path in (watchlib.WATCHES_JSON, watchlib.WATCHES_CSV,
                 watchlib.RAW_HITS_JSON, watchlib.REVIEWED_JSON):
        try:
            stamps.append(path.stat().st_mtime)
        except OSError:
            stamps.append(None)
    return (n, *stamps)


def _watch_refresher() -> None:
    """Recompute the watch/registry match forever, off the request path."""
    last_sig = None
    while True:
        try:
            conn = db.connect()
            try:
                sig = _watch_signature(conn)
                if sig != last_sig:
                    rows = watchlib.status_rows(conn)
                    _watch_cache.update(rows=rows,
                                        source=watchlib.source_note(),
                                        error="", computing=False)
                    last_sig = sig
                elif _watch_cache["computing"]:
                    _watch_cache["computing"] = False
            finally:
                conn.close()
        except Exception as e:          # never let this kill the thread
            _watch_cache.update(source=watchlib.source_note(), computing=False,
                                error=f"{type(e).__name__}: {e}")
        time.sleep(_WATCH_TTL)


def _snapshot() -> dict:
    cfg = load_config()
    conn = db.connect()
    conn.row_factory = sqlite3.Row
    try:

        def one(sql, args=()):
            return conn.execute(sql, args).fetchone()[0]

        totals = {
            "posts": one("SELECT COUNT(*) FROM posts"),
            "analyzed": one("SELECT COUNT(*) FROM posts WHERE score IS NOT NULL"),
            "queue": one("SELECT COUNT(*) FROM posts WHERE score IS NULL"),
            # records = every sighting, all kept so a family sees each one;
            # distinct = deduplicated by identifier, i.e. how many CARS. Shared
            # definition lives in db.py so this and the exported page agree.
            "vehicles": one("SELECT COUNT(*) FROM vehicles"),
            "distinct_vehicles": one(db.DISTINCT_VEHICLES_SQL),
            "photos": one("SELECT COUNT(*) FROM media"),
        }

        services = []
        for key, label in SERVICES:
            age = health.age_seconds(key)
            info = health.read(key) or {}
            services.append({
                "key": key, "label": label,
                "age": None if age is None else round(age),
                "ok": age is not None and age < STALE,
                "detail": info.get("detail", ""),
            })

        recent = [dict(r) for r in conn.execute(
            "SELECT id, source, score, seen_at, url, substr(text,1,150) AS text, "
            "json_extract(verdict,'$.tier') AS tier "
            "FROM posts ORDER BY id DESC LIMIT 12")]

        # only verified tiers can raise an alert — screen-tier scores are not trusted
        alerts = [dict(r) for r in conn.execute(
            "SELECT id, source, score, url, substr(json_extract(verdict,'$.reasons'),1,220) "
            "AS reasons FROM posts WHERE score >= 60 AND json_extract(verdict,'$.tier') IN "
            "('deep','deep_after_batch','deep_recheck','identifier_scan') "
            "ORDER BY score DESC LIMIT 8")]

        sources = [dict(r) for r in conn.execute(
            "SELECT p.source, COUNT(DISTINCT p.id) AS posts, "
            "COUNT(DISTINCT CASE WHEN p.score IS NULL THEN p.id END) AS queued, "
            "COUNT(v.id) AS vehicles FROM posts p LEFT JOIN vehicles v ON v.post_id = p.id "
            "GROUP BY p.source ORDER BY posts DESC")]

        hourly = [dict(r) for r in conn.execute(
            "SELECT substr(seen_at,1,13) AS hour, COUNT(*) AS n FROM posts "
            "WHERE seen_at >= datetime('now','-24 hours') GROUP BY hour ORDER BY hour")]

        return {"totals": totals, "services": services, "recent": recent,
                "alerts": alerts, "sources": sources, "hourly": hourly,
                "watches": dict(_watch_cache),
                "car": f"{cfg['car']['color']} {cfg['car']['make']} "
                       f"{cfg['car']['model']} {cfg['car']['year']}",
                "vin": cfg["car"]["vin"], "plate": cfg["car"]["plate"].split("(")[0].strip()}
    finally:
        conn.close()   # a raising query used to leak the connection

PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Car Hunt — live</title>
<style>
:root{
  --bg:#0f1418; --panel:#161d23; --line:#243039; --ink:#e6edf3; --dim:#8b9aa7;
  --ok:#3fb950; --bad:#f85149; --warn:#d29922; --accent:#58a6ff;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font:14px/1.5 "Segoe UI",system-ui,sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:18px}
h1{font-size:1.15rem;margin:0;font-weight:600}
.sub{color:var(--dim);font-size:.82rem;margin-top:2px}
header{display:flex;justify-content:space-between;align-items:flex-start;
  gap:16px;flex-wrap:wrap;margin-bottom:16px}
.live{display:flex;align-items:center;gap:7px;color:var(--dim);font-size:.8rem}
.dot{width:8px;height:8px;border-radius:50%;background:var(--ok);
  animation:pulse 2s infinite}
@keyframes pulse{50%{opacity:.25}}
@media (prefers-reduced-motion:reduce){.dot{animation:none}}
.grid{display:grid;gap:10px}
.stats{grid-template-columns:repeat(auto-fit,minmax(140px,1fr));margin-bottom:12px}
.stat{background:var(--panel);border:1px solid var(--line);border-radius:9px;padding:11px 13px}
.stat .n{font-size:1.55rem;font-weight:650;font-variant-numeric:tabular-nums;line-height:1.1}
.stat .l{color:var(--dim);font-size:.76rem;margin-top:1px}
.svc{grid-template-columns:repeat(auto-fit,minmax(210px,1fr));margin-bottom:14px}
.service{background:var(--panel);border:1px solid var(--line);border-left-width:3px;
  border-radius:9px;padding:10px 13px}
.service.up{border-left-color:var(--ok)} .service.down{border-left-color:var(--bad)}
.service .name{font-weight:600;display:flex;justify-content:space-between;gap:8px}
.service .meta{color:var(--dim);font-size:.76rem;margin-top:2px}
.badge{font-size:.68rem;padding:1px 8px;border-radius:20px;white-space:nowrap}
.badge.up{background:rgba(63,185,80,.14);color:var(--ok)}
.badge.down{background:rgba(248,81,73,.14);color:var(--bad)}
.cols{display:grid;grid-template-columns:1.4fr 1fr;gap:12px}
@media(max-width:860px){.cols{grid-template-columns:1fr}}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:9px;
  padding:12px 14px}
.panel h2{font-size:.82rem;text-transform:uppercase;letter-spacing:.06em;
  color:var(--dim);margin:0 0 9px;font-weight:600}
.row{padding:7px 0;border-bottom:1px solid var(--line);font-size:.83rem}
.row:last-child{border-bottom:0}
.row .top{display:flex;justify-content:space-between;gap:10px;align-items:baseline}
.src{color:var(--accent);font-size:.75rem}
.txt{color:var(--dim);margin-top:2px;overflow:hidden;text-overflow:ellipsis;
  display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;
  direction:rtl;text-align:right}
.score{font-variant-numeric:tabular-nums;font-size:.75rem;padding:1px 7px;
  border-radius:20px;background:#20262c;color:var(--dim)}
.score.hot{background:rgba(248,81,73,.16);color:var(--bad);font-weight:600}
.score.warm{background:rgba(210,153,34,.16);color:var(--warn)}
.score.none{background:#20262c;color:#5d6b77}
table{width:100%;border-collapse:collapse;font-size:.8rem}
th{text-align:left;color:var(--dim);font-weight:500;padding:5px 6px;
  border-bottom:1px solid var(--line);font-size:.74rem}
td{padding:5px 6px;border-bottom:1px solid var(--line);font-variant-numeric:tabular-nums}
tr:last-child td{border-bottom:0}
td.q{color:var(--warn)}
.spark{display:flex;align-items:flex-end;gap:2px;height:38px;margin-top:6px}
.spark i{flex:1;background:var(--accent);opacity:.5;border-radius:1px 1px 0 0;min-height:2px}
.spark i:last-child{opacity:1}
.empty{color:var(--dim);font-size:.82rem;padding:6px 0}
.wsrc{color:var(--dim);font-size:.72rem;margin-top:7px}
td.wid{font-family:"Consolas",ui-monospace,monospace;direction:ltr;text-align:left}
td.wok{color:var(--dim)}
td.whit{color:var(--bad);font-weight:650}
.wnote{color:var(--dim);font-size:.74rem}
a{color:var(--accent);text-decoration:none} a:hover{text-decoration:underline}
</style></head><body><div class="wrap">
<header>
  <div>
    <h1>Car Hunt — live operations</h1>
    <div class="sub" id="target"></div>
  </div>
  <div class="live"><span class="dot"></span><span id="tick">connecting…</span></div>
</header>
<div class="grid stats" id="stats"></div>
<div class="grid svc" id="services"></div>
<div class="cols">
  <div class="panel">
    <h2>Incoming posts</h2>
    <div id="recent"></div>
  </div>
  <div>
    <div class="panel" style="margin-bottom:12px">
      <h2>Verified alerts</h2>
      <div id="alerts"></div>
    </div>
    <div class="panel" style="margin-bottom:12px">
      <h2>Collected · last 24h</h2>
      <div class="spark" id="spark"></div>
    </div>
    <div class="panel" style="margin-bottom:12px">
      <h2>Standing watches</h2>
      <table id="watches"></table>
      <div class="wsrc" id="watch-source"></div>
    </div>
    <div class="panel">
      <h2>Sources</h2>
      <table id="sources"></table>
    </div>
  </div>
</div>
</div>
<script>
const nf = new Intl.NumberFormat('en');
const esc = s => String(s ?? '').replace(/[&<>"]/g, c =>
  ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const ago = s => s == null ? 'never' :
  s < 90 ? `${s}s ago` : s < 5400 ? `${Math.round(s/60)}m ago` : `${(s/3600).toFixed(1)}h ago`;

function scoreClass(v){
  if (v === null || v === undefined) return 'none';
  if (v >= 60) return 'hot';
  if (v >= 30) return 'warm';
  return '';
}

async function refresh(){
  let d;
  if (window.__busy) return;              // a poll is already in flight
  window.__busy = true;
  try {
    const ctl = new AbortController();
    const kill = setTimeout(() => ctl.abort(), 20000);
    d = await (await fetch('/api', {signal: ctl.signal})).json();
    clearTimeout(kill);
  }
  catch(e){ document.getElementById('tick').textContent = 'disconnected'; return; }
  finally { window.__busy = false; }

  document.getElementById('target').textContent =
    `${d.car} · chassis ${d.vin} · plate ${d.plate}`;
  document.getElementById('tick').textContent =
    'updated ' + new Date().toLocaleTimeString();

  const t = d.totals;
  document.getElementById('stats').innerHTML = [
    ['Posts collected', t.posts], ['Analyzed', t.analyzed],
    ['Awaiting scoring', t.queue],
    // distinct cars first — it is the number that answers "how many vehicles
    // are catalogued". The record count sits beside it because one car can be
    // recorded dozens of times (the same recovery list reposted across
    // groups); every one of those records is kept and searchable.
    ['Distinct vehicles', t.distinct_vehicles],
    ['Sighting records', t.vehicles],
    ['Photos archived', t.photos],
  ].map(([l,n]) => `<div class="stat"><div class="n">${nf.format(n)}</div>
      <div class="l">${l}</div></div>`).join('');

  document.getElementById('services').innerHTML = d.services.map(s =>
    `<div class="service ${s.ok?'up':'down'}">
       <div class="name">${esc(s.label)}
         <span class="badge ${s.ok?'up':'down'}">${s.ok?'live':'not reporting'}</span></div>
       <div class="meta">heartbeat ${ago(s.age)}${s.detail?' · '+esc(s.detail):''}</div>
     </div>`).join('');

  document.getElementById('recent').innerHTML = d.recent.length ? d.recent.map(r =>
    `<div class="row"><div class="top">
       <span class="src">${esc(r.source)}${r.tier?' · '+esc(r.tier):''}</span>
       <span class="score ${scoreClass(r.score)}">${r.score ?? '—'}</span></div>
     <div class="txt">${esc(r.text)}</div></div>`).join('')
    : '<div class="empty">nothing yet</div>';

  document.getElementById('alerts').innerHTML = d.alerts.length ? d.alerts.map(a =>
    `<div class="row"><div class="top">
       <span class="src">${esc(a.source)}</span>
       <span class="score hot">${a.score}</span></div>
     <div class="txt" style="direction:ltr;text-align:left">${esc(a.reasons)}</div>
     ${a.url?`<a href="${esc(a.url)}" target="_blank" rel="noopener">open post</a>`:''}
     </div>`).join('')
    : '<div class="empty">no verified matches — deep-checked tiers only</div>';

  const max = Math.max(1, ...d.hourly.map(h => h.n));
  document.getElementById('spark').innerHTML = d.hourly.length
    ? d.hourly.map(h => `<i style="height:${Math.round(h.n/max*100)}%"
        title="${esc(h.hour)}: ${h.n}"></i>`).join('')
    : '';

  // Standing watches: what a visitor asked us to look out for, and whether it
  // has turned up. An empty table is ambiguous — "nobody registered" and "we
  // never fetched" look identical — so the caption always says which it is.
  const w = d.watches || {rows: [], source: '', error: ''};
  document.getElementById('watches').innerHTML = w.rows.length
    ? '<tr><th>vehicle registered</th><th>status</th></tr>' + w.rows.map(r =>
        `<tr><td class="wid">${esc(r.identifier)}${r.note
            ? `<div class="wnote">${esc(r.note)}</div>` : ''}</td>
         <td class="${r.hits ? 'whit' : 'wok'}">${r.hits
            ? `${r.status === 'MATCH' ? 'MATCH' : 'raw-text lead'} · ${
                nf.format(r.hits)} post${r.hits > 1 ? 's' : ''} to check`
            : esc(r.status)}${r.reviewed
            ? `<div class="wnote">${nf.format(r.reviewed)} checked by hand, not sightings</div>`
            : ''}</td></tr>`).join('')
    : `<tr><td class="empty">${w.computing
        ? 'checking the registry…' : 'no watches held locally'}</td></tr>`;
  document.getElementById('watch-source').textContent =
    w.error ? 'watch check failed — ' + w.error
            : w.computing ? 'first check running' : 'source: ' + w.source;

  document.getElementById('sources').innerHTML =
    '<tr><th>source</th><th>posts</th><th>queued</th><th>vehicles</th></tr>' +
    d.sources.map(s => `<tr><td>${esc(s.source)}</td><td>${nf.format(s.posts)}</td>
      <td class="${s.queued?'q':''}">${s.queued||''}</td>
      <td>${nf.format(s.vehicles)}</td></tr>`).join('');
}
refresh();
setInterval(refresh, 5000);
</script></body></html>
"""

# The page polls /api every few seconds. _snapshot() costs a couple of seconds
# once the registry passes a few hundred thousand rows, and ThreadingHTTPServer
# runs each poll in its own thread - so overlapping polls piled up, contended on
# SQLite and dragged a 2-second query out to SEVENTY seconds. The first fetch
# never returned, so the page sat on "connecting..." forever.
#
# One computation is shared by every caller inside the TTL; a concurrent caller
# waits on the lock and then gets the fresh value instead of starting its own.
_CACHE_TTL = 4.0
_cache_lock = threading.Lock()
_cache: dict = {"at": 0.0, "data": None}


def _snapshot_cached() -> dict:
    now = time.monotonic()
    if _cache["data"] is not None and now - _cache["at"] < _CACHE_TTL:
        return _cache["data"]
    with _cache_lock:
        now = time.monotonic()          # re-check: another thread may have run
        if _cache["data"] is not None and now - _cache["at"] < _CACHE_TTL:
            return _cache["data"]
        data = _snapshot()
        _cache["data"] = data
        _cache["at"] = time.monotonic()
        return data


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/api"):
            try:
                body = json.dumps(_snapshot_cached()).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
            except Exception as e:
                body = json.dumps({"error": str(e)}).encode("utf-8")
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
        else:
            body = PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass  # keep the console clean


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8770)
    args = parser.parse_args()
    db.init()
    # started before the server so the first page load already has a value, or
    # at least an honest "checking" state rather than a silent empty table
    threading.Thread(target=_watch_refresher, daemon=True).start()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Dashboard live at http://localhost:{args.port}  (Ctrl+C to stop)",
          flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
