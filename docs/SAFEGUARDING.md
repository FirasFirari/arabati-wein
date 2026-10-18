# Safeguarding and do-no-harm

People searching for a looted car are a pre-qualified target for extortion:
they are known to be desperate and known to answer unknown numbers. The corpus
this registry is built from documents the scams in detail. These rules exist so
the registry never becomes one of them.

## Rules for the registry

1. **Free, always.** No fee to search, register a watch, or receive a lead.
2. **Identifiers, not resemblance.** An alert needs an exact chassis or plate
   match. A white 2015 Corolla looks like every other one, and chassis
   transplant is the standard laundering method. Model scores never trigger an
   alert (`db.set_verdict` caps any unbacked score below the alert threshold).
3. **No private channel.** We never ask for a chassis number in a direct
   message, and the site says so on every page. There is no official WhatsApp
   number for recovery in Sudan; any account claiming one is not the police.
4. **Route to the official channels.** Every zero-result screen, every lead
   message and every footer points to the free online report at
   `sudanpolice.net/reg/car_stealing` (which triggers the legal re-registration
   ban and the INTERPOL listing) and to the Traffic Department in person.
5. **A scam warning beside every contact number** shown on the site.
6. **Never contact the poster or holder.** Not us, and we tell registrants not
   to either. Approaching a holder directly is how families are extorted and
   how a car disappears again.
7. **Never quote a post back to a registrant.** A lead message says where the
   number appeared and links to the post; it never pastes the post text, which
   may carry the poster's phone.
8. **Never read a verification code to anyone.** Account hijacking via the
   six-digit code is the commonest attack on searchers; we warn about it.

## Handling a lead (operator checklist)

1. Open the post. Decide: found list, lost report (often the registrant's own
   post), or noise. Record the verdict in `data/watch_reviewed.json`.
2. If it is a genuine sighting, send the pre-written message from
   `data/leads_outbox.md` **as is**. It already tells the registrant what to
   do and what not to do.
3. Do not add the poster's name or number, your own number, or any
   speculation about who holds the car.
4. If the registrant replies asking us to "check" with the poster, decline and
   repeat the official route. Offer to help them draft the police report.
5. Log the relay (date, identifier, channel) in `data/impact.json` under
   `leads_relayed`; log a confirmed recovery only when the owner confirms it.

## Known scam patterns in the corpus

* Fake "recovery committees" and "search platforms" that ask for the chassis
  number over WhatsApp, then demand fees or block the victim.
* Government yard signage reused as bait with a private WhatsApp number stamped
  on it.
* "Your car is rotting in the rain" urgency to pull people off-platform.
* The same operation under several names and rotating numbers: judge by the
  technique, not the number.

## Reporting a concern

If you believe a record on the site is being used to target someone, or a
message claiming to be from us asks for money or a chassis number, write to the
contact on the support page. We remove the record and publish a warning.
