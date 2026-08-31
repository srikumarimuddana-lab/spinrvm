# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-30 |
| Author | Claude Code (session), on behalf of vikas@ngitservices.com |
| Surface(s) | backend, admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | branch `claude/migration-batch-readiness-wicr1d` |
| Related issue or gap ID | Phase 4 of `docs/migration/2026-08-27-legacy-data-full-migration-approach.md` §4 |

## 1. Issue / gap identified

301 rider saved-address rows exist in the legacy Mongo export (`customer_addresses.csv`) with no import path into Spinr. The migration plan doc's own Phase 4 entry claimed "no current Spinr table/UI concept maps 1:1" — that claim was checked directly against the schema before starting this work and found to be **wrong**: Spinr already has a live, self-serve `saved_addresses` table (`routes/addresses.py`) that's exactly the right destination.

## 2. Root cause

The plan doc's Phase 4 assessment was written without checking the actual backend schema. No code gap existed beyond "nobody built the importer yet" — the destination table, its shape, and its RLS posture all already existed.

## 3. Fix / remediation

**Data-quality investigation performed before writing any code** (not assumed):
- Of 301 raw rows, 20 are outside a Saskatchewan bounding box (19 explicitly `country=India`, 1 blank/no-address) — the same class of test/junk data already found and excluded from the rider CSV import earlier in this session. The remaining 281 are legitimate (207 with blank `country`/`state` but real in-province coordinates, 74 explicitly `country=Canada`).
- Found and corrected a real join-key gotcha before it became a bug: `customer_addresses.csv`'s own `customer_id` column is actually the legacy customer's Mongo `_id`, **not** the Stripe `customer_id` despite the shared column name — confirmed by cross-referencing the real export directly. An importer written against the wrong assumption would have silently matched zero rows.
- 278/281 legitimate rows resolve to a real phone via `customers.csv`; all 216 distinct resulting phones match an existing, already-migrated Spinr rider — confirmed via direct production SQL before writing the importer.

**Built:**
- Migration 373: additive `legacy_import_metadata JSONB NOT NULL DEFAULT '{}'` column on `saved_addresses`, matching the shape every other importer already uses on `users`/`drivers`/`rides`. Reviewed by `spinr-migration-reviewer`: ship as-is.
- `backend/services/saved_address_import_service.py`: `build_saved_address_import_plan`/`commit_saved_address_import_plan`, mirroring the established two-CSV-crosswalk pattern (SIN/DOB, vehicle-history). Filters to the Saskatchewan bounding box, requires a real matched rider, and is idempotent (skips an address already saved for that rider).
- `backend/routes/admin/legacy_saved_address_backfill.py`: validate/commit-token routes mirroring `legacy_vehicle_history_backfill.py`'s shape exactly, gated `require_module("users")` (same as the rider importer).
- Admin-dashboard page `dashboard/riders/legacy-saved-address-backfill` — dry-run-first Preview→Apply flow, same pattern as every other tool this session, linked from Bulk Operations.

**CSV field mapping** (the source columns don't map 1:1 onto `SavedAddress`): CSV `name` (the full formatted address text) → `SavedAddress.address`; CSV `type` (home/work/blank) → `SavedAddress.name`/`icon` (title-cased label + matching icon, default "Saved Address"/"location" for anything else).

## 4. Risk & impact on existing functionality

- **Blast radius: isolated.** `build_saved_address_import_plan`/`commit_saved_address_import_plan` have exactly one caller each (the new route). Grepped to confirm.
- **No existing row touched.** This is purely additive INSERTs into `saved_addresses`; no existing address, user, or driver row is read for mutation.
- **`place_id` is intentionally left `None`** for every backfilled row — no live geocode-verification call is made (the field is `Optional`, and the live create-endpoint's own comment already documents `None` as an accepted degraded state: "failed open (no API key, budget exhausted, no match)"). Avoids adding a Google Maps API dependency/cost to a background data-migration script.
- **Idempotent, safe to re-run**: keyed on `(user_id, address text)` — a second run of the same CSV inserts nothing new.
- **A genuine hard-error path exists and is tested**: missing required CSV columns refuses the whole commit (`plan.errors`), matching every other importer's contract — not previously present until this review, added specifically to give the route's existing "refuse to commit on errors" check real meaning.
- **Found, documented, and intentionally not silently fixed**: `saved_addresses` has RLS enabled but zero policies (deny-all for anon/authenticated, service-role bypasses — confirmed safe today, no anon-key path exists in either mobile app). Filed as `ACTION_ITEMS.md` B40 rather than bundled into this unrelated migration change.
- 168 tests pass across the affected/related test files (28 new: 20 service-layer + 8 route-level); `ruff check`/`format` clean; migration reviewed and approved by `spinr-migration-reviewer`.
- `npx tsc --noEmit` clean; `npm run build` succeeded — the new page compiled.

## 5. User-experience effect

- **Internal admin only**, and once backfilled, riders whose saved-address history gets imported will see their old home/work addresses appear in their own address book on next app load — visible, additive, and something the rider would recognize as their own data (not a new/unexpected feature). No mid-session change to anyone currently using the app; this only affects historical data an operator explicitly imports via the new admin tool.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/373_saved_addresses_legacy_import_metadata.sql` | New: additive `legacy_import_metadata` column on `saved_addresses` | Provenance tracking, matching every other importer's convention |
| `backend/services/saved_address_import_service.py` | New service file: plan/commit for the two-CSV backfill | Core import logic |
| `backend/routes/admin/legacy_saved_address_backfill.py` | New: validate/commit routes | Sanctioned production-write path (admin API, not raw SQL) |
| `backend/routes/admin/__init__.py` | Mount the new router under `require_module("users")` | Wire the route into the app |
| `backend/utils/rate_limiter.py` | New `legacy_saved_address_backfill_commit_limit` (10/hour) | Match every other backfill's commit-path rate limit |
| `backend/tests/test_saved_address_import_service.py` | New: 20 service-layer tests | Lock in filtering/matching/idempotency logic |
| `backend/tests/test_admin_legacy_saved_address_backfill.py` | New: 8 HTTP-level tests | Lock in the route's contract |
| `admin-dashboard/src/lib/api/imports.ts` | New client functions/types | Frontend client for the two new routes |
| `admin-dashboard/src/lib/api.ts` | Re-export the new symbols | Keep the barrel export in sync |
| `admin-dashboard/src/app/dashboard/riders/legacy-saved-address-backfill/page.tsx` | New admin-dashboard page | Preview → Apply UI |
| `admin-dashboard/src/app/dashboard/bulk-operations/page.tsx` | One link added to the intro paragraph | Make the new tool discoverable |
| `ACTION_ITEMS.md` | New B40 entry | Track the pre-existing `saved_addresses` RLS-policy gap found while building this, without bundling a fix into this change |

## 7. Before / after

Not applicable — every touched file except `bulk-operations/page.tsx` (one added link) is new. The one existing-behavior change:

```tsx
// Before
and{" "}
<Link href="/dashboard/drivers/legacy-vehicle-history-backfill" className="underline">
    vehicle-history
</Link>{" "}
backfills for drivers created there.
```

```tsx
// After
and{" "}
<Link href="/dashboard/drivers/legacy-vehicle-history-backfill" className="underline">
    vehicle-history
</Link>{" "}
backfills for drivers created there. Riders get a{" "}
<Link href="/dashboard/riders/legacy-saved-address-backfill" className="underline">
    saved-address
</Link>{" "}
backfill for riders created via Bulk Rider Import below.
```

## 8. Rollback plan

`git-revert-safe` for all code. Data written by the backfill (should the operator run it and later want to undo it) is identifiable via `saved_addresses.legacy_import_metadata->>'source' = 'legacy_customer_address_import'` for a targeted `DELETE` — no other table or field is touched by this importer, so a delete has no cascading side effects.

## 9. Verification performed

- [x] Investigated the real data before writing any import logic — confirmed the SK bounding-box split (281 legitimate / 20 junk), the `customer_id`-is-a-Mongo-`_id` join-key gotcha, and that all 216 resulting distinct phones match real, already-migrated Spinr riders — all via direct queries against the real export and production, not assumed.
- [x] `pytest tests/test_saved_address_import_service.py tests/test_admin_legacy_saved_address_backfill.py` plus the full set of related importer test files — 148 passed, 0 regressions.
- [x] `ruff check` / `ruff format --check` on every touched Python file — clean (43 pre-existing, unrelated repo-wide lint findings confirmed not touched by this change).
- [x] Migration reviewed by `spinr-migration-reviewer` subagent — ship-as-is verdict, numbering/idempotency/forward-compat/RLS-reasoning/rollback/index-need all confirmed correct.
- [x] `npx tsc --noEmit` — clean.
- [x] `npm run build` (admin-dashboard) — real production build, succeeded, new page compiled.
- [x] Blast-radius grep: every new service function has exactly one caller.

## What was NOT verified

- Not yet run against real production — previewing, then applying, the actual 301-row batch is the operator's next step once this deploys; production will be re-checked directly via SQL afterward, same rigor as every other verification this session.
- `place_id` geocode enrichment was deliberately not attempted for backfilled rows (see §4) — those rows will show a saved address with no `place_id`, same as any live-created address whose verification failed open.
- The pre-existing `saved_addresses` RLS-policy gap (filed as B40) was found and documented, not fixed — that's a deliberate, separate decision, not an oversight.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (`git revert`; backfilled rows are individually identifiable and deletable by their own provenance tag)
- [x] Blast radius is stated, not assumed (one caller per new function; grepped confirmation)
- [x] No silent behavior change to any existing flow — purely additive rows into a table nothing else currently reads differently based on this new metadata column
