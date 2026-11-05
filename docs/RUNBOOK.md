# Runbook

Everyday operation of an Arabati Wein deployment. Commands are PowerShell on
Windows; the code is plain Python 3.12+ and runs anywhere. Current as of
2026-11-05.

## First-time setup

```powershell
pip install -r requirements.txt
playwright install chromium               # the Facebook collector's browser
copy config.example.yaml config.yaml      # then fill it in
python scripts\check_identifier_rule.py   # the car profile parses and the rule fires correctly
python scripts\check_watch_rule.py
python scripts\install_hooks.py           # pre-commit secrets check
python -m pytest                          # public + private tests (see tests/conftest.py)
```

Credentials live in `config.yaml` or the environment, never in code:
`GEMINI_API_KEY` (an AI Studio key on a project WITHOUT billing),
`NETLIFY_AUTH_TOKEN` (or `site/netlify_token.txt`), `site/cloudflare_token.txt`
(Cloudflare Pages: Edit), Telegram `api_id` / `api_hash` / `bot_token`,
`healthchecks.ping_key`. `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` only matter if
`live_scoring.enabled` is turned on, which costs money; it is off.

## Scheduled tasks (Windows Task Scheduler, all named `CarHunt *`)

| task | when | what | log |
|---|---|---|---|
| Channel Monitor | logon | `python -m carhunt.telegram_monitor` | `data/logs/monitor.log` |
| Facebook Service | logon | `python -m carhunt.fb_service` | `data/logs/fb_service.log` |
| Intake Bot | logon | `python -m carhunt.intake_bot` | `data/logs/intake_bot.log` |
| Watchdog | logon, loops every 5 min | `python -m carhunt.watchdog --loop` | `data/logs/watchdog.log` |
| Dashboard | logon | `python -m carhunt.dashboard` → http://localhost:8770 | `data/logs/dashboard.log` |
| Raw Sweep | hourly | `scripts\raw_sweep.py --fetch` | `data/logs/raw_sweep.log` |
| Photo OCR | every 2 h | `scripts\gemini_ocr_sweep.py --hours 1.75` | `data/logs/gemini_ocr.log` |
| Daily Free Lane | 12:00, retried hourly to 22:00 | `scripts\daily_free_lane.py` | `data/logs/daily_free_lane.log` |
| Backup | 13:00, retried hourly to 23:00 | `scripts\backup_db.py` | `data/logs/backup.log` |
| Clarity Snapshot | 21:00 | `scripts\clarity_export.py --days 1 --save data\clarity` | — |

Plus, on Netlify (runs with the PC off): `site/functions/submission-created.js`
announces each new watch registration on Telegram.

**Pause / resume:** `scripts\carhunt_control.ps1 status|pause|resume [-Light]`
(or the desktop shortcuts). Pause and resume each send a Telegram line. Services
hold code in memory: restart them after a code or config change.

**When something stops happening, you hear about it:**
* the watchdog restarts a stuck service and says so on Telegram
  (`telegram.notify_chat_id`);
* a scheduled job that has not *succeeded* for too long (stamps in
  `data/health/job_*.json`) is started again by the watchdog within 5 minutes;
* Healthchecks.io (`healthchecks.ping_key`) alerts when pings stop arriving:
  `carhunt-alive` DOWN means the PC is off, asleep or fully paused; any other
  check DOWN or failed means that job is broken.
* `data/logs/db_locks.log` names any process that held the database write lock
  for over a second.

## The $0 processing lane

`daily_free_lane.py` runs all of it, in this order, and stamps/pings the result:

1. `python -m carhunt.free_pass` — deterministic settles
2. `scripts\kashf_verdicts.py` + `scripts\ingest_local_verdicts.py --tier kashf_parser` — the كشف lists
3. `scripts\openai_backlog.py --provider gemini --use-ocr ...` for each of the two
   Flash-Lite models (their own free daily quotas), then `--replay-spool` for any
   save that failed
4. RECONCILE (claimed vehicles vs stored rows) and the raw sweep with `--db-scan`

Photos are read by the separate Photo OCR task (`gemini_ocr_sweep.py`): each
distinct image once (files are hashed; copies get the text at zero calls), a
second read for VIN-like photos, riskiest-first by check digit. The Gemini lane
holds photo posts until their photos have OCR text, so every identifier can be
checked.

Rules that do not change: never put a guessed price in `openai_backlog.py`'s
PRICES table (`--max-calls` is the price-independent guard); long manual runs
are detached (`Start-Process -WindowStyle Hidden`); never start a paid lane
without the owner.

## Standing watches and leads

```powershell
python scripts\fetch_watches.py --check      # Netlify form -> data/watches.json, then match
python scripts\check_watches.py              # human report WITH contacts (personal data)
python -m carhunt.notify --whoami            # one-time: find your Telegram chat id
python -m carhunt.notify --test "hello"      # confirm the operator channel works
python -m carhunt.notify --outbox            # pending WhatsApp drafts for registrants
```

A raw-sweep hit is a **lead, never a sighting**: open the post, judge it, then
record the verdict in `data/watch_reviewed.json` so it is not raised again.
Relay the lead with the pre-written message in `data/leads_outbox.md`; follow
`docs/SAFEGUARDING.md` to the letter. A "verify by eye" Telegram from the
Gemini lane is the same: a reason to open the photo, not a match.

## Publishing the site

```powershell
scripts\carhunt_control.ps1 pause -Light     # the export needs ~2 GB of RAM
python -m carhunt.export_site                # -> site/arabati-wein.html, core.bin, detail.bin
scripts\carhunt_control.ps1 resume
python scripts\build_deploy.py               # -> site/deploy (stats, sitemap, static pages; allow-list enforced)
python scripts\netlify_deploy.py             # Cloudflare payloads -> Netlify DRAFT -> verify -> publish
```

What `netlify_deploy.py` refuses, and why:
* anything `carhunt/publish_guard.py` finds in what ships — decompressed: a
  number listed in `publish.forbidden_contacts`, a phone number in free text
  that is nobody's listed contact, an unfilled `__TOKEN__`;
* a build with more than 5% fewer vehicles than the live one (`--allow-shrink`);
* a draft that fails `verify_deploy.py` at its own URL.

The payloads go to Cloudflare Pages first (`scripts/cloudflare_deploy.py`,
content-hashed names, the previous build kept); the page loads them from there
and falls back to the copy on Netlify. Each deploy records its git commit and
the deploy it replaced in `data/deploy_history.jsonl`.
**Undo:** `python scripts\netlify_deploy.py --rollback` (or `--rollback <deploy id>`).

## Backups

`scripts\backup_db.py` (the Backup task): a consistent snapshot of
`data/carhunt.db` via the SQLite backup API, `integrity_check`, zstd (~1 GB →
~100 MB), plus the Telegram sessions, `config.yaml` and the watch files, into
`data/backups/<date>/` with a MANIFEST written last; 7 kept. A weekly copy goes
to the Google Drive folder ENCRYPTED with age, once `backup.age_recipient` is
set and `age` is installed — nothing unencrypted ever leaves the machine.

Restore: `python -c "from compression import zstd; open('carhunt.db','wb').write(zstd.decompress(open('data/backups/<date>/carhunt.db.zst','rb').read()))"`
into a scratch folder, `PRAGMA integrity_check`, then swap it in with
everything paused. From an offsite copy, first decrypt it with the private key
kept off this machine: `age -d -i <private key file> -o backup.tar
carhunt-<date>.tar.age`, then unpack the tar (it holds the dated folder above).
Verified 2026-11-05: an offsite copy decrypted, matched its MANIFEST sha256 and
held an intact database.

## Repairs

`python scripts\regate_registry.py` (dry run) / `--apply` brings rows already in
the registry up to the current rules: chassis_norm recomputed, doubt flags added
(`vehicles.low_confidence`), lost rows re-queued. It never deletes a value.
Take a backup first.

## Partner intake (replacing scraping)

Recovery groups and field platforms can send lists directly: a Telegram
forward to the intake bot, a shared folder of photos, or a CSV. Ingest with
`python -m carhunt.ingest add --source "partner:<name>" ...` or `batch posts.json`,
then run the lane above. Credit the source on every record.

## Things that bit us (keep)

* Windows console is cp1252: every script reconfigures stdout to UTF-8, or an
  Arabic plate in a print crashes a write loop halfway.
* Writes go through `db.write_tx()` (BEGIN IMMEDIATE, short, retried). Never
  hold a write transaction across a network call; one in the OCR sweep held
  the lock 94 s and stalled every other writer.
* Verdict files come in two shapes (nested `verdict` or flat); both are accepted.
* An ingest summary is not success; compare file counts and row counts.
* grep cannot see inside the gzipped payloads: check what ships with
  `python -m carhunt.publish_guard site\deploy`.
