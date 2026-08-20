# 7-anomalous-row disposition — implementation (zero-fare completed import)

## 1. Issue/gap identified

`docs/change-log/2026-08-20-anomalous-legacy-rows-payment-verification.md` found and confirmed —
using the old app's own `payments.csv` export, no live DB/Stripe needed — that 7 legacy bookings
labeled `booking_status='failed'` structurally completed a real trip (real GPS/timestamps/driver) but
were never paid for (0/225 `failed` bookings, including all 7, have any payment record). Until this
change, `booking_import_service.py`'s hard guard permanently excluded these rows from any import path:
neither the completed-fare path (would falsely assert the driver was already paid) nor the lightweight
cancelled/failed path (would violate the ride state machine's "never cancelled after trip start"
invariant, since these rows have real start/end timestamps) fit.

## 2. Root cause

Two pre-existing import branches, each built for a different shape of legacy row, neither of which
matches "trip completed, payment never collected." No third branch existed.

## 3. Fix/remediation

Product-owner decision recorded 2026-08-20 (`AskUserQuestion`, option **"Completed, $0 fare"**) after
presenting three concrete options grounded in the payment-verification finding. `booking_import_service.py`'s
cancelled/failed loop now branches on the same structural check the old hard guard used
(`start_ride_at` AND `complete_delivery_at` both populated) — instead of skipping, it imports the row as
`rides.status='completed'` with real GPS/pickup-dropoff/distance/duration/timestamps, but:

- `base_fare` / `distance_fare` / `time_fare` / `total_fare` / `tip_amount` / `driver_earnings` /
  `admin_earnings` all `0.0`; `grand_total` / `tax_amount` / `area_fees_total` / `discount_amount` all
  `"0"`.
- No `payouts` row (the completed path's offsetting-payout mechanism is skipped entirely for this
  branch — there is nothing to offset).
- `payment_status="pending"` — deliberately never `"failed"`/`"processing"`/`"requires_action"`.
  `payment_retry.py`'s `retry_failed_payments()` scans exactly `{"failed", "requires_action",
  "processing"}` for any row with `status != 'cancelled'`, which a real completed row here is; setting
  one of those values would have made the background payment-retry loop try to actually collect payment
  on a ~9-year-old $0 legacy ride the next time it ran. `auth_status` is deliberately left unset (never
  `"authorized"`/`"fare_only"`) so `preauth_capture.py`'s `status='completed' AND payment_status='pending'
  AND auth_status IN (...)` sweep can't claim it either. With `total_fare=0`, `"pending"` is not
  misleading — nothing is actually owed.
- `legacy_import_metadata.anomalous_looks_completed_zero_fare = true` — new, explicit marker
  distinguishing these rows from every other legacy-completed row (which does carry real fare/earnings),
  alongside the existing `original_booking_status`/`duration_estimated` markers.
- Driver **is** added to `plan.driver_ids_to_recount` (unlike the normal cancelled/failed branch, which
  deliberately never recounts — those rows are never `status='completed'`). `total_rides` is a plain
  `COUNT` of `status='completed'` rows; a real completed trip should be counted, even at $0 fare.

New, separately-tracked stat: `cancelled_failed_zero_fare_completed` (replaces the removed
`cancelled_failed_skipped_looks_completed`). `cancelled_failed_rides_planned` (status='cancelled' rows
only) now explicitly subtracts this count too, so its meaning is unchanged from before this fix.

**No migration was needed.** These new rows are `status='completed'` with `legacy_import_metadata !=
'{}'`, exactly the shape migration 341's `completed` CTE, migration 349's blanket-exclusion functions,
and migration 349's narrower `cohort`-CTE predicate already handle — they fall into the same
already-established "legacy completed row" analytics treatment as the other 271 (271 + 7 = 278 going
forward), with zero new admin-analytics gaps.

## 4. Risk & impact on existing functionality

**Blast radius grepped, not assumed:**
- `booking_import_service.py`'s `build_plan`/`commit_plan`/`recount_drivers` — only caller-visible
  change is the new `status='completed'`, $0-fare rows appearing in `plan.rides_to_insert` for rows that
  previously never appeared anywhere. No existing field, stat, or behavior for the completed-fare path or
  the normal cancelled/failed path changed.
- `payment_retry.py`'s `retry_failed_payments()` and `sweep_guest_corporate_settlements()`, and
  `preauth_capture.py`'s completed+pending sweep — all three background loops were checked against the
  new rows' exact field values (`payment_status`, `auth_status`, `payment_method`, `guest_booking`) and
  confirmed inert; see the `payment_status` reasoning above.
- `admin_earnings_overview_agg`/`admin_earnings_daily_series`/`admin_dashboard_money` (migration 341) and
  `admin_driver_acceptance_rates`/`admin_cancellation_breakdown`/`admin_analytics_overview` (migration
  349) — all already exclude `legacy_import_metadata != '{}'` completed rows from every money/funnel
  aggregate except the `cohort` CTE's `fn_completed` bucket (migration 349's deliberate, narrower
  choice) — unchanged by this fix, these 7 rows land in the same bucket as the other 271.
- `drivers.total_rides` (via `recount_driver_total_rides` RPC / fallback) — will increase by 1 for each
  of the up to 7 affected drivers once committed. Purely an activity count, no money attached.
- Driver Activity screen / trip history surfaces reading `rides` directly — will show these as completed
  trips with $0 fare once committed; not verified visually (no live environment), reasoned about only.

## 5. User experience effect

**None until `--apply`/commit actually runs** (still pending, per the rollout runbook). Once committed:
up to 7 riders and/or drivers may see one additional completed trip each in their trip history, with $0
fare — same "the app now shows a trip you took years ago" effect the rest of this session's legacy
import already produces for hundreds of other rows, not a new UX pattern.

## 6. Files modified

| File | What changed | Why |
|---|---|---|
| `backend/services/booking_import_service.py` | New branch in the cancelled/failed loop: `looks_completed` rows import as `status='completed'`, $0 fare/earnings/payout, `payment_status='pending'`, driver recounted. Stats renamed/added. Module docstring updated. | Implements the 2026-08-20 disposition decision. |
| `backend/tests/test_booking_import_cancelled_failed.py` | Replaced the 2 "skipped/excluded" tests with 8 new tests covering the zero-fare-completed import, payment_status inertness, GPS/timestamp preservation, legacy metadata flag, driver recount, and idempotency. | Cover the new branch; the old tests asserted behavior this change intentionally removes. |
| `docs/change-log/2026-08-20-anomalous-legacy-rows-payment-verification.md` | (New, prior commit) Investigation finding. | Establishes the evidence this fix acts on. |
| `docs/change-log/2026-08-20-anomalous-rows-zero-fare-completed-import.md` | This file. | Change Impact Log for the implementation, per CLAUDE.md mandate. |
| `ACTION_ITEMS.md` | A41's "7-anomalous-row disposition" item marked FIXED. | Tracking. |

## 7. Before/after snippet

Before (hard guard, `booking_import_service.py`):
```python
if (b.get("start_ride_at") or "").strip() and (b.get("complete_delivery_at") or "").strip():
    cancelled_failed_skipped_looks_completed += 1
    plan.warnings.append(ImportReportItem(idx, code, "booking_status", "... not imported by this path"))
    continue
```

After:
```python
looks_completed = bool((b.get("start_ride_at") or "").strip()) and bool(
    (b.get("complete_delivery_at") or "").strip()
)
...
if looks_completed:
    ride = {
        ...
        "status": "completed",
        "total_fare": 0.0, "driver_earnings": 0.0, "admin_earnings": 0.0,
        "payment_status": "pending",  # inert to payment_retry.py / preauth_capture.py
        "legacy_import_metadata": {..., "anomalous_looks_completed_zero_fare": True},
    }
    plan.rides_to_insert.append(ride)
    cancelled_failed_zero_fare_completed += 1
    if driver_id:
        plan.driver_ids_to_recount.add(driver_id)
    continue
```

## 8. Rollback plan

No migration involved. If committed and found wrong: the commit response returns every inserted ride's
`id` (same shape as every other path in this importer). Reverting means deleting exactly those `rides`
rows by id, then re-running `recount_driver_total_rides` (or waiting for the next import/recount) for
any affected driver to reflect the reversal. No `payouts` row exists for these rows to unwind. Add this
capability to `docs/runbooks/legacy-backfill-scripts-rollout.md`'s existing "Cancelled/failed legacy
booking import" rollback section (same id-list-based revert already documented there) rather than a
separate procedure.

## 9. Verification performed

- `ruff check backend/services/booking_import_service.py` — clean.
- `python3 -m py_compile` — clean.
- New unit tests added (8) covering: zero-fare-completed classification, no fare/earnings/payout written,
  payment_status/auth_status inertness to the two background loops, GPS/timestamp preservation, legacy
  metadata flag, driver recount inclusion, idempotency on re-run, and that a row with only ONE of the two
  timestamps still imports as a normal cancellation (not misclassified as anomalous).
- Full `test_booking_import_cancelled_failed.py` + `test_booking_import_service.py` suites re-run
  after the change.
- **No production build applicable** — backend-only change, no `admin-dashboard`/`rider-app`/`driver-app`
  surface touched.

## 10. What was NOT verified

- **No live Supabase.** All tests run against the fake in-memory Supabase client, same as every other
  importer in this session. No `--apply`/commit has happened against any environment for this branch.
- **Not re-verified against the real cached CSV export** the way the original cancelled/failed path was
  (that verification, done in the prior task, is what produced the exact "7 rows, all in `failed`" count
  this fix targets) — the real export's 7 rows were not re-run through `build_plan()` for this specific
  commit; the new unit tests use a synthetic row with the same structural shape instead. Recommended
  before actually running `--apply`: one more real-export dry run, per the rollout runbook's existing
  pre-flight checklist.
- **No live Stripe cross-check** — not applicable (no money touched).
- **No visual/snapshot regression tooling** — backend-only, not applicable here.
