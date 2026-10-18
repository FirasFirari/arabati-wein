# Runbook

Everyday operation of an Arabati Wein deployment. Commands are PowerShell on
Windows; the code is plain Python 3.12+ and runs anywhere.

## First-time setup

```powershell
pip install -r requirements.txt
copy config.example.yaml config.yaml      # then fill it in
python scripts\check_identifier_rule.py   # the car profile parses and the rule fires correctly
python scripts\check_watch_rule.py
python scripts\install_hooks.py           # pre-commit secrets check
```

Credentials live in `config.yaml` or the environment, never in code:
`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`, `GOOGLE_VISION_API_KEY`,
`NETLIFY_AUTH_TOKEN` (or `site/netlify_token.txt`), `RESEND_API_KEY` (or
`site/resend_key.txt`), Telegram `api_id` / `api_hash` / `bot_token`.

## Services (start at logon as scheduled tasks)

| task | module | log |
|---|---|---|
| Channel monitor | `python -m carhunt.telegram_monitor` | `data/logs/monitor.log` |
| Facebook service | `python -m carhunt.fb_service` | `data/logs/fb_service.log` |
| Intake bot | `python -m carhunt.intake_bot` | `data/logs/intake_bot.log` |
| Watchdog (5 min) | `python -m carhunt.watchdog` | `data/logs/watchdog.log` |
| Dashboard | `python -m carhunt.dashboard` → http://localhost:8770 | `data/logs/dashboard.log` |
| Raw sweep (hourly) | `scripts\raw_sweep.py --fetch` | `data/logs/raw_sweep.log`, `data/logs/notify.log` |
| Daily free lane (12:00) | `scripts\daily_free_lane.py` | `data/logs/daily_free_lane.log` |

Pause and resume everything: `scripts\carhunt_control.ps1 status|pause|resume [-Light]`.
Services hold code in memory; restart after a code or config change.

## The $0 processing lane (run in this order)

```powershell
python scripts\gvision_sweep.py --dry-run         # OCR new photos first (cheap, raises gate coverage)
python -m carhunt.free_pass                        # deterministic settles
python scripts\kashf_verdicts.py --out data\kashf_wN
python scripts\ingest_local_verdicts.py data\kashf_wN\verdicts_kashf.json --batch data\kashf_wN\batch_kashf.json --tier kashf_parser
python scripts\openai_backlog.py --provider gemini --use-ocr --skip-over-chars 8000 --workers 3 --max-calls 460 --budget 10
```

**Always reconcile** claimed vehicles vs stored rows after any run. Never put a
guessed price in `openai_backlog.py`'s PRICES table; `--max-calls` is the
price-independent guard. Long runs must be detached (`Start-Process -WindowStyle Hidden`).

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
`docs/SAFEGUARDING.md` to the letter.

## Publishing the site

```powershell
scripts\carhunt_control.ps1 pause -Light     # the export needs RAM
python -m carhunt.export_site                # -> site/arabati-wein.html, core.bin, detail.bin
scripts\carhunt_control.ps1 resume
python scripts\build_deploy.py               # -> site/deploy (stats, sitemap, static pages; allow-list enforced)
python scripts\netlify_deploy.py             # digest-API deploy
python scripts\verify_deploy.py              # live checks: form intact, bins match, pages 200
```

The two `.bin` files and `index.html` must come from the **same** export
(`build_deploy.py` checks the `?v=` hashes). Keep backups in `site/backups/`,
never in `site/deploy/`.

## Partner intake (replacing scraping)

Recovery groups and field platforms can send lists directly: a Telegram
forward to the intake bot, a shared folder of photos, or a CSV. Ingest with
`python -m carhunt.ingest add --source "partner:<name>" ...` or `batch posts.json`,
then run the lane above. Credit the source on every record.

## Things that bit us (keep)

* Windows console is cp1252: every script reconfigures stdout to UTF-8, or an
  Arabic plate in a print crashes a write loop halfway.
* `vehicles.post_id` has no index: never query per post in a loop; fetch once
  and group in Python.
* SQLite has one writer: do not run `free_pass` and a scorer at the same time.
* Verdict files come in two shapes (nested `verdict` or flat); both are accepted.
* An ingest summary is not success; compare file counts and row counts.
