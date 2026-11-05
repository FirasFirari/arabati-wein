# Architecture

Arabati Wein (عربتي وين؟, "where is my car?") turns the recovery lists that
Sudanese police committees, field volunteers and community groups post as text
and photos into one searchable registry of vehicles looted or lost in the war,
published at https://mycarsd.online. Every stage below is a plain Python module;
the whole thing runs from one machine against one SQLite file. Current as of
2026-11-05; the operational detail is in `docs/RUNBOOK.md`.

```
 collectors ──► posts + media (SQLite) ──► photo OCR ──► lanes ──► GATE ──► vehicles ──► export ──► static site
                      │                                                         │                 (Netlify page,
                      └── raw sweep (hourly) ◄── standing watches ◄── Netlify form      Cloudflare payloads)
                                 │
                                 └── notify (Telegram / WhatsApp draft) ── dead-man pings (Healthchecks.io)
```

## 1. Collectors (`carhunt/telegram_monitor.py`, `carhunt/fb_service.py`, `carhunt/intake_bot.py`)

* **Telegram channels** are read live with Telethon and backfilled on first run.
* **Facebook groups and pages** are read with a logged-in browser session
  (Playwright), using in-group search and chronological feeds; the harvester
  reveals each post's permalink by a scripted hover because Facebook leaves it
  as a placeholder until then. See `fb_harvest.py`.
* **Intake bot** (`@ArabatiWeinBot`) lets anyone forward a post or screenshot.

Every post is stored once, keyed by a stable `source_ref`, with its photos in
`data/media/`. Posts are **never modified**; everything downstream is derived.

## 2. Free checks the moment a post arrives (`carhunt/pipeline.py`, `carhunt/prefilter.py`)

1. **Deterministic identifier scan**: the configured car's chassis tail, full
   VIN and plate are matched token by token (never against flattened text,
   because lists of numbers invent sequences when joined). This is the only
   check allowed to declare a match on its own authority. Guarded by
   `tests/test_identifier_rule.py` / `scripts/check_identifier_rule.py`.
2. **Relevance gate**: no vehicle vocabulary, no chassis-like number, no image
   → never reaches a model.
3. **Duplicate reuse**: a content hash lets one verdict serve every cross-post.

Live paid model scoring is **off** (`live_scoring.enabled`): anything else
waits for the free daily lane.

## 3. Photo OCR (`scripts/gemini_ocr_sweep.py`)

Photos are OCR'd into `media.ocr_text` (database-only: it holds registration
card text) on the Gemini free tier, 6 photos per call (10+ leaks numbers
between photos). Files are hashed (`media.sha1`): each distinct image is read
once and its text lands on every byte-identical copy — 75% of the backlog was
copies. VIN-like photos get a second read from another model, riskiest first by
check digit (`carhunt/vin.py`); disagreements keep both reads.

## 4. Extraction lanes

* **كشف parser** (`carhunt/kashf.py`): deterministic parser for the long numbered
  recovery lists. Measured against a frozen answer key it did not write:
  99.30% recall (`tests/test_kashf_gold.py`), zero fabrications by construction.
* **Gemini free tier** (`scripts/openai_backlog.py --provider gemini`) for prose
  posts, through `carhunt/analyzer.py`'s prompt and verdict contract. Photo posts
  wait until their OCR text exists.
* **free_pass** (`carhunt/free_pass.py`) settles posts the free checks decide.

Each lane is run by `scripts/daily_free_lane.py`, which reconciles claimed
vehicles against stored rows afterwards.

## 5. The gate every write passes (`carhunt/gates.py`, `carhunt/db.py`)

`db.add_vehicles` / `db.save_analysis` call the gate for **every** lane:

| check | what happens |
|---|---|
| **grounding** | a chassis / plate / contact / price must sit inside ONE number of the post text + its trusted photo OCR; otherwise it is nulled |
| **photo not yet read** | the value is kept and marked `low_confidence` (it may be the only record of a car) |
| **disputed OCR** | a value found only in a disputed or unverified OCR read is kept and marked |
| **prose** | sentences in identifier fields are stripped |

Plus, per lane: coverage and no-shrink (`scripts/ingest_local_verdicts.py`),
the spool for saves that fail (`--replay-spool`), and the alert cap in
`db.set_verdict` (a score at the alert line needs an identifier of the target
behind it; a model can never supply that evidence itself). Writes are short
`BEGIN IMMEDIATE` transactions (`db.write_tx`); verdict and rows commit together.
`vehicles.low_confidence` holds a REASON and is shown on the page, never hidden.

## 6. Normalisation and search (`carhunt/normalize.py`, `carhunt/watches.py`, `carhunt/vin.py`)

Arabic-Indic digits fold to ASCII; chassis values keep letters (the plant
prefix identifies the factory) and O/I/Q fold to 0/1/0 in 17-character VINs,
where they are illegal; **plates are matched digit-run by digit-run**, because a
Sudanese plate reads `12345 خ1` and gluing it to `123451` matches nothing.
`carhunt/watches.py` is the one matching rule; the page's JavaScript mirrors it
and `tests/test_search_parity.py` / `tests/test_js_parity.py` run that
JavaScript in node to prove they agree.

## 7. Export and site (`carhunt/export_site.py`, `site/src/index.template.html`)

Sightings are grouped at build time into vehicle dossiers keyed by normalised
chassis (plate when there is no chassis); identical reposts are merged. The page
(~100 KB, template in `site/src/`) loads two gzip payloads: `core.bin` (number
search works as soon as it lands) and `detail.bin` (timelines, text search),
from **Cloudflare Pages** by content-hashed name, falling back to the copy on
Netlify. URLs carry a content hash because the page uses `force-cache`.

Privacy on the way out: a contact on `publish.forbidden_contacts` never ships;
phone numbers in free text that are not the row's own contact are masked;
registration-card text is redacted. `carhunt/publish_guard.py` checks the
decompressed payloads at export and again before upload.

`scripts/build_deploy.py` assembles `site/deploy/` (allow-list
`scripts/deploy_allowlist.py`); `scripts/netlify_deploy.py` uploads the payloads
to Cloudflare, creates a Netlify **draft**, verifies it with
`scripts/verify_deploy.py`, publishes, verifies live, and records the deploy
(`--rollback` re-publishes the previous one).

## 8. Standing watches and notifications (`carhunt/watches.py`, `scripts/raw_sweep.py`, `carhunt/notify.py`, `carhunt/verify.py`)

A visitor whose search returns nothing can leave a chassis or plate and a
contact (Netlify Forms). The Netlify function `site/functions/submission-created.js`
announces it to the operator on Telegram at once (PC on or off). Hourly,
`raw_sweep.py` pulls the registrations and checks the configured car **and every
watch** against raw post text plus OCR, and the car against new registry rows.
Watch hits are queued and read by headless Claude (`carhunt/verify.py`) before
the operator gets one Telegram message; a possible sighting gets a ready-to-send
message for the family. Leads on the deployment's OWN car get the same reading
plus a look at the post's photos (a sealed copy, Read as the only tool), but
the verdict is advice only: written onto the lead and sent to the operator,
never applied. Messages never quote the post, never suggest contacting
the poster, and always route to the official free channels
(`docs/SAFEGUARDING.md`).

## 9. Operations (`carhunt/watchdog.py`, `carhunt/health.py`, `carhunt/deadman.py`, `scripts/backup_db.py`)

Services heartbeat to `data/health/*.json` only while genuinely connected; the
watchdog restarts stuck ones, catches up scheduled jobs that have not succeeded
for too long, and alerts on Telegram. Dead-man pings to Healthchecks.io report a
PC that is off, asleep or paused from outside. A nightly verified, compressed
backup keeps 7 copies; a weekly age-encrypted copy goes offsite.
`carhunt_control.ps1 pause|resume` stops everything cleanly and says so.

## Tests

`python -m pytest`: public tests use a fictional car (`tests/fixtures/config.test.yaml`)
and run in CI (`.github/workflows/ci.yml`, with the secrets scan and ruff);
`@pytest.mark.private` tests use the real config and database and run locally.

## What is private and why

`config.yaml` (keys, Telegram secrets, the car profile), `data/` (every post,
photo, OCR text and the watch contacts), `reference/` (the family's documents),
`private/` and the operator logs are gitignored. OCR of a photographed
registration card holds the owner's name and address: it stays in the database
and is never exported. See `docs/PRIVACY.md`.
