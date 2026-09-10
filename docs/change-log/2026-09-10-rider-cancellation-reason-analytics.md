# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-10 |
| Author | Claude Code (feat/50) |
| Surface(s) | backend, admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | 77c9730, d1866bb (this branch, `mvapps/sleepy-galileo-hqp2xn`) |
| Related issue or gap ID | feat/50 "rider cancellation reason analytics" |

## 1. Issue / gap identified

`admin_cancellation_breakdown` (the RPC behind `/api/admin/analytics/cancellation-reasons`)
classified *who* cancelled a ride by fuzzy-matching the free-text `cancellation_reason`
column (e.g. `lower(cancellation_reason) LIKE '%driver%'`), even though migration 38 added
the structured `cancelled_by`/`cancellation_type` columns in 2026 expressly "so reports can
aggregate without parsing reason strings." Separately, there was no breakdown at all of
*why* a rider cancels — only who cancelled, never the rider's stated reason.

## 2. Root cause

The rider-facing cancel-reason preset in `rider-app/components/CancelReasonSheet.tsx`,
`"Driver is too far / long wait"`, contains the substring `"driver"` but not `"rider"`. The
string-matching CASE statement in `admin_cancellation_breakdown` (migration 350's version)
checks `LIKE '%rider%'` before `LIKE '%driver%'`, but that check order doesn't save this
case — the text never matches `%rider%` at all, so it falls through to the `%driver%` branch
regardless of who actually cancelled. A **rider** picking this exact, most-common preset was
bucketed as a `driver_cancelled` / `party='driver'` row. This function was simply never
updated to use the migration-38 columns when they were added; the sibling function
`admin_marketplace_funnel` (migration 351) was already fixed to prefer them, but that fix
was never ported to this function. This directly undermines the rider-cancellation-rate KPI
diagnostic ("Long wait or wrong ETA" — CLAUDE.md's KPI table), because the single most
informative bucket for that diagnosis was being counted against the wrong party.

## 3. Fix / remediation

Migration 411 (`backend/migrations/411_cancellation_breakdown_structured_attribution.sql`)
`CREATE OR REPLACE`s `admin_cancellation_breakdown` to:
1. Prefer `cancelled_by`/`cancellation_type` over string-matching for both the `reason` and
   `party` fields (string-matching remains only as a fallback for pre-migration-38 rows with
   a NULL `cancelled_by`) — same pattern as `admin_marketplace_funnel` (351).
2. Add a new `rider_reasons`/`total_rider_cancellations` breakdown: what riders actually
   selected in `CancelReasonSheet.tsx`, scoped to rows now correctly attributed to the rider.

`backend/routes/admin/analytics.py`'s `get_cancellation_breakdown()` threads the two new
fields through to the JSON response and bumps the Redis cache key `v2` → `v3` (the RPC's
response shape changed, so a stale cached body would be missing the new keys).
`admin-dashboard/src/app/dashboard/analytics/page.tsx` adds a new "Rider Cancellation
Reasons" table to the existing Cancellations tab, in the same style as the pre-existing
"Cancellation Reasons" table.

## 4. Risk & impact on existing functionality

- **Callers of `admin_cancellation_breakdown`**: grepped the whole repo — the only caller is
  `backend/routes/admin/analytics.py`'s `get_cancellation_breakdown()` (`/api/admin/analytics/cancellation-reasons`).
  No other route, service, or scheduled job calls this RPC. Blast radius: **isolated to this
  one endpoint.**
- **Bucket-name contract**: the existing `reason` values (`rider_cancelled`,
  `driver_cancelled`, `no_drivers_available`, `search_timeout`, `scheduled_cancelled`,
  `unspecified`, `other`) and `party` values (`rider`, `driver`, `admin`, `system`, `unknown`)
  are unchanged — only the *logic* that assigns rows to them changed. One new `reason` value,
  `admin_cancelled`, appears for the first time (admin force-cancels, previously folded into
  `other`). The frontend's `REASON_LABELS`/`REASON_COLORS` maps already handle an unknown key
  gracefully (`REASON_LABELS[r.reason] || r.reason`), so this was safe even before I added an
  explicit label/color for it — additive, not breaking.
- **`by_party` field**: confirmed (grep) it is computed by the RPC but was never rendered
  anywhere in `analytics/page.tsx` before this change, and still isn't — out of scope for
  this fix; noted here so it isn't mistaken for newly-broken.
- **Legacy-imported rows**: `backend/services/booking_import_service.py` writes a synthetic
  `cancellation_reason` ("No driver found (legacy import)") for legacy bookings with no
  driver. This function's `WHERE legacy_import_metadata = '{}'::jsonb` predicate (carried
  over unchanged from migration 349) excludes every legacy-imported row before any
  classification runs, so this fix has no interaction with that data at all.
- **Ride state machine / money paths**: none touched. This is a read-only, `STABLE` analytics
  function; no writes to `rides` or any other table.
- **Historical data, not just new rides**: this is a *reclassification* of existing rows on
  every read (the function re-derives `reason`/`party` from stored columns each call — no
  backfill needed or performed). Any cached dashboard numbers from before this deploy that an
  admin compares against post-deploy numbers for the same historical date range will show a
  step change in `driver_cancelled`/`rider_cancelled`/`admin_cancelled` counts. This is the
  fix working as intended (correcting past mis-attribution), not a regression, but it is
  worth calling out explicitly since a KPI trend line will show a discontinuity at the
  deploy boundary.

## 5. User-experience effect

- Admin-dashboard only (internal admin, not rider/driver/corporate-facing).
- Visible on the existing Analytics → Cancellations tab: the "Cancellation Reasons" pie
  chart/table's existing category counts will shift (rider_cancelled counts increase,
  driver_cancelled counts decrease, since the bug specifically miscounted riders as drivers)
  once deployed, and a new "Admin Cancelled" category appears. A new "Rider Cancellation
  Reasons" table appears below it.
- Not visible mid-session to a rider or driver — this is a backend RPC + an admin-only
  dashboard page, not a rider/driver-facing flow.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/411_cancellation_breakdown_structured_attribution.sql` | New migration: `CREATE OR REPLACE admin_cancellation_breakdown` — structured-attribution-first classification + new `rider_reasons`/`total_rider_cancellations` output | Fix the mis-attribution bug; add the rider-reason breakdown the ticket asked for |
| `backend/routes/admin/analytics.py` | `get_cancellation_breakdown()` now reads `rider_reasons`/`total_rider_cancellations` from the RPC response and includes them in the JSON response; cache key bumped `v2` → `v3` | Thread the new RPC fields to the frontend; avoid serving stale-shaped cached responses after deploy |
| `backend/tests/test_admin_analytics_coverage.py` | New `TestCancellationBreakdownMigration411` static-assertion test class; two new route-level tests (`test_rider_reasons_passed_through_with_pct`, `test_rider_reasons_zero_total_no_division_error`); updated `test_cache_key_version_bumped_off_the_utc_buckets` (the two endpoints' cache versions now diverge) | Cover the new migration and route behavior; fix a test whose assumption (`overview` and `cancellation-reasons` share one cache version) no longer holds |
| `admin-dashboard/src/app/dashboard/analytics/page.tsx` | New "Rider Cancellation Reasons" table in the Cancellations tab; new `RIDER_REASON_LABELS`/`riderReasonColors()`; added `admin_cancelled` to the existing `REASON_LABELS`/`reasonColors()` | Render the new breakdown; give the new `admin_cancelled` bucket a real label/color instead of relying on the fallback grey |

## 7. Before / after

```sql
-- Before (migration 350's version of admin_cancellation_breakdown's `party` CASE)
CASE
    WHEN cancellation_reason IS NULL OR cancellation_reason = '' THEN 'unspecified'
    WHEN lower(cancellation_reason) LIKE '%no nearby drivers%'
      OR lower(cancellation_reason) LIKE '%no driver%' THEN 'no_drivers_available'
    WHEN lower(cancellation_reason) LIKE '%rider%' THEN 'rider_cancelled'
    WHEN lower(cancellation_reason) LIKE '%driver%' THEN 'driver_cancelled'  -- matches
                                                                              -- "Driver is
                                                                              -- too far..."
                                                                              -- even when a
                                                                              -- RIDER picked it
    ...
END AS reason,
```

```sql
-- After (migration 411)
CASE
    WHEN cancellation_type = 'no_drivers_found' THEN 'no_drivers_available'
    WHEN cancelled_by = 'rider'  THEN 'rider_cancelled'   -- structured column checked first
    WHEN cancelled_by = 'driver' THEN 'driver_cancelled'
    WHEN cancelled_by = 'admin'  THEN 'admin_cancelled'
    WHEN cancelled_by = 'system' THEN 'no_drivers_available'
    WHEN cancellation_reason IS NULL OR cancellation_reason = '' THEN 'unspecified'
    -- string-matching only runs for pre-migration-38 rows below this point
    ...
END AS reason,
```

## 8. Rollback plan

Re-run migration 350's `admin_cancellation_breakdown` function body verbatim (a
`CREATE OR REPLACE`, no schema change, no data written) — this is a pure read-path revert,
documented in migration 411's own rollback comment. If only the frontend/route changes need
reverting independently, `git revert` those two commits; they touch only response
construction and rendering, not stored data.

Note: because this is a read-only reclassification (see §4), there is no data-level
remediation needed either direction — reverting or rolling forward only changes how existing
rows are *read*, never what's stored.

**Renumbering note:** this migration was originally authored and committed as `410_...sql`.
CI's Migration Safety Check caught a genuine cross-PR numbering race — a different PR
(`410_ai_fare_quote_show_unavailable.sql`) merged migration 410 to `main` first, after this
branch had already been created from an earlier `main`. Per `backend/migrations/CLAUDE.md`
("if two PRs conflict on a number, the second one renames to the next free slot before
merge"), this file was renamed to `411_...sql` and all in-repo self-references (the file's own
header comment, its `COMMENT ON FUNCTION`, the test class name, and this log) were updated to
match. No functional change resulted from the rename.

## 9. Verification performed

- [x] Automated tests run: `pytest backend/tests/test_admin_analytics_coverage.py -q -k Cancellation` → 19 passed (7 pre-existing in `TestCancellationReasons` + 1 pre-existing in `TestMarketplaceMigration351`, unrelated to this change but matched by the keyword filter, + 2 new route-level tests + 9 new `TestCancellationBreakdownMigration411` static-assertion tests). The pre-existing `test_cache_key_version_bumped_off_the_utc_buckets` was also updated (not added) since its assumption that `/overview` and `/cancellation-reasons` share one cache version no longer holds. `ruff check` / `ruff format --check` clean on both changed backend files.
- [x] Manual repro / verification: read migration 350's actual current body (not a stale earlier version — self-corrected mid-investigation, see below) and confirmed the exact bug via `CancelReasonSheet.tsx`'s preset text before writing the fix.
- [x] Blast-radius grep performed: `admin_cancellation_breakdown` (only caller: `routes/admin/analytics.py`), and `booking_import_service.py`'s legacy synthetic reason text (excluded via `legacy_import_metadata` predicate, unaffected).
- [x] Reviewed against relevant CLAUDE.md conventions: migration append-only/reversible rules, structured-attribution pattern already established by migration 351, cache-key versioning convention already established by the `v2` bump on the same function.
- [x] Frontend: `tsc --noEmit` clean; `eslint` on the changed file shows only 3 pre-existing warnings unrelated to this diff (0 errors); a real `npm run build` (not just dev server or `tsc`) compiled successfully; `npx playwright test e2e/crawl-audit.spec.ts -g "audit: /dashboard/analytics$"` (a11y, axe) passed with 0 violations against a running `next start` production build; manually verified the new table's rendering with a mocked API response carrying real `rider_reasons` data via a throwaway Playwright script (screenshot confirmed correct labels, colors, sort headers, percentages) — script was not committed.
- [x] Feature-flagged: not flagged. This is an admin-only, read-only analytics correctness fix + additive table — no rider/driver-facing behavior changes, and the existing bucket-name contract is preserved (see §4), so per CLAUDE.md's flagging criteria (user-visible *and* non-trivial, or a shared component used by 3+ pages) this doesn't meet the bar. The classification-logic change is visible only as corrected historical counts on one admin dashboard page.

## 10. What was NOT verified

- Not tested against a live Supabase/Postgres instance — the SQL was verified only via static
  string-assertion tests on the migration file body (no local Postgres available in this
  session to actually execute the CASE logic against real rows). This is the standard
  limitation for migration verification in this repo/session (see `TestMarketplaceMigration351`
  and similar classes) — the SQL syntax itself (balanced parens, valid CTE structure, correct
  `jsonb_build_object` usage) was hand-reviewed but not executed.
- Not tested against the visual-regression suite — `/dashboard/analytics` is not one of the 6
  seeded visual-regression pages (`login`, `dashboard-home`, `dashboard-drivers`,
  `dashboard-monitoring`, `dashboard-settings`, `dashboard-rides`), so there is no
  pixel-diff baseline for this page at all; the crawl-audit (a11y) pass and the manual
  screenshot above are the only visual verification performed.
- The historical-discontinuity effect noted in §4 (KPI trend lines showing a step change at
  deploy time) was reasoned about, not measured against real production cancellation data —
  no access to production `rides` rows in this session to quantify how large the shift will
  actually be.

## 11. Sign-off

- [x] Rollback plan is concrete and testable (re-run 350's function body verbatim; no data mutated either direction)
- [x] Blast radius is stated, not assumed (single caller, confirmed by repo-wide grep)
- [x] No silent behavior change to an already-shipped flow without the UX field filled in (§5 above)
