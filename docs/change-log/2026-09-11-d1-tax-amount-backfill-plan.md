# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-11 |
| Author | Claude Code session |
| Surface(s) | backend (`services/legacy_tax_amount_backfill_service.py`, `tests/test_legacy_tax_amount_backfill_service.py`) |
| Domain (Sentry tag) | payments |
| PR / commit link | (filled in on push) |
| Related issue or gap ID | `ACTION_ITEMS.md` D1 (under A34) |

## 1. Issue / gap identified

186 legacy-imported `rides` rows have `tax_amount` populated from the old app's commission-side GST instead of rider-fare-side GST — a backend bookkeeping/categorization bug, not a pricing error riders ever saw (see `docs/change-log/2026-08-15-legacy-import-gst-preservation.md` for the original finding). The product owner decided on 2026-09-07 to fix it using the already-preserved `legacy_import_metadata->>'old_payout_gst_amount'` figure, but validated, not copied blind.

## 2. Root cause

`booking_import_service.py`'s importer read the export's `gst` column (which is `commission_gst_amount`) straight into `tax_amount`, never reading the separate `payout_gst_amount` column that holds the correct rider-fare-side figure. Fixed for future imports 2026-08-16 (PR #3963); the 186 rows imported before that fix still carry the wrong value.

## 3. Fix / remediation

**This PR does not touch production data.** It ships the validated *plan-builder tool* the D1 decision calls for — a new read-only module, `legacy_tax_amount_backfill_service.py` — mirroring the existing `legacy_gst_backfill_service.py` pattern used for the 2026-08-16 backfill:

1. `build_backfill_plan(bookings_csv)` fetches every legacy-imported ride and, for each one, validates the stored `old_payout_gst_amount` two ways before proposing it as the new `tax_amount`:
   - **Re-derives the figure from `bookings.csv` and compares.** A mismatch (something drifted since the 2026-08-16 backfill) or an unresolved CSV lookup **blocks** the row — it is flagged in the plan, never silently applied.
   - **Advisory 5%-of-`total_fare` sanity check.** GST is 5% of the pre-tax fare; `total_fare` is the closest already-stored approximation of that base for a legacy row. A row outside tolerance is flagged for human review but stays applyable — `total_fare` isn't a confirmed-exact GST base, so this is a flag, not a gate.
   - A row missing `old_payout_gst_amount` entirely (or carrying an explicit JSON `null`) is blocked as `not_yet_backfilled`, never crashes the plan.
2. `print_report(plan)` — human-readable dry-run summary (counts, blocked-row list with reasons, sanity-outlier list, before/after sums).
3. `render_update_sql(plan)` — renders (never executes) the guarded SQL `UPDATE` for the plan's applyable rows only, for a human with `DATABASE_URL` production access to review and run separately. This session has no such access — same constraint noted throughout `ACTION_ITEMS.md` for every other production write.

No commit/apply path exists in the module itself, by design — same posture as `legacy_gst_backfill_service.py`.

A `spinr-money-auditor` review pass on the first draft found and fixed 4 issues before this was written up (see commit `6113cc6`): a `float()` conversion of a Decimal in the emitted SQL's JSON literal (now built from the Decimal's own `str()`), a missing `ride_id` SQL-literal escape (defensive hardening — `rides.id` is a backend `uuid4()` today, not currently exploitable), a hardcoded copy of `IMPORT_SOURCE` in the emitted guard clause that could drift from the module's own fetch filter, and an unhandled explicit-`null` case for `old_payout_gst_amount` that would have raised `decimal.InvalidOperation` and aborted the whole plan instead of flagging just that row.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated.** Two new files, zero existing files modified. Grepped for other callers/consumers of `rides.tax_amount`, `rides.tax_breakdown`, and `legacy_import_metadata->>'old_payout_gst_amount'` — no route, service, or background loop reads the new module or its output; nothing in the request path is touched.
- **No production data is written by this PR.** `build_backfill_plan()` is read-only end to end (confirmed: no `.update(`/`.insert(`/`.upsert(` anywhere in the module); `render_update_sql()` only returns a string for a human to review.
- **When the emitted SQL is eventually run** (a separate, later step by someone with `DATABASE_URL` access — not this PR): it writes `rides.tax_amount` and `rides.tax_breakdown` only, guarded by `r.tax_amount = v.old_tax_amount` (optimistic concurrency — a row changed since the plan was built is skipped, not clobbered) and `r.legacy_import_metadata->>'source' = 'legacy_mongo_booking_import'` (scoped to legacy rows only). No other column, and no non-legacy ride, can be touched by the generated statement.
- **Downstream consumers of `tax_amount`/`tax_breakdown`** (admin dashboards, `admin_ride_money_rollup`, `admin_payouts_overview_aggregates`, receipt rendering) will see a different number **once the emitted SQL is actually run** — that execution is out of scope for this PR and requires its own sign-off at that time (this doc documents the tool, not the write).

## 5. User-experience effect

None from this PR. No rider, driver, corporate-admin, or internal-admin screen reads this module or is affected by it merging. When the correction SQL is eventually run (separately), the effect is backend-bookkeeping-only per the product owner's own determination (`ACTION_ITEMS.md` D1, 2026-09-07): "all receipts had the gst populated and validated... the dollar amount riders were actually charged and shown was correct at the time" — no refund, receipt reissue, or rider-facing disclosure follows from it.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/services/legacy_tax_amount_backfill_service.py` | New — read-only plan builder validating `old_payout_gst_amount` against `bookings.csv` + a sanity check, and a guarded-SQL renderer | Implements the validation step the D1 decision requires before any write |
| `backend/tests/test_legacy_tax_amount_backfill_service.py` | New — 9 unit tests (applyable/validated row, CSV-drift block, CSV-unresolved block, not-yet-backfilled block, explicit-null block, sanity-outlier flagged-not-blocked, report content, SQL-rendering scope + `IMPORT_SOURCE` reuse + Decimal-safe JSON) | Locks in the validation logic so a future edit can't silently start trusting an unvalidated figure |

## 7. Before / after

Not applicable — purely additive new module, no existing caller or behavior changed.

## 8. Rollback plan

`git revert` — this PR contains no production write and no migration; reverting removes the tool with zero data-level cleanup needed. **Separately, once the emitted SQL is actually run** (a later, human-gated step outside this PR): that write only sets `tax_amount`/`tax_breakdown` on rows guarded by the previous `tax_amount` value, so rollback at that time is `UPDATE rides SET tax_amount = <old value>, tax_breakdown = <old breakdown> WHERE id = ANY(<ride_ids>)` using the `RETURNING r.id` list and the plan's own `old_tax_amount`/`old_tax_breakdown` snapshot as the restore values — not something this PR needs to provide, since it performs no write itself.

## 9. Verification performed

- [x] Unit tests: `pytest backend/tests/test_legacy_tax_amount_backfill_service.py` — 9/9 pass
- [x] `ruff check` on both new files — clean
- [x] `ast.parse` on both new files — clean
- [x] Blast-radius grep: no existing caller of the new module; no other reader of `old_payout_gst_amount`
- [x] `spinr-money-auditor` review pass — 4 findings, all fixed before this doc was written (see §3)
- [x] Reviewed against CLAUDE.md conventions: Decimal-only money math (all comparisons/arithmetic go through `to_decimal()`), dual-import pattern (matches `legacy_gst_backfill_service.py`), one-off data-migration script rules (no real production data embedded, no CSV committed)
- [ ] Not run against a real Supabase connection or the real `bookings.csv` — this session has neither `DATABASE_URL` access nor the (PII-bearing, gitignored) real export file; all tests use synthetic fixtures via a mocked Supabase client, consistent with every other legacy-migration-tool test in this repo
- [ ] Not feature-flagged — not applicable, this is an internal dev tool with no runtime/request-path surface

## What was NOT verified

- **The actual `tax_amount` values for the real 186 rows** — this session cannot connect to production Supabase, so the plan/report/SQL this tool would produce against real data is unverified here. Whoever runs it should read `print_report(plan)`'s output before running `render_update_sql(plan)`'s output, and treat any `csv_drift`/`csv_unresolved`/`sanity_flagged` row as a stop-and-look signal, not a rubber stamp.
- **Whether the real `bookings.csv` (source export) is still available/unchanged** — the Oct 31 old-app decommission (noted in `docs/change-log/2026-08-16-gst-backfill-executed.md`) means this file's availability is time-sensitive; not confirmed from this session.
- **The actual production write** is explicitly out of scope for this PR and is a separate, later, human-gated step — this doc covers the tool only, not the data correction itself.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (`git revert` for this PR; the separate future data-write's rollback is derivable from the plan's own before-values, stated explicitly above)
- [x] Blast radius is stated, not assumed (grepped, not guessed — two new files, zero existing callers)
- [x] No silent behavior change to an already-shipped flow without the UX field filled in (none — this PR changes no runtime behavior; §5 states plainly there is no effect from this PR alone)
