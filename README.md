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
  number is checked against all new posts and OCR; a hit is read and judged
  before the operator gets one Telegram message, and a possible sighting comes
  with a ready-to-send message for the family.
* **Alerts only on identifiers, never on resemblance.** A model score cannot
  trigger an alert on its own.

Read `docs/METHOD.md` for what was measured and what went wrong on the way,
`docs/ARCHITECTURE.md` for how the pieces fit, `docs/RUNBOOK.md` to run it,
`docs/PRIVACY.md` and `docs/SAFEGUARDING.md` for the rules that do not change
with money.

## Pieces you can reuse

Three modules have no dependency outside the standard library and this
package, and are useful to anyone extracting identifiers from Arabic text with
a language model.

**The grounding gate** (`carhunt/gates.py`): reject any number a model returns
that is not in the source.

```python
from carhunt import gates

rows = [{"chassis": "421376"}, {"chassis": "999111"}]      # what a model returned
gates.check_entries(rows, "سانتافي 421376 ابيض", photos_unread=False)
# ["chassis='999111' not in the post or its OCR: nulled"]
# rows == [{"chassis": "421376"}, {"chassis": None}]
```

**The list parser** (`carhunt/kashf.py`): read Sudanese vehicle inventory lists
(كشف / حصر) without a model. It copies digits from the line or emits nothing.

```python
from carhunt import kashf

out = kashf.parse("هايلوكس 1خ63922 شاسي 774512")
# out["vehicles"][0] -> chassis "774512", plate "1 خ 63922", model_key "toyota hilux"
# out["residue"]     -> lines with a number but no vocabulary it knows, for a model to read
```

**The location mapper** (`carhunt/geo.py`): turn a free-text Sudanese location,
Arabic or Latin, into an OCHA state P-code, or say why it cannot.

```python
from carhunt import geo

geo.locate("امدرمان الثورة").kind     # "SD01"
geo.locate("Kordofan").kind           # "region_only": three states share the name
```

## Open data

`open-data/` holds aggregate counts from the registry as CSV with HXL hashtags
and state P-codes: vehicles by state, status and make, with a data
responsibility note and a KoboToolbox form for collecting watch registrations
offline. No identifier or contact is in it. Start at `open-data/README.md`.

## Quick start

```powershell
pip install -r requirements.txt
copy config.example.yaml config.yaml       # fill in keys, Telegram, and the car profile
python -m pytest                           # tests; the private ones need config.yaml + data/
python scripts\check_identifier_rule.py    # the matching rule fires on the configured car only
python scripts\check_watch_rule.py
python -m carhunt.telegram_monitor --backfill 300
python -m carhunt.intake_bot
python -m carhunt.search 1122334           # search the local registry
python -m carhunt.export_site              # build the public site
python scripts\build_deploy.py; python scripts\netlify_deploy.py   # draft, verify, publish
```

`config.yaml` is private and gitignored: it holds every credential and the
profile of the car a deployment searches for. **No identifier from that profile
may appear in code**: public tests use a fictional car
(`tests/fixtures/config.test.yaml`), private tests read the real config, and a
pre-commit hook (`scripts/install_hooks.py`) refuses keys, phone numbers and the
car's identifiers.

## Repository layout

```
carhunt/            the package: collectors, gate, parsers, providers, export, watches, notify, ops
scripts/            operational tools: daily lane, OCR sweep, raw sweep, backup, deploy, checks
tests/              pytest (public + private); fixtures use a fictional car
site/src/           the page template (HTML, CSS, search JavaScript)
site/static/        content pages, robots, llms.txt, headers
site/functions/     the Netlify function (instant watch-registration alert)
docs/               method, architecture, runbook, privacy, safeguarding
open-data/          aggregate counts (CSV + HXL + P-codes), dashboard, Kobo intake form
config.example.yaml every key, documented
```

## Status (November 2026)

Measured from the database on 2026-11-14 and republished at
https://mycarsd.online/stats.html: 199,117 posts collected since 22 August 2026,
90,441 distinct chassis numbers searchable, 106,213 identified vehicles, 137
standing watches, 2 leads relayed, 0 confirmed recoveries so far. 136,422 posts
are still waiting to be processed. The project runs on one computer. It has a
fiscal host (Open Source Europe, via Open Collective) and no funding yet; the
funding ladder and what each level adds are on the support page.

## Contributing and reporting

See `CONTRIBUTING.md`. Security or privacy problems: `SECURITY.md`. If you
administer a recovery group or a field platform, the most valuable thing you can
do is send us your lists directly; see "Partner intake" in the runbook.

## Licence

Apache License 2.0. See `LICENSE`.
