# Architecture

Arabati Wein (عربتي وين؟, "where is my car?") turns the recovery lists that
Sudanese police committees, field volunteers and community groups post as text
and photos into one searchable registry of vehicles looted or lost in the war,
published at https://mycarsd.online. Every stage below is a plain Python module;
the whole thing runs from one machine against one SQLite file.

```
 collectors ──► posts + media (SQLite) ──► gates ──► vehicles ──► export ──► static site
                      │                                  │                      │
                      └── raw sweep (hourly) ◄── standing watches ◄── Netlify form
                                 │
                                 └── notify (Telegram / WhatsApp draft / email)
```

## 1. Collectors (`carhunt/telegram_monitor.py`, `carhunt/fb_service.py`, `carhunt/intake_bot.py`)

* **Telegram channels** are read live with Telethon and backfilled on first run.
* **Facebook groups and pages** are read with a logged-in browser session
  (Playwright), using in-group search and chronological feeds; the harvester
  reveals each post's permalink by a scripted hover because Facebook leaves it
  as a placeholder until then. See `fb_harvest.py`. This is the stage the
  project intends to replace with lists sent directly by group admins and field
  platforms (`docs/RUNBOOK.md`, "partner intake").
* **Intake bot** (`@ArabatiWeinBot`) lets anyone forward a post or screenshot.

Every post is stored once, keyed by a stable `source_ref`, with its photos in
`data/media/`. Posts are **never modified**; everything downstream is derived.

## 2. Gates before any model call (`carhunt/prefilter.py`, `carhunt/free_pass.py`)

1. **Deterministic identifier scan**: the configured car's chassis tail, full
   VIN and plate are matched token by token (never against flattened text,
   because lists of numbers invent sequences when joined). This is the only
   check allowed to declare a match on its own authority, and it is guarded by
   `scripts/check_identifier_rule.py`.
2. **Relevance gate**: no vehicle vocabulary, no chassis-like number, no image
   → never reaches a model.
3. **Duplicate reuse**: a content hash lets one verdict serve every cross-post.

## 3. Extraction (`carhunt/kashf.py`, `carhunt/extract.py`, `carhunt/analyzer.py`, OCR scripts)

* Photos are OCR'd once into `media.ocr_text` (Google Cloud Vision, then the
  Gemini free tier for the remainder). OCR text is a **verifiable artifact**: an
  identifier read off a photo becomes text the grounding gate can check.
* **كشف parser** (`kashf.py`): deterministic parser for the long numbered
  recovery lists, 99.6% recall and zero fabrications measured; it beats every
  model on these lists and costs nothing.
* **Model extraction** for ordinary prose posts, on whichever provider is
  configured (Claude, OpenAI fallback, Gemini, Vertex, Moonshot). Output is one
  record per vehicle: make, model, colour, year, chassis, plate, status,
  location, contact, description.

## 4. The ingest gates (`scripts/ingest_local_verdicts.py`)

Four gates, each added after a real loss:

| gate | what it refuses |
|---|---|
| **coverage** | a batch where any post id came back missing |
| **grounding** | any chassis / plate / contact / price whose digits are absent from the post's own text **plus its OCR text** |
| **no-shrink** | replacing a post whose stored vehicles outnumber the incoming ones |
| **shortfall** (flag only) | far fewer vehicles than chassis-shaped numbers in the source |

Then **reconcile**: vehicles claimed by the extractor vs rows actually stored.
Over a hundred fabricated identifiers were caught by the grounding gate; not one
was self-reported by the model that produced it.

## 5. Normalisation and search (`carhunt/normalize.py`, `carhunt/aliases.py`, `carhunt/watches.py`)

Arabic-Indic digits fold to ASCII; chassis values keep letters (the plant prefix
identifies the factory); **plates are matched digit-run by digit-run**, because
a Sudanese plate reads `12345 خ1` and gluing it to `123451` matches nothing. The
Python helpers and the JavaScript in the published page must stay
byte-for-byte equivalent — change one, change both. `scripts/check_watch_rule.py`
guards the watch matcher.

## 6. Export and site (`carhunt/export_site.py`, `scripts/build_deploy.py`, `scripts/netlify_deploy.py`)

Sightings are grouped at build time into vehicle dossiers keyed by normalised
chassis (plate when there is no chassis). Identical reposts are merged. The page
ships as a 50 KB shell plus two gzip payloads: `core.bin` (number search works
as soon as it lands) and `detail.bin` (timelines, text search). URLs carry a
content hash (`?v=`) because the page uses `force-cache`.

`build_deploy.py` assembles `site/deploy/` from the export plus `site/static/`
(support, privacy, stats, robots, sitemap, llms.txt) and refuses any file not
in `scripts/deploy_allowlist.py`. `netlify_deploy.py` publishes via the digest
API. `verify_deploy.py` checks the live site afterwards.

## 7. Standing watches and notifications (`carhunt/watches.py`, `scripts/raw_sweep.py`, `carhunt/notify.py`)

A visitor whose search returns nothing can leave a chassis or plate and a
contact (Netlify Forms). The moment a submission lands, the Netlify function
`site/functions/submission-created.js` announces it to the operator on Telegram
(PC on or off; deployed with every `netlify_deploy.py` run). Hourly,
`raw_sweep.py` pulls the registrations and
checks the configured car **and every watch** against raw post text plus OCR,
which is current the moment a post is stored. A new hit produces: a Telegram
message to the operator, a ready-to-tap WhatsApp draft (or an email) for the
registrant, and a line in `data/leads_outbox.md`. Messages never quote the
post, never suggest contacting the poster, and always route to the official
free channels (`docs/SAFEGUARDING.md`).

## 8. Operations (`carhunt/watchdog.py`, `carhunt/health.py`, `carhunt/dashboard.py`, `scripts/carhunt_control.ps1`)

Services heartbeat to `data/health/*.json` only while genuinely connected; the
watchdog restarts the stuck ones. A local dashboard shows queues, tiers, watches
and health. `carhunt_control.ps1 pause|resume` stops everything cleanly.

## What is private and why

`config.yaml` (keys, Telegram secrets, the car profile), `data/` (every post,
photo, OCR text and the watch contacts), `reference/` (the family's documents)
and the operator logs are gitignored. OCR of a photographed registration card
holds the owner's name and address: it stays in the database and is never
exported. See `docs/PRIVACY.md`.
