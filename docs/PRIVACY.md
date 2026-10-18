# Privacy and data policy

*Last reviewed 2026-10-18. This is the policy the public page at
https://mycarsd.online/privacy.html summarises; this file is authoritative.*

## What the registry is

A free public index of **vehicles**, built from posts that were already public
on Facebook groups, Facebook pages and Telegram channels about vehicles looted
or lost in the Sudan war. It exists so a family can type a chassis or plate
number and learn where that number has appeared.

## What we collect

| data | from | kept where | published? |
|---|---|---|---|
| Post text, date, link and source | public groups, pages, channels; forwards to our Telegram bot | database | the **vehicle record** derived from it is published with a link to the original post. The full post text is not published. |
| Photos attached to posts | same | database folder | **no**. Photos are never republished. |
| OCR text read from photos | our own transcription | database only | **no**. A photographed registration card or ID can contain the owner's name, address and phone. This text is used only to ground identifiers and is never exported. |
| Per-vehicle fields: make, model, colour, year, chassis, plate, status, location, description, **contact number as written in the post** | extracted from the post | database and site | yes. A contact number that a poster published with their own listing is shown beside a scam warning. |
| Standing-watch registrations: identifier, contact, note | the form on the site, with the registrant's consent | Netlify Forms and the operator's machine | **no**. Used only to tell the registrant their number appeared. Never shown on the site or the dashboard. |
| Analytics (Microsoft Clarity): device, country, pages, clicks, session recordings, custom events | visitors to mycarsd.online | Microsoft Clarity | not published. See below. |

## Search queries are recorded

The site sends each search query, **including the number typed**, to Microsoft
Clarity as a custom event. This is a deliberate choice by the operator: if a
stranger ever searches for the operator's own stolen car, that is an
investigative signal. We disclose it here so nobody is surprised. Queries are
not linked to a name or contact, are not published, and are kept under
Clarity's retention (currently 30 days for recordings and up to 13 months for
aggregated data). If you do not want your query recorded, search with your
browser's tracking protection on, or ask us to look a number up for you.

## What we never do

* We never charge anyone, for anything.
* We never contact the person who posted a vehicle, and we never give a
  registrant's contact to anyone.
* We never ask for a chassis number in a private message.
* We never publish photos, OCR text, or watch registrations.

## Your rights and how to ask

Anyone can ask us to **remove or correct** a record that concerns them: a
contact number they want taken down, a vehicle entry that is wrong, a watch
registration they want deleted. Write to the contact address on the support
page, or message the Telegram bot, with the number or link concerned. We act
within 7 days and confirm in writing. Records removed from the registry are
also removed from the next published build.

## Where data lives

The database and photos live on the operator's own machine (moving to a cloud
server when funded). The published site is served by Netlify (US/EU edge).
Watch registrations are stored by Netlify Forms. Analytics by Microsoft Clarity.
Model providers (Anthropic, OpenAI, Google) receive post text and photos for
extraction under their API terms; no watch registration or analytics data is
ever sent to a model.

## Legal basis and limits

The project is run in the public interest by volunteers; it is not a government
body and is not affiliated with the Sudan Police or the Traffic Department.
Collection from Facebook is limited to public groups and pages and is being
replaced by lists sent directly by their administrators. If you administer a
group whose posts appear here and object, tell us and we will stop collecting
from it.
