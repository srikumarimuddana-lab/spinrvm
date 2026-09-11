# Change Impact & Risk Log — A34 broader pre-launch data-contamination check

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-10 |
| Author | Claude Code (spinr platform) |
| Surface(s) | backend (data only — no code changed) |
| Domain (Sentry tag) | admin |
| PR / commit link | this doc + `ACTION_ITEMS.md` A34 addendum |
| Related issue or gap ID | A34 — "Broader pre-launch question raised 2026-08-30, investigation pending" (`docs/migration/2026-08-27-legacy-data-full-migration-approach.md` Phase 6) |

**No code or data was changed by this investigation.** Every number below comes from
read-only `SELECT` queries against the live production Supabase project
(`spinrmobileapp`, `soavhtdhefowwvforzwb`), run directly in this session.

## 1. Issue / gap identified

Phase 6 of the migration-approach doc (2026-08-30) flagged an open question and never
resolved it: the owner-confirmed 2026-03-30 launch-date cutoff applies to the wallet
importer, but "plausibly applies to every other legacy importer used in this migration
effort ... some already merged and run against production. Whether any already-imported
record traces back to pre-launch legacy data, and what (if anything) should be cleaned
up before go-live, is a separate investigation — not yet done as of this edit."

This closes that investigation for the SIN/DOB backfill, vehicle-history backfill,
saved-address backfill, and the pre-launch-flagging tool itself. **It does not perform
any cleanup** — that decision is explicitly left to the product owner/privacy officer,
per this repo's established pattern for PII-retention calls.

## 2. Root cause / what was actually checked

Four live queries, in order:

1. **Has the pre-launch-flagging tool (#16, `pre_launch_flag_service.py`) actually run
   against production?** Yes — fully, and currently complete. `drivers` with
   `legacy_import_metadata->>'pre_launch_test' = 'true'`: **854**. Recomputing the tool's
   own dormant-candidate logic live (legacy-imported AND zero rides ever AND zero
   `driver_insurance_periods` rows ever, out of 924 total legacy-imported drivers) gives
   the same **854**, with **0** dormant-but-unflagged remaining. `rides` flagged
   `pre_launch_test = true`: **25**, matching **25/25** of all rides with
   `created_at < 2026-03-30` exactly. **This corrects the migration-approach doc's
   "not yet done" framing** — for the two tables this tool covers, the gap is closed and
   current as of today, not open.

2. **Of those 854 confirmed-dormant (zero real activity, ever) driver profiles, how much
   real PII has other backfill tools already written onto them?**
   - `sin` (encrypted, migration-19-era SIN import) populated: **97**
   - `date_of_birth` populated: **146**
   - `driver_vehicle_history` (VIN/insurance/registration, migration 157) — at least one
     row: **241**

   The SIN/DOB backfill (tool #4) and vehicle-history backfill (tool #5) match rows by
   phone/driver-row existence only — neither has an activity or launch-date gate, unlike
   the pre-launch-flagging tool built specifically because that gap mattered for drivers.
   Net effect: real government ID numbers and vehicle records sit on record today for
   driver profiles Spinr's own tooling has independently determined have zero
   operational footprint.

3. **Does the same pattern exist for riders?** Yes, and there's no flag for it at all.
   `pre_launch_flag_service.py` only writes `drivers` and `rides` — `users` (riders) has
   no equivalent. Of 1,132 legacy-imported rider accounts (`legacy_import_metadata
   ->>'rider_csv_import' IS NOT NULL`):
   - Only **5** carry a pre-launch `created_at` — riders' import doesn't preserve the
     original old-app signup date the way drivers' does, so date is not a useful proxy
     here either, for the same reason the driver tool's own docstring already rejected
     date-gating in favor of an activity gate.
   - Using that same zero-activity proxy instead: **1,046 of 1,132 (92%)** have never
     taken a single ride in Spinr.
   - Of those 1,046 dormant riders, **153** already have `saved_addresses` rows (home/
     work/favorite-address backfill, tool #10) attached.

4. **Is the insurance-period reconstruction (migration 332) silently contaminated with
   test-trip audit rows?** All 25 pre-launch-flagged rides are among the 246 rides
   covered by migration 332's reconstruction (492 Period 2/3 rows total) — so yes, SGI
   audit-trail rows exist for confirmed pre-launch test trips. **This is not a silent or
   untraceable gap**, though: migration 332 already stamps `is_reconstructed = true` on
   every row it writes, and these rides are independently marked `pre_launch_test =
   true`. Both flags together mean an SGI audit pull can identify and exclude this
   population today. Recorded here for completeness, not as a new action item.

## 3. Fix / remediation

None performed. This is a findings report only. See "Recommendation" below for the
decision this surfaces.

## 4. Risk & impact on existing functionality

- **Blast radius of the investigation itself: zero** — every query was a `SELECT`, no
  `UPDATE`/`INSERT`/`DELETE` was run.
- **Risk being reported (not created by this check):** SIN is the single most sensitive
  field this codebase stores (`CLAUDE.md`'s PIPEDA section lists "Government IDs, SIN,
  driver license numbers" as never-log/most-sensitive). 97 dormant/test driver profiles
  carry it today with no operational purpose being served — a data-minimization gap
  under PIPEDA's "collect only what is needed to provide the ride" principle, not a
  breach (no unauthorized access or exposure occurred; this is retention hygiene, not an
  incident).
- No interaction with the ride state machine, money/wallet paths, or any live user
  session — none of the flagged accounts have ever taken a ride or gone online.

## 5. User-experience effect

None. No rider, driver, corporate-admin, or internal-admin-facing behavior changed.
The 854 dormant driver profiles and 1,046 dormant rider profiles identified are not
signed into or otherwise interacting with the app (that's the definition used to find
them).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `docs/change-log/2026-09-10-a34-pre-launch-data-contamination-check.md` | New — this file | Record the investigation and its findings |
| `ACTION_ITEMS.md` | A34 addendum | Surface the findings where the open question was tracked |

## 7. Before / after

Not applicable — no behavior-changing diff.

## 8. Rollback plan

Not applicable — no code or data changed.

## 9. Verification performed

- [x] Ran directly against live production Supabase (`spinrmobileapp`) via the Supabase
  MCP connector — not mocked, not inferred from prior docs.
- [x] Recomputed `pre_launch_flag_service.py`'s own dormant-candidate SQL logic
  independently (from reading the service module) rather than trusting its docstring's
  claimed 854 in isolation — the live recomputation matched exactly.
- [x] Reviewed against CLAUDE.md's PIPEDA data-minimization and never-log-PII
  conventions.
- [ ] No code test run — no code was changed.

## What was NOT verified

- Whether the 97/146/241 SIN/DOB/vehicle-history rows on dormant driver profiles were
  written by the SIN/DOB and vehicle-history backfill tools specifically, versus some
  other path (e.g. manual admin entry) — the population overlap is confirmed live; the
  *writer* of each individual row was not traced row-by-row (would need each row's own
  audit/updated_at history, not attempted here).
- Whether any of the 153 dormant riders with saved-address rows, or the 97/146/241
  dormant drivers with SIN/DOB/vehicle-history, have since become active (this is a
  point-in-time snapshot as of 2026-09-10; a rider or driver could return and go active
  the same day).
- No legal/privacy-officer judgment is offered here on whether this rises to a
  reportable retention gap — that determination is explicitly left to the product
  owner, consistent with every other PII-scope decision recorded elsewhere in A34.

## 10. Sign-off

- [x] Blast radius is stated, not assumed (read-only investigation, zero writes)
- [x] No silent behavior change — nothing changed
- [ ] Rollback plan — not applicable, no change made
