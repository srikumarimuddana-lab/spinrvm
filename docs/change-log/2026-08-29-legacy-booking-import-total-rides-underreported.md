# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-29 |
| Author | Claude Code (session), on behalf of vikas@ngitservices.com |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | branch `claude/migration-batch-readiness-wicr1d` |
| Related issue or gap ID | Found live while the operator validated the 08-22 export on the "Legacy booking import" card — `docs/migration/2026-08-27-legacy-data-full-migration-approach.md` |

## 1. Issue / gap identified

After the service-area-ID fix unblocked validation, the operator's dry run
reported **"55 Rides to import."** That number is real but incomplete: it
is `plan.stats.rides_planned`, which has meant **completed rides only**
since before the cancelled/failed import path existed (added 2026-08-20,
A41 — see the field's own code comment: *"Completed rides only -- unchanged
meaning from before the cancelled/failed path existed, even though both
paths now share `plan.rides_to_insert`"*). The backend already computes
and returns a `cancelled_failed_rides_planned` and `total_rides_planned`
in the same JSON response, but `BookingImportCounts` (the admin-dashboard's
TypeScript type for that response) and `LegacyBookingImport.tsx`'s stat
cards were never updated when the cancelled/failed path shipped — those
fields were silently dropped on the way to the screen.

Independently verified against the raw 08-22 `bookings.csv`: after the
test-account and missing-coordinate filters `build_plan()` applies, **944**
net-new cancelled/failed rows are still candidates for insertion (before
rider/driver phone matching narrows that further) — an order of magnitude
more than the 55 completed rows the screen showed. `commit_plan()` inserts
every row in `plan.rides_to_insert`, which already includes both buckets —
only the *report* was wrong, not the commit logic itself.

## 2. Root cause

Classic "added a new code path, forgot to update every consumer of its old
output shape" gap: `build_plan()`'s `plan.stats` dict was extended with
~10 new `cancelled_failed_*` / `total_rides_planned` keys when the
cancelled/failed path was added, but the two admin-dashboard-side
consumers of that dict's shape (`BookingImportCounts` interface,
`LegacyBookingImport.tsx`'s rendering) were not updated to match. The
pre-commit confirmation text (`"This writes {c.rides_planned} ride(s)..."`)
inherited the same stale field, so an operator confirming the commit was
shown a materially smaller number than what would actually be written.

## 3. Fix / remediation

- Added the missing `cancelled_target_rows`, `failed_target_rows`,
  `cancelled_failed_rides_planned`, `cancelled_failed_skipped_already_imported`,
  `cancelled_failed_skipped_unmatched_both`, `cancelled_failed_zero_fare_completed`,
  `cancelled_failed_skipped_missing_coordinates`,
  `cancelled_failed_unmatched_riders`, `cancelled_failed_unmatched_drivers`,
  `total_rides_planned`, `insurance_periods_planned` fields to
  `BookingImportCounts` (`admin-dashboard/src/lib/api/imports.ts`) — pure
  type addition, matching what the backend already sends.
- Added a prominent **"TOTAL rides to import"** stat card
  (`c.total_rides_planned`) plus a completed/cancelled-failed breakdown,
  combined unmatched-rider/driver and already-imported counts across both
  paths, and expanded the summary line to show the cancelled/failed skip
  reasons.
- Fixed the pre-commit confirmation `<Label>` text to state
  `c.total_rides_planned` (with the completed/cancelled-failed split spelled
  out), not `c.rides_planned` alone — this is the text an operator reads
  immediately before typing `IMPORT` and clicking Commit, so it is the
  single most important string to get right on this screen.
- **Not changed**: the post-commit success message already used
  `committed.imported_rides`, which the backend sets to
  `len(plan.rides_to_insert)` (`routes/admin/booking_import.py`) — the true
  total across both paths. That string was already correct; only the
  pre-commit report and confirmation text were wrong.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated.** Grepped for other consumers of
  `BookingImportCounts` — only `admin-dashboard/src/lib/api.ts`, a
  re-export barrel with no logic of its own (per this file's own header
  comment). `LegacyBookingImport.tsx` is mounted exactly once, on this one
  card.
- **Purely additive to the type** (new optional-in-practice-but-always-present
  fields); no field removed or renamed. Existing `c.rides_planned` /
  `c.payouts_planned` usages elsewhere in the component are unchanged in
  meaning.
- **No backend change** — `build_plan()` already computed and returned
  every field this fix surfaces; nothing about what gets validated or
  written changes.
- Real production build (`npm run build`) passed, exit 0, no errors.

## 5. User-experience effect

- **Internal admin only**, but a materially important one: before this fix,
  an operator relying on the on-screen dry-run report to decide whether to
  commit was shown a number roughly an order of magnitude smaller than
  what commit would actually write to `rides` (and by extension
  `driver_insurance_periods` and `payouts`). This is exactly the kind of
  gap CLAUDE.md's pre-merge release gates exist to catch on a live-tested,
  money-adjacent surface. After this fix, the total, the completed/
  cancelled-failed split, and the exact confirmation text all agree with
  what `commit_plan()` will do.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/lib/api/imports.ts` | Added the 11 missing `cancelled_failed_*`/`total_rides_planned`/`insurance_periods_planned` fields to `BookingImportCounts` | Match the backend's actual `plan.stats` response shape |
| `admin-dashboard/src/app/dashboard/bulk-operations/_components/LegacyBookingImport.tsx` | Added a "TOTAL rides to import" stat card + completed/cancelled-failed breakdown; combined unmatched/already-imported counts across both paths; fixed the pre-commit confirmation text to state the true total instead of the completed-only count | Stop under-reporting what Commit will actually write, before the operator confirms |

## 7. Before / after

```tsx
// Before
<Stat label="Rides to import" value={c.rides_planned} />
...
<Label>
    This writes {c.rides_planned} ride(s) and {c.payouts_planned} payout record(s)
    to live data and cannot be undone from here. Type IMPORT to enable.
</Label>
```

```tsx
// After
<Stat label="TOTAL rides to import" value={c.total_rides_planned} />
<Stat label="— completed" value={c.rides_planned} />
<Stat label="— cancelled/failed" value={c.cancelled_failed_rides_planned} />
...
<Label>
    This writes {c.total_rides_planned} ride(s) ({c.rides_planned} completed +
    {c.cancelled_failed_rides_planned} cancelled/failed) and {c.payouts_planned}
    payout record(s) to live data and cannot be undone from here. Type IMPORT to enable.
</Label>
```

## 8. Rollback plan

`git-revert-safe` — pure reporting/display fix; no write path changed. A
revert restores the prior (under-reporting) display, not any data
integrity issue — nothing written by `commit_plan()` before or after this
fix differs.

## 9. Verification performed

- [x] `npx tsc --noEmit -p .` — no new errors in either changed file.
- [x] **Real production build**: `npm run build` (admin-dashboard) — exit 0, no errors. Not just a dev server or `tsc --noEmit` alone, per CLAUDE.md's requirement.
- [x] Cross-checked the backend's actual `plan.stats` dict (`booking_import_service.py` lines 1134-1178) field-by-field against the new `BookingImportCounts` fields — exact match, no field missed or misspelled.
- [x] Independently reproduced the underlying discrepancy against the real 08-22 `bookings.csv` (not just reading code): confirmed 944 net-new cancelled/failed rows pass the test-account + coordinate filters, versus the 55 completed-only rows the pre-fix screen showed.
- [x] Blast-radius grep: 1 other file imports `BookingImportCounts` (a re-export barrel, no logic).

## What was NOT verified

- Not yet re-validated live against production with this fix deployed —
  pending the operator's next validate run, which will show the corrected
  total.
- No visual regression tooling exists for admin-dashboard (per CLAUDE.md
  §6) — the new/reworded stat cards were reasoned about (same `Stat`
  component, same grid) rather than screenshotted.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (`git revert`)
- [x] Blast radius is stated, not assumed (one component + one type file,
      one inert re-export consumer)
- [x] This is itself the fix for a silent-underreporting gap on an
      already-shipped, live-tested, money-adjacent flow — found and fixed
      before any commit against this batch was made
