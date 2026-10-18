# Contributing

Thank you. This project helps families in and from Sudan find looted cars, and
it is run by volunteers. Useful contributions, in rough order of impact:

1. **Partner intake**: if you administer a recovery group or a field platform,
   send us your lists directly (see `docs/RUNBOOK.md`, "Partner intake"). This
   replaces scraping and is the single most valuable thing anyone can do.
2. **Arabic list parsing**: new spellings, new list layouts, new region
   markers. Start from `carhunt/kashf.py` and `carhunt/aliases.py`; add a case
   to `scripts/check_identifier_rule.py` or `scripts/check_watch_rule.py`.
3. **Search and the page**: `carhunt/export_site.py`. The Python
   normalisation helpers and the JavaScript in the template must stay
   equivalent; change one, change both, and test mixed queries (a model word
   plus a year, a plate with its region marker).
4. **Messaging bots** (WhatsApp, Telegram search), the low-data page, and the
   cloud migration: see `funding/` on the support page for the roadmap.

## Ground rules

* Read `docs/SAFEGUARDING.md` and `docs/PRIVACY.md` first. Changes that would
  publish photos, OCR text, watch contacts, or that would message a poster, are
  rejected.
* **No identifiers in code.** The car a deployment searches for lives only in
  `config.yaml`. Tests derive their cases from it.
* **No secrets in the repo.** `scripts/check_no_secrets.py` runs as a pre-commit
  hook; install it with `python scripts/install_hooks.py`.
* Every script that prints Arabic reconfigures stdout to UTF-8 (Windows).
* Measure, don't assume: when a change affects extraction, report claimed vs
  stored rows and distinct chassis, never raw row counts.

## Running the checks

```powershell
python scripts\check_identifier_rule.py
python scripts\check_watch_rule.py
python scripts\check_no_secrets.py
```

## Reporting a problem

Open an issue without personal data (no phone numbers, no documents). For a
security or privacy problem, see `SECURITY.md`.
