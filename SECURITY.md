# Security and privacy reports

If you find a vulnerability in this code, a secret committed by mistake, or a
page on mycarsd.online exposing personal data it should not, please report it
privately rather than in a public issue.

* Email the contact address on https://mycarsd.online/support.html, or
* message the Telegram bot named on the site.

Tell us what you found and how to reproduce it. We acknowledge within 72 hours,
fix exposure of personal data within 7 days, and credit you if you wish.

## Scope

* The code in this repository.
* The published site (mycarsd.online) and its data files.
* Messages sent by the project (Telegram, email, WhatsApp drafts).

Out of scope: the third-party platforms the project reads from or publishes
through (Facebook, Telegram, Netlify, Microsoft Clarity, model providers).

## Secrets

Credentials are never in the repository (`.gitignore`, pre-commit check in
`scripts/check_no_secrets.py`). If you see one anyway, report it; it will be
rotated the same day.
