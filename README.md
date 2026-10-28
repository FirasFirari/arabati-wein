# Arabati Wein — عربتي وين؟

**A free, scam-free public registry of vehicles looted or lost in the Sudan war.**
Live at **https://mycarsd.online** · support and funding: https://mycarsd.online/support.html
Facebook Page: https://www.facebook.com/profile.php?id=61596074817604 · English: https://mycarsd.online/en.html
Guides (Arabic): [how to search for your car](https://mycarsd.online/guide.html) · [scams to avoid](https://mycarsd.online/scams.html) · [impound yards](https://mycarsd.online/yards.html)

When Khartoum emptied in 2023, cars were taken by the hundreds of thousands.
They surface again in police yards, in field volunteers' lists, in recovery
committees' photographed handwritten pages and in dealer groups, scattered
across dozens of Facebook groups and Telegram channels. A family that still
holds its chassis number had nowhere to type it. This project collects those
lists, turns them into one searchable record per vehicle, lets anyone search by
chassis or plate in Arabic or Latin digits, registers a standing watch when
nothing is found, and routes every searcher to the official free recovery
channels and away from the extortion networks that prey on them.

It was built by one Sudanese family looking for their own car. The code is
published so that it can outlive them, be audited, and be run for other
communities.

## What it does

* **Collects** public posts from Telegram channels and Facebook recovery groups
  and pages, plus anything forwarded to a Telegram intake bot.
* **Reads** them: a deterministic parser for the long numbered كشف lists, OCR
  for every photo (a VIN stamped on a compliance plate is invisible to text
  search), and model extraction for prose posts.
* **Refuses fabrication**: a grounding gate deletes any identifier not present
  in the source text or its OCR; coverage, no-shrink and reconcile checks catch
  silent loss. Over a hundred invented numbers were caught this way; none were
  self-reported by the model that produced them.
* **Publishes** a static Arabic-first site with an English toggle: one dossier
  per vehicle, newest sighting first, a link to every original post, a scam
  warning beside every contact number, and the official channels as the
  empty state.
* **Watches**: a visitor who finds nothing leaves a number; every hour the
  number is checked against all new posts and OCR, and a hit produces a
  Telegram alert for the operator and a ready-to-send message for the family.
* **Alerts only on identifiers, never on resemblance.** A model score cannot
  trigger an alert on its own.

Read `docs/ARCHITECTURE.md` for how the pieces fit, `docs/RUNBOOK.md` to run
it, `docs/PRIVACY.md` and `docs/SAFEGUARDING.md` for the rules that do not
change with money.

## Quick start

```powershell
pip install -r requirements.txt
copy config.example.yaml config.yaml       # fill in keys, Telegram, and the car profile
python scripts\check_identifier_rule.py    # the matching rule fires on the configured car only
python scripts\check_watch_rule.py
python -m carhunt.telegram_monitor --backfill 300
python -m carhunt.intake_bot
python -m carhunt.search 1122334           # search the local registry
python -m carhunt.export_site              # build the public site
```

`config.yaml` is private and gitignored: it holds every credential and the
profile of the car a deployment searches for. **No identifier from that profile
may appear in code**; tests derive their cases from the config.

## Repository layout

```
carhunt/            the package: collectors, gates, parsers, providers, export, watches, notify
scripts/            operational tools: OCR sweeps, raw sweep, deploy, checks
docs/               architecture, runbook, privacy, safeguarding
prompts/            model prompts with {{VIN}} / {{PLATE}} templates
site/static/        support, privacy, stats pages, robots, sitemap, llms.txt
config.example.yaml every key, documented
```

## Status (October 2026)

Measured from the database on 2026-10-18 and republished monthly at
https://mycarsd.online/stats.html: 159,310 posts collected since 22 August 2026,
78,379 distinct chassis numbers searchable, 93,584 identified vehicles, 40
standing watches, 2 leads relayed, 0 confirmed recoveries so far. The project
is unfunded and runs on one computer; the funding ladder and what each level
adds are on the support page.

## Contributing and reporting

See `CONTRIBUTING.md`. Security or privacy problems: `SECURITY.md`. If you
administer a recovery group or a field platform, the most valuable thing you can
do is send us your lists directly; see "Partner intake" in the runbook.

## Licence

Apache License 2.0. See `LICENSE`.
