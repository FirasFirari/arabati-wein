# Reading 199,000 Arabic posts without inventing a number

*How Arabati Wein extracts vehicle identifiers from Arabic social media posts
and photos with language models, at no model cost, and what stops a model's
invention from reaching a family. Figures are from the database on
2026-11-14 unless a date is given.*

## Summary

Families searching for a car looted in the Sudan war hold one fact that
matters: a chassis number. The lists that might contain it are scattered over
Facebook groups and Telegram channels as typed text and photographed pages.
We collected 199,117 public posts and 258,910 photos and turned them into a
registry of 106,213 distinct vehicles that anyone can search for free.

A wrong number in this registry is worse than a missing one. It sends a family
to a yard, or to a stranger, for a car that is not theirs. So the system is
built on one rule: **a language model may read, and it may never be believed.**
Every identifier is checked mechanically against the source before it is
stored. That check has caught 106 invented identifiers. The models that
invented them reported none.

The extraction lane that runs today costs nothing. The price of that is speed:
136,422 posts are still waiting.

## 1. The failure that shaped everything

Early on, model output went straight into the database. Three kinds of error
came back, all found later by hand:

* whole vehicles that were in no post, with a make, a model and a chassis;
* a real phone number attached to 117 vehicles, carried over from the
  neighbouring post by the same author;
* a chassis with one digit dropped.

None of these looks wrong. Each is a plausible number in a plausible row. And
when asked, the model that produced them described its work as complete and
correct. Models were often right about doubts in their own method. They never
once flagged their own invented data.

## 2. The grounding gate

The fix is small and has no model in it (`carhunt/gates.py`). A chassis, plate,
contact or price is stored only if its digits appear in the post's own text or
in the OCR of its photos. Anything else is set to empty.

Two details decide whether the gate works:

**Compare against whole numbers.** The first version joined every digit in the
post into one string and looked for the value inside it. On a list of 20,000
characters that string contains almost any short number somewhere: it accepted
52% of invented four-digit values. The gate now requires a value to sit inside
one number as written in the source.

**Apply it on every path.** For more than two months the gate ran only on
the lane that had needed it first. When it was finally applied everywhere, one photo lane turned
out to hold 8.6% of chassis and 27% of plates that were in neither the post nor
its OCR. The gate now sits inside the one function every write goes through.

A value read from a photo that has not been transcribed yet cannot be checked.
It is kept, because it may be the only record of a car, and it is published
with a visible low-confidence reason.

## 3. Counting what was lost

Invention is one way to be wrong. Silence is the other, and it is harder to
see because every dashboard stays green.

* A model given a list of 684 vehicles returned 401 and stopped. The run was
  scored one post in, one post out: complete.
* An API tier cut long outputs at 16,000 tokens. About 130,000 records never
  arrived, and per-item completion showed a clean pass.
* A result file in a slightly different shape was rejected line by line while
  the summary read "ingested 10 files". 3,277 vehicles were dropped behind it.

Three checks now run on every batch. **Coverage:** every post sent must come
back, or nothing is written. **Shortfall:** vehicles returned are compared
with the count of chassis-shaped numbers in the source. **Reconcile:** rows the
lane claims are compared with rows actually in the database. The last one has
caught the most. Verify the records, and treat the completion metric as a
claim.

## 4. Taking the model out where it is weakest

Measured in August 2026: 159 long recovery lists were 1.5% of the posts and
held about 44% of all chassis-shaped numbers. They were also where every model
failed. One ran for 24 minutes on a 953-vehicle list and returned nothing.

These lists are regular enough to parse (`carhunt/kashf.py`). The parser copies
digit runs out of the line or emits nothing, so it cannot invent, and it has no
output limit, so it cannot truncate. Against a frozen answer key it did not
write, it recovers 99.30% of 64,624 chassis across 750 list posts
(`tests/test_kashf_gold.py`). It costs nothing to run.

What it took to get there is in the tests: several vehicles on one line; three
ways of writing a Sudanese plate, where dropping one "optional" digit from the
pattern cut recall by four points; model years and list serial numbers read as
chassis; and a phone filter that had to be narrower than the nine-digit chassis
it protects. Anything the parser cannot read with confidence goes to a model.
Identifiers come from the parser. Judgement stays with the model.

## 5. Photos

A VIN stamped on a compliance plate is invisible to text search, and many
lists exist only as photographed handwriting. Photos are transcribed once into
a database-only field (a registration card carries the owner's name and
address, so OCR text is never published).

* **Six photos per call.** At ten, a photo was given its neighbour's VIN. At
  twenty, 20 of 80 photos carried a neighbour's number. Recall did not move
  between these settings. Only a leakage test showed the difference.
* **Hash before reading.** 75% of the photo backlog was byte-identical copies
  of images already seen. Each distinct image is read once.
* **Second read for VINs.** VIN-like photos get a second reading from a
  different model, riskiest first by check digit. When the two disagree, both
  are kept and the row is marked.

## 6. Identifiers decide. Scores do not.

Models were asked to score how likely a post was to match a given car. One
scored four posts at 95 out of 100 on wheels, door handles and "similar
damage". The chassis numbers in those posts were unrelated to the target's.

A white 2015 Corolla looks like every other one, and moving a chassis plate
between cars is the standard way to launder a stolen vehicle. So an alert
needs an exact identifier match, made by deterministic code, token by token. A
model score with no identifier behind it is capped below the alert line. The
matching rule itself is treated as the most dangerous code in the project:
a version that accepted any leading digit where one specific digit belonged
produced 27 confident false alerts from a single reposted list.

## 7. Cost

In September 2026 a paid batch lane spent USD 646 gross in one day. The cause
was a guessed unit price in our own cost table: the meter read about 60 while
the invoice read 646, so the budget stop never fired. Output tokens were three
quarters of the bill.

The lane that replaced it uses, in order: free deterministic checks that
settle a post with no model; the list parser; and a free API tier for the
prose that remains, at roughly 800 posts a day. Model spend is zero. The
backlog is the cost, and it is published on the stats page with every other
figure.

## 8. What this does not show

* No recovery has been confirmed yet. Two leads have been verified by a person
  and passed to registrants.
* Make, status and location are read by a parser or a model and are not
  individually checked. Only identifiers are gated.
* Recall on prose posts is measured on small benchmarks, not on a frozen key
  the way the list parser is.
* The collection follows the groups we could read. It is not a census.

## Where to look

| | |
|---|---|
| The gate | `carhunt/gates.py`, `tests/test_gates.py`, `tests/test_grounding.py` |
| The list parser | `carhunt/kashf.py`, `tests/test_kashf.py` |
| Matching rules | `carhunt/normalize.py`, `carhunt/watches.py`, `tests/test_identifier_rule.py` |
| Architecture | `docs/ARCHITECTURE.md` |
| Open aggregate data | `open-data/` |
| Live figures | https://mycarsd.online/stats.html |
