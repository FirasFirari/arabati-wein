# Data responsibility note for the open dataset

*Written 2026-11-14 for the first release. It follows the questions asked by
the IASC Operational Guidance on Data Responsibility in Humanitarian Action and
the OCHA Data Responsibility Guidelines. It is a self-assessment by a volunteer
project, not a certification by either body.*

## 1. What is being shared, and why

Six tables of counts drawn from the Arabati Wein registry: vehicles by state,
by status, by make, posts collected by month, a quality table for the location
mapping, and headline figures.

Purpose: let people who work on the Sudan response see what the public
recovery lists contain (how many distinct vehicles, where they are reported,
in what state of recovery) without sharing anything that identifies a vehicle
or a person. The registry itself stays where it is: a search box that answers
one number at a time.

## 2. What the underlying data contains

| held in the database | sensitivity | in this dataset? |
|---|---|---|
| Chassis and plate numbers | identifies one vehicle and, through registration records, its owner | no |
| Contact numbers written in posts | personal data of the poster | no |
| Post text, links, photos | may name people and places | no |
| OCR text of photographed registration cards | owner name, address, phone | no, and never exported anywhere |
| Standing-watch registrations (identifier, contact) | personal data given in confidence | no |
| Free-text location | usually a city or neighbourhood, sometimes a house or an office | only as a count per state |
| Make, status | not personal | as counts |

## 3. Risks considered

**Re-identification of a vehicle or owner.** A count cannot be searched for a
chassis. The residual route is a small cell: "one vehicle reported in state X"
combined with outside knowledge. Mitigation: geography stops at the state, no
table crosses two attributes, and any non-zero count below 5 is withheld.

**Harm to people holding or selling vehicles.** A fine-grained map of where
looted vehicles are reported could direct attention, or violence, at a place.
Mitigation: state level only. No locality, neighbourhood, yard or office is
published here, although the database holds some.

**Harm to searching families.** Extortion of people looking for a car is the
main documented harm in this corpus (`docs/SAFEGUARDING.md`). This dataset
gives an extortionist nothing: it holds no identifier and no contact.

**Misreading.** The most likely harm is a wrong conclusion: reading the state
table as "where cars were looted" or a zero as "no looting". Mitigation: the
caveats in `README.md` and the mapping-quality table are published beside the
figures, and the map carries the same warning in its title block.

**Source sensitivity.** The posts are public, but group administrators did not
publish them for analysis. No group, page or channel is named or counted
individually here.

## 4. Classification

Public. Aggregate, non-personal, no cell under 5. The registry's row-level data
is not public as a dataset and is not offered for bulk download.

## 5. Controls in the build

* `scripts/build_open_dataset.py` reads the database read-only and can only
  emit counts; the suppression threshold is a constant in that file.
* `carhunt/geo.py` maps a location to a state or to an explicit "not mapped"
  class. It never emits the location text.
* The repository's pre-commit and CI check (`scripts/check_no_secrets.py`)
  refuses phone numbers and the operator's own identifiers in any tracked file.
* Every release is a full rebuild; there is no hand editing.

## 6. Retention, correction, withdrawal

Each build replaces the last. Anyone can ask for a correction or raise a
concern at info@mycarsd.online; the answer comes within 7 days, as for the
registry (`docs/PRIVACY.md`).

The dataset will be withdrawn, or a table dropped, if:

* a way is shown to recover an identifier or a person from it;
* a partner in Sudan reports that the state table is being used to target a
  place or a group;
* the sources narrow so far that a state's count describes one list by one
  committee.

## 7. Who decides

The project operator decides on release and withdrawal. There is no data
sharing agreement behind this dataset because nothing but public aggregates is
shared. A request for row-level data from a humanitarian or legal body would
need a written agreement and is not covered by this note.
