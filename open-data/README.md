# Open data: vehicles reported looted or lost in the Sudan war (aggregates)

Counts from the Arabati Wein registry (https://mycarsd.online), published so
that responders, researchers and journalists can see the shape of what the
recovery lists hold without anyone handing out a single chassis number.

**Aggregates only.** No chassis, plate, phone number, post text, photo, link
or watch registration is in these files. To look up one vehicle, use the
search on the site.

| | |
|---|---|
| Source | Public posts in Sudanese vehicle-recovery groups, pages and channels on Facebook and Telegram, collected since 22 August 2026 |
| Coverage | Sudan. Vehicles that someone posted about. Not a count of vehicles looted. |
| Build | See `build.json` for the date of this build. Rebuilt with `python scripts/build_open_dataset.py` |
| Format | CSV, UTF-8, with an [HXL](https://hxlstandard.org) hashtag row under the header |
| Geography | State (admin 1) P-codes from the OCHA [Sudan administrative boundaries](https://data.humdata.org/dataset/cod-ab-sdn), version v03 |
| Licence | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Credit "Arabati Wein, mycarsd.online". |
| Contact | info@mycarsd.online |

## Files

| file | one row is | columns |
|---|---|---|
| `registry_summary.csv` | one headline figure | indicator, value, definition |
| `vehicles_by_admin1.csv` | one of Sudan's 19 states | P-code, English and Arabic name, vehicles |
| `location_mapping_quality.csv` | one outcome of the location mapping | class, meaning, vehicles |
| `vehicles_by_status.csv` | one status | status, meaning, vehicles |
| `vehicles_by_make.csv` | one make or body type | make, kind, vehicles |
| `posts_by_month_platform.csv` | one month on one platform | month collected, platform, posts |

`dashboard.html` is a self-contained page drawn from these files
(`scripts/build_open_dashboard.py`). `map/sdn_admin1_simplified.geojson` is the
OCHA state layer simplified for that page; use the original for analysis.
`kobo/watch_intake.xlsx` is the standing-watch registration as an XLSForm for
KoboToolbox or ODK, in Arabic and English, built by `scripts/build_kobo_form.py`.

## What a "vehicle" is

One distinct normalised chassis number. A row with no chassis counts by its
plate. Rows with neither cannot be told apart and are not counted. The same
list is reposted to dozens of groups, so the registry holds about fourteen
sighting rows per vehicle; every count here is of distinct vehicles, never of
rows.

## How locations become P-codes

A post says where a vehicle was seen in the poster's own words. Each string is
matched against a short gazetteer of states, capitals, localities and large
neighbourhoods (`carhunt/geo.py`, tests in `tests/test_geo.py`). The mapper
does not guess:

* a string naming places in two states is counted as `multiple`;
* "Kordofan" with no state is counted as `region_only`, because three states
  share the name;
* a string naming no place the gazetteer knows is `unmapped`.

`location_mapping_quality.csv` reports how many vehicles fall in each class.
Read it before reading the map.

## Caveats. Read these before using a number.

1. **Most vehicles have no location.** Only about one vehicle in eight has a
   location in any post about it, and about one in twelve maps to a single
   state. The state table describes that subset. It says nothing about the rest.
2. **Location is where a vehicle was reported**, found, held or seen. It is
   not where it was taken from.
3. **Coverage follows the sources.** The groups collected are concentrated on
   Khartoum state and on the Kordofan recovery committees. A state with a zero
   is a state these sources did not post about. It is not a state without
   looted vehicles.
4. **Dates are collection dates.** `posts_by_month_platform.csv` shows when a
   post was collected, starting 22 August 2026, not when a vehicle was taken
   or found. Collection volume changes with how the collector was run.
5. **Statuses overlap.** A vehicle listed as found in one post and for sale in
   another is counted under both, so the status column does not sum to the
   number of vehicles.
6. **Extraction is automated.** Identifiers pass a grounding gate (a number
   must appear in the post or its photo OCR), but make, status and location
   are read by a parser or a language model and are not individually checked.
   See `docs/METHOD.md`.
7. **Small cells are withheld.** A non-zero count below 5 is published as
   `1-4` in the note column with the number left empty.
8. **Make is often missing.** Recovery lists frequently give a model name or
   only a number. `kind` separates real makes from rows that record only a
   body type (pickup, rickshaw, bus).

## Using it

Join `vehicles_by_admin1.csv` to the boundary layer on `adm1_pcode`. In QGIS:
add `sdn_admin1.geojson`, add the CSV as a delimited text layer starting at
line 3 or with the HXL row filtered out, and join on the P-code. In Power BI
or Excel: load the CSV and remove the second row (the hashtags).

## Data responsibility

How this dataset was assessed before publication, and what would cause it to
be withdrawn: `DATA_RESPONSIBILITY.md`.
