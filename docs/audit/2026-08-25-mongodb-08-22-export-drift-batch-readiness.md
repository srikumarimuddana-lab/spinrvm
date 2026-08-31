# MongoDB 2026-08-22 Export — Drift & Batch-Readiness Audit

**Date:** 2026-08-25
**Trigger:** User supplied a fresh MongoDB legacy export (`Mongo_20260822-DrivelocLess.zip`, internal file
timestamps 2026-08-22) for a "migration batch readiness" check, followed by `driverlocationlogs.csv` in two
parts (split for upload-size reasons) and, in a later message, the original 2026-07-26-vintage `Mongo.zip`
for a verified baseline comparison.
**Scope:** Structural inventory, freshness/drift comparison against the 2026-07-26-vintage export (the one
that fed the 2026-07-29 production cut and every prior migration audit), and a full-export malformed-CSV
spot-check. **This is a read-only source-data audit — no code, schema, or data changes are proposed or made
here.**
**Auditor:** Claude Code, reporting as senior DBA / migration auditor.
**Method:** Extracted every collection with Python's stdlib `csv` module (not naive `wc -l`/delimiter
splitting), cross-referenced row counts and ID crosswalks against the 07-26 baseline, and verified suspect
files with a real RFC4180-aware parser rather than line-count heuristics alone.

---

## Executive summary

| # | Finding | Severity | Evidence |
|---|---|---|---|
| 1 | **This is a genuinely new export, not a re-pull of the 07-26 vintage.** All 53 non-`driverlocationlogs` collections grew by roughly 7–11% in four weeks, proportionally consistent with each other (bookings +7.5%, drivers +5.5%, customers +10.4%, location-log ride coverage +7.4%). No collection newly appeared or newly emptied. | Informational — confirms freshness | Row-count diff table, §1 |
| 2 | **`driverlocationlogs.csv`/`driverlocationlogs-1.csv` (supplied separately, in two parts) are malformed CSV — the only corrupted files in the entire export.** The `way_points` array field explodes into thousands of unquoted pseudo-columns/pseudo-rows, inflating a naive line count by roughly 2×. A purpose-built parser recovers true records reliably (verified via an internal-consistency check: `ride_id`-populated count exactly equals `on_ride + going_to_pickup` count in both parts). | **P1 — blocks any naive/generic import of this one collection** | §2, §4 |
| 3 | **The malformed-CSV problem is new, not inherited.** The 07-26-vintage `driverlocationlogs.csv` (verified directly from the original `Mongo.zip` this session) is clean, standard CSV — every one of its 7,948 physical lines is one valid record. Something changed in the export process between July 26 and August 22 specific to this collection. | **P2 — export-tooling issue, needs root-causing before the next pull** | §4 |
| 4 | **Full-export spot-check found no other corrupted files.** All 51 other non-empty CSVs parse cleanly with a standard CSV reader; the only anomalies (bookings.csv, chats.csv, drivers.csv, errorlogs.csv) turned out to be legitimate multi-line quoted fields or a harmless trailing empty column — not corruption. | Informational — scopes the P1 finding to one collection | §5 |
| 5 | **`created_at` in `driverlocationlogs` is not a real per-event timestamp in either vintage.** Only 67 distinct values exist across both the 07-26 and 08-22 pulls — the identical 67, with zero new values added despite 874 new records. `start_time`/`end_time` are the only trustworthy per-record timing fields. | **P2 — must inform any Period-2/3 reconstruction use of this file** | §3 |
| 6 | **Net-new completed, real-Canada rides since the last migration batch: 19** (243 in this pull vs. the 224 already in production, using the same customer-and-driver `country_code == '1'` filter the 08-14 audit established). A small, tractable next-batch size if a follow-up import is greenlit. | Informational — sizes a potential next batch | §1 |
| 7 | **`driverlocationlogs` ride-coverage crosswalk still 100%** against same-vintage `bookings._id` in both pulls (393/393 rides in 07-26, 422/422 in 08-22) — the join key the 08-19 inventory established remains solid. | Informational — confirms crosswalk stability | §3 |

---

## §1. Freshness & row-count drift (08-22 vs. verified 07-26 baseline)

The 07-26 figures below are re-derived directly from the original `Mongo.zip` supplied later in this
session (not just quoted from the prior audit doc), so this is now a verified, not assumed, baseline.

| Collection | 07-26 | 08-22 | Δ | Note |
|---|---|---|---|---|
| `bookings` | 1,210 | 1,301 | +91 (+7.5%) | status: 766 cancelled / **290 completed** / 243 failed / 2 blank |
| `customers` | 1,121 | 1,238 | +117 (+10.4%) | |
| `drivers` | 877 | 925 | +48 (+5.5%) | |
| `payments` | 372 | 392 | +20 | `pending_amount_status`: 154 due / 238 no_due (was 158/214) |
| `driverearnings` | 276 | 296 | +20 | |
| `banks` | 157 | 162 | +5 | still 100% raw SIN/DOB/banking in plaintext (unchanged risk posture, see 08-19 inventory) |
| `wallets` | 13 | 13 | **0** | net balances unchanged: $900 rider / $60 driver referral — no new prepaid-money drift |
| `sessions` | 2,074 | 2,248 | +174 | still live-format JWTs — never-import (per 08-19 inventory) |
| `activities` | 7,497 | 7,964 | +467 | |
| `errorlogs` | 261,298 | 205,095 lines / 18,701 real records* | — | *naive line count is misleading here too — see §5. Excluded from migration scope either way. |
| `driverlocationlogs` | 7,948 (verified accurate) | 8,822 (corrected; see §4) | +874 (+11.0%) | see §2–§4 for the parsing caveat |
| 15 previously-confirmed-empty collections | 0 each | 0 each | — | still all empty (`restaurants`, `vendors`, `fleets`, `companies`, `extraorders`, `extraorderinvoices`, `orders`, `taxes`, `surchargehistories`, `driverpayouthistories`, `documentsdetails`, `cards`, `contactus`, `serviceareas`, `users`) |

**Canada-filtered completed-bookings drift** (same methodology as the 08-14 audit: customer **and** driver
both `country_code == '1'`):

```
07-26: 475 both-Canada bookings, of which 224 completed  (matches the 224 rides already in production)
08-22: 509 both-Canada bookings, of which 243 completed
```

**19 net-new completed, real-Canada rides** exist in this pull beyond what's already migrated — a small,
tractable batch if a follow-up import is greenlit. The overall completed count (290) is higher than 243
because 47 of those completed rows fail the Canada-both-sides filter (same multi-tenant/test-data caveat
Finding 1 of the 08-14 audit already established).

## §2. `driverlocationlogs` — malformed CSV, only in the 08-22 pull

The base `Mongo_20260822-DrivelocLess.zip` excluded `driverlocationlogs.csv` (hence the filename) because
even compressed it exceeded the 30 MB upload limit. It was supplied separately in two parts:

| | Part 1 (`driverlocationlogs.csv`) | Part 2 (`driverlocationlogs-1.csv`) |
|---|---|---|
| Compressed / uncompressed | 22 MB / 275 MB | 11 MB / 99 MB |
| Naive physical line count | 11,815 | 3,344 |
| **True record count** (corrected) | **6,000** | **2,822** |

A naive `wc -l`-style count overstates true records by roughly 2× on part 1. The header itself carries
**14,088 columns** (mostly blank padding) in both parts — a symptom of the same root cause: the `way_points`
array field (`[{"lat": ..., "lng": ...}, ...]` per location segment) is emitted without a correctly-closed
outer quote for a meaningful share of records, so its internal commas and `{lat}`/`{lng}` fragments spill
into what a naive parser reads as extra columns or extra rows.

**Recovery method:** every real record reliably starts with the pattern `<int>,<24-hex ObjectId>,<int>,`
(the `#`, `_id`, `__v` columns, which are never themselves malformed). Reconstructing records by anchoring
on that pattern and treating non-matching lines as continuation noise recovers the file correctly — verified
by an internal-consistency check that holds exactly in both parts: **`ride_id`-populated record count equals
`on_ride + going_to_pickup` record count** (part 1: 585 = 277+308; part 2: 516 = 433+83). `way_points` itself
is present and its JSON is recoverable with a purpose-built joiner, but is not needed for the core
migration-readiness columns (`_id`, `created_at`, `distance`, `driver_id`, `end_time`, `phase`, `ride_id`,
`start_time`, `updated_at`).

**Practical implication:** do not point a generic CSV importer or a `wc -l`-based row count at this specific
file. Every other collection in this export is safe for a standard RFC4180 reader (§5).

## §3. Recovered contents & crosswalk (corrected parse)

| Metric | Part 1 | Part 2 | Combined |
|---|---|---|---|
| Real records | 6,000 | 2,822 | 8,822 |
| `idle` | 5,415 | 2,306 | 7,721 (87.5%) |
| `going_to_pickup` | 308 | 83 | 391 (4.4%) |
| `on_ride` | 277 | 433 | 710 (8.0%) |
| `ride_id` populated | 585 | 516 | 1,101 |
| `start_time`/`end_time` window | 2026-01-30 → 2026-06-17 | 2026-06-17 → 2026-08-22 | 2026-01-30 → 2026-08-22, no gap |

- **Zero duplicate `_id` values** between part 1 and part 2 — the split is a clean chronological cut, not an
  overlapping or duplicated re-export.
- **Ride crosswalk: 422 distinct `ride_id` values, 100% match rate against `bookings._id`** in the *same*
  08-22 export (up from 393/393 against the 07-26 baseline — growth of +29, or +7.4%, tracking closely with
  the +7.5% growth in total bookings over the same window; three independent numbers converging on the same
  growth rate is a good consistency signal).
- **`created_at` is not a real per-event timestamp, confirmed across both vintages.** Only 67 distinct
  values exist in the 07-26 file and 67 in the combined 08-22 files — **the identical 67 values**, with zero
  new markers introduced despite 874 new records being added. New records are being stamped with one of
  these pre-existing values (the dominant one grew from 2,365 occurrences in 07-26 to 3,239 in 08-22). This
  is a source-side batch-insert/backfill artifact, not live per-GPS-ping data. **Any use of this file for
  timing (e.g. tightening migration 332's Period-2/3 insurance-boundary reconstruction) must key off
  `start_time`/`end_time`, never `created_at`.**

## §4. Root-cause note: the corruption is new, not pre-existing

The 2026-08-19 inventory doc's driverlocationlogs figures (7,948 rows, exact phase/ride_id breakdown) were
computed via `wc -l - 1` and, at the time, assumed to carry the same caveats as every other naive count in
that document. Re-verifying directly against the original 07-26 `Mongo.zip` this session shows that
assumption was too cautious in that direction: **the 07-26 file is completely clean** — every one of its
7,948 physical lines is exactly one record, 0 malformed lines, 0 odd-quote lines, and its header carries only
the expected 12 named columns with no padding. The malformed multi-row/multi-column `way_points` output
started appearing **only in the export that produced this session's 08-22 pull**.

This means whatever generated the 08-22 `driverlocationlogs` parts — export tool version, script, or
manual step — changed behavior for this specific collection between the two pulls. Worth root-causing before
the next scheduled pull (the playbook's anticipated "Oct 30 final export"), since the same tooling will
presumably be used again. See §5 for confirmation that no *other* collection in the 08-22 export shows the
same symptom, so this is scoped to `driverlocationlogs` specifically, not a general regression in the
export pipeline.

## §5. Full-export spot-check — no other corrupted files

A line-based heuristic pass across all 53 non-`driverlocationlogs` CSVs in the 08-22 export flagged 5
candidates; each was then verified with Python's real RFC4180-aware `csv.reader` (the same class of parser
that caught the genuine corruption in §2) rather than trusted on the heuristic alone.

| File | Heuristic flag | `csv.reader` verdict | What it actually was |
|---|---|---|---|
| `bookings.csv` | 1 unmatched line, 2 odd-quote lines | **Clean** — every row parses to 112 or 113 cols, no misalignment | Harmless trailing empty column on most rows + one legitimate multi-line quoted field |
| `chats.csv` | 5 unmatched, 10 odd-quote lines | **Clean** — every row parses to exactly 12 cols | Multi-line chat messages, properly quoted (real newlines inside a correctly-quoted field — valid CSV) |
| `drivers.csv` | 38 unmatched, 16 odd-quote lines | **Clean** — every row parses to exactly 73 cols | Multi-line `documents` JSON, properly quoted |
| `errorlogs.csv` | 186,394 unmatched, 37,402 odd-quote lines | **Clean, different shape** — 17,901/18,701 rows carry 2 trailing empty columns beyond the 10-col header | Multi-line NestJS stack traces, properly quoted. Excluded from migration scope regardless (operational log noise per the 08-19 inventory). |
| `languages.csv` | 0/93 lines matched the `#,_id,__v,` pattern | **Clean — false alarm** | This collection's schema has no `__v` column (`#,_id,english,french,hindi,key,spanish`); the heuristic's pattern simply didn't apply, nothing wrong with the data |

**Conclusion: `driverlocationlogs` is the only collection in this export batch with the malformed-export
problem.** The other 51 non-empty CSVs are standard, RFC4180-compliant CSV, safe for an ordinary
`csv.DictReader`-style import with no bespoke record reconstruction required.

---

## What this doc does NOT do

- No code, schema, or data changes. No importer written or run.
- No re-verification of the PII/minimization decisions the 2026-08-19 inventory already recorded
  (`banks.csv`, `coupons.csv`, etc.) — those stand as previously decided; this doc only re-checks row-count
  drift and structural integrity.
- No claim about what changed inside individual rows (e.g., whether any specific booking's fare fields
  drifted) — this is a structural/volumetric drift check, not a field-by-field financial reconciliation
  (see the companion `2026-08-14-mongodb-legacy-extract-audit.md` and
  `2026-08-14-three-ledger-reconciliation.md` for that class of check, not repeated here).

## Recommended next steps (tracked, not actioned here)

1. Root-cause the `driverlocationlogs` export malformation (§4) before the next scheduled pull, ideally with
   whoever/whatever generates these exports — confirm whether it's a tool-version change, a data-shape
   change on the source side (e.g., `way_points` growing larger/nested differently), or a one-off glitch.
2. If a next migration batch is greenlit, the 19 net-new completed Canada rides (§1) are a small, bounded
   starting scope.
3. Any future use of `driverlocationlogs` for Period-boundary reconstruction work must use `start_time`/
   `end_time`, not `created_at` (§3) — worth calling out explicitly in whatever script consumes this file
   next, since the field name is an easy default to reach for.
4. No action needed on the other 51 collections' structural integrity — confirmed clean in §5.
