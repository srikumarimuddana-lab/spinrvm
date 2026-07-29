# Change Impact & Risk Log — Legacy booking import

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-29 |
| Author | Claude Code (agent-assisted), for @srikumarimuddana |
| Surface(s) | backend (+ read-only effect on rider-app, driver-app, admin-dashboard) |
| Domain (Sentry tag) | rides, payments |
| PR / commit link | branch `claude/booking-migration-csv-import-17v4nm` |
| Related issue or gap ID | Migration from the previous (MongoDB) ride-hailing app |

## 1. Issue / gap identified

Riders and drivers who used the previous app have no trip history in Spinr: their completed rides live only in a MongoDB CSV export. There was no import path for rides — `users` (migration 256) and `drivers` (migration 221) already had legacy-import provenance columns, but `rides` did not.

## 2. Root cause

Not a defect — a data migration gap. The previous app's bookings were never carried over when riders/drivers were onboarded into Spinr by phone number.

The non-obvious hazard is that Spinr does **not** store driver earnings in an earnings table: `routes/drivers/earnings.py::get_driver_balance` recomputes `payable_balance` live as `Σ(base_fare + distance_fare + time_fare + tip_amount)` over **every** completed ride, minus payouts, with no date floor — and that value bounds the Stripe payout Transfer (`routes/drivers/payouts.py`). Driver payouts for these legacy rides were already settled in the previous app, so a naive import would have handed 64 drivers a second, withdrawable copy of $2,207.06 in already-paid earnings.

## 3. Fix / remediation

A one-time, idempotent CLI importer that writes completed legacy bookings into `rides` marked with provenance metadata, and neutralizes the payable-balance side effect with one offsetting `payouts` row per driver.

Scope imported: `booking_status='completed'` where **both** customer and driver are Canadian accounts (`country_code == 1`) — 224 of 1,210 exported bookings. The remainder are cancelled/failed bookings (939) and the previous vendor's test accounts (47). Parties are matched by phone (E.164); an unmatched party imports with a NULL link so a later re-run can re-link it once they sign up. Bookings where *neither* party matches are skipped.

The column mapping satisfies two invariants simultaneously:

- **Driver money**: `base_fare` carries the driver's actual old-app earning net of tip, with `distance_fare = time_fare = 0`, so `Σ(base + distance + time + tip)` equals exactly what they earned.
- **Rider receipt**: `total_fare` carries the rider-facing residual, so `_build_fare_breakdown`'s minimum-fare uplift path (`routes/rides/_shared.py:600-616`) reconstructs the correct ride-fare line. `fare_breakdown_snapshot` carries faithful legacy line items summing exactly to the amount charged.

Then one `payouts` row per matched driver (`payout_type='legacy_import'`, `status='completed'`, `bank_name='Settled in previous app (legacy import)'`, deterministic ID `legacy-import-<batch>-<driver_id>`) deducts the same total. **Net payable delta: $0.00, verified on the real export.**

## 4. Risk & impact on existing functionality

**Blast radius: cross-surface.** Imported rows enter the live `rides` table, which is read by rider history, driver history/earnings, admin listings and rollups, and several background loops. Every consumer was enumerated:

| Consumer | Effect | Handling |
|---|---|---|
| `get_driver_balance` (`routes/drivers/earnings.py:55-81`) | Would create withdrawable balance | Offset `payouts` row; net $0 |
| `GET /rides/active` (`routes/rides/queries.py:53-63`) | Returns any completed ride with `payment_status NOT IN (paid, waived_admin)` as "active", trapping the rider on a payment screen | Import as `payment_status='paid'` + `paid_at` |
| Payment retry loop (`utils/payment_retry.py:243-268`) | Retries `failed/requires_action/processing` with no age bound — would attempt to charge historical rides | Never write those statuses |
| Distance reconciliation loop (`utils/distance_reconciliation.py:122-163`) | Scans completed rides with `distance_reconciled_at IS NULL`; the batch would flood one nightly run and could fire a false systematic-bias Sentry alert | Set `distance_reconciled_at` at import |
| Payout retry loop (`utils/payment_retry.py:223-232`) | Claims only `status='pending'` | Offset row is `completed` → inert |
| Stripe reconciler (`utils/stripe_reconcile.py:381-397`) | Flags only `requires_manual_review` / `transfer_completed` | Inert |
| Payout reservation guard (migration 250) | Unique index covers `reserved/pending/transfer_completed` only | Never blocks a future real payout |
| `rides_one_active_per_rider` (migration 53) | Partial unique index on active statuses | `completed` is terminal → unaffected |
| `rate_driver` (`routes/rides/rating.py`) | No age guard — a rider could rate a months-old imported ride into the driver's live rolling average | New guard rejects imported rides (400) |
| Rider history (`routes/rides/queries.py:149-205`) | Cursor paginates on `(created_at DESC, id DESC)` | Real historical `created_at` → interleaves correctly |
| Admin rides list (`routes/admin/rides.py:166-184`) | NULL `rider_id` rows | Already null-safe; renders a blank name |
| `financial_events` | Append-only + immutable trigger | **Not written** — history there could never be corrected |
| `driver_bonuses` | `driver_id` is UUID-typed vs `drivers.id` TEXT | **Not written** |
| `driver_daily_stats` | Nightly rollup | **Not written**; weekly/monthly driver endpoints prefer it, so those periods exclude legacy rides |

**Accepted, documented side effects (display-only, no money impact):**

1. The legacy data runs to 2026-07-26, so recent imported rides land inside the *current* week/month windows of the driver earnings endpoints (which filter on `ride_completed_at`). Driver period income displays jump for those windows. `payable_balance` is unchanged.
2. `/drivers/earnings` adds `tax_amount` as a driver income passthrough (`routes/drivers/earnings.py:245-253`). Setting `tax_amount = gst` (required for a correct rider receipt) inflates the driver's displayed *period* income by the GST amount. Per-ride driver views are unaffected because they read `driver_earnings_snapshot`, which carries `tax = 0`.
3. Admin `/stats` `total_rides` / `completed_rides` are unfiltered counts, so they include imported rides immediately. Date-bucketed rollups (`admin_ride_money_rollup` on `ride_completed_at`, `admin_ride_daily_counts` on `created_at`) place them in historical buckets.
4. The offsetting payout appears in the driver's payout history (`routes/drivers/payouts.py:1004-1024`). This is intentional and self-describing via `bank_name`.
5. `drivers.total_rides` is recomputed (not incremented) as the count of completed rides, preserving the migration-74 invariant. Consequence: the Meta `DriverActivated` first-ride event (`routes/drivers/ride_complete.py:829-845`, cheap filter `_rides_before == 0`) will not fire for legacy drivers. Correct — they activated in the previous app.
6. **`driver_insurance_periods` is NOT backfilled.** Those rides were driven under the previous app's insurance framework and its records; fabricating Spinr period rows would corrupt a regulatory audit trail that must be append-only and truthful. Stated here as a known, deliberate gap rather than an oversight.

## 5. User-experience effect

- **Riders** (matched by phone): old trips appear in the Activity tab, interleaved by date, with a receipt whose line items sum to what they were originally charged. Visible mid-session — a rider with the app open sees new history rows on next refresh. No copy changes.
- **Drivers** (matched by phone): old trips appear in ride history with their true old-app earning; lifetime earnings and `total_rides` rise; one "Settled in previous app (legacy import)" entry appears in payout history. **Withdrawable balance does not change.** Visible mid-session.
- **Internal admin**: ride counts and historical money rollups include legacy rides; rows for unmatched riders show a blank rider name.
- **Corporate admin**: no effect (no corporate fields are written).

The rating guard is the one behavior change to an already-shipped flow: rating an imported ride now returns 400. No ride created in this app is affected (they carry the `'{}'` default).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/268_rides_legacy_import_metadata.sql` | New: adds `rides.legacy_import_metadata JSONB NOT NULL DEFAULT '{}'` + unique partial index on `old_booking_id` | Provenance surface matching users/drivers; the unique index is the importer's idempotency guard |
| `backend/services/booking_import_service.py` | New: validate→commit importer (plan builder, phone matching, money mapping, offset payouts, driver recount, PII-free report) | Shared, testable core |
| `backend/tests/test_booking_import_service.py` | New: 32 tests | Money invariants, hazard fields, matching, idempotency, CSV quirks |
| `scripts/import_legacy_bookings.py` | New: CLI wrapper with `--dry-run`/`--commit` | One-time operator entry point |
| `backend/routes/rides/rating.py` | Reject rating when `legacy_import_metadata` is non-empty | Prevents a months-old imported ride mutating a driver's live rating average |
| `backend/tests/test_rate_driver.py` | +2 tests | Guard rejects imported rides; normal rides still ratable |

## 7. Before / after

Rating endpoint (the only change to existing behavior):

```python
# Before
    if ride.get("rider_rating") is not None:
        raise HTTPException(status_code=409, detail="Ride already rated")

    _pay_status = (ride.get("payment_status") or "").lower()
```

```python
# After
    if ride.get("rider_rating") is not None:
        raise HTTPException(status_code=409, detail="Ride already rated")

    # Rides imported from the previous app are historical records: ...
    if ride.get("legacy_import_metadata"):
        raise HTTPException(status_code=400, detail="Imported historical rides cannot be rated")

    _pay_status = (ride.get("payment_status") or "").lower()
```

Driver payable balance, per driver, before and after import (worked example — driver with $223.39 of legacy earnings):

```
# Before import
payable = Σ(completed ride components) - Σ(payouts)          = $X

# After import (224 rides + 64 offset payouts)
payable = ($X + $223.39) - ($Y + $223.39)                     = $X   ← unchanged
total_earnings displayed  += $223.39
total_paid_out displayed  += $223.39
```

## 8. Rollback plan

Batch-scoped, data-level, no redeploy required. Every imported artifact is tagged with the batch ID the CLI prints on commit:

```sql
-- Order matters. Run the rides delete FIRST (see note below).
DELETE FROM rides   WHERE legacy_import_metadata->>'batch' = '<batch>';
DELETE FROM payouts WHERE id LIKE 'legacy-import-<batch>-%';
UPDATE drivers d
   SET total_rides = (SELECT count(*) FROM rides r
                       WHERE r.driver_id = d.id AND r.status = 'completed')
 WHERE d.id IN (<affected driver ids>);
```

**Delete rides *before* payouts.** `payable_balance = earnings − payouts`, so removing the offset payouts first would strip a deduction and raise every affected driver's withdrawable balance by their full imported amount — the exact double-withdraw the offset exists to prevent. Removing the rides first lowers the balance instead, which is temporarily understated but recoverable, matching the preference already stated in `routes/drivers/earnings.py:68-75` ("worst case a driver is temporarily under-paid (recoverable), NEVER a double-withdraw of platform money"). Run both statements in one transaction where possible so no window exists at all.

No Stripe charge, wallet delta, or ride-state transition is created by this import, so there is nothing external to unwind. Migration 268 is additive and inert if unused; its own rollback is in the file header.

The rating-guard code change is independently revertable by `git revert` (pure code, no data effect).

## 9. Verification performed

- [x] **Automated tests**: 32 new unit tests (`test_booking_import_service.py`) all pass; 34 pass across the rating blast radius (`test_rate_driver.py`, `test_e2e_rating_regression.py`, `test_p0_rating_and_payment.py`, `test_rate_tip_abuse.py`).
- [x] **Dry run against the real export** with a fake DB simulating every legacy party as onboarded: 224 rides planned, **0 errors**, 9 warnings (4 earnings-fallback, 4 blank pickup address, 1 estimated duration). Per-row assertions held for all 224: payable components == legacy driver earning; receipt lines sum == legacy `total_amount`; snapshot total == `driver_earnings`; `grand_total` identity; hazard fields set. Per-driver offset == imported payable for all 64 drivers. **Net payable delta $0.00.**
- [x] **Data integrity cross-check**: the export's booking rows carry one more field than the header. Confirmed this is a trailing *unnamed* column, not a column shift, by cross-referencing the independent driver-earnings export (`booking_amount` matches `total_amount`, `amount` matches `you_earn`). A blind read would have crashed; a careless one could have silently mis-mapped every column.
- [x] **Blast-radius grep**: enumerated all readers/writers of `rides` (history/active/receipt paths, admin rollup RPCs, the 16 lifespan loops), of `payouts` (retry loop, Stripe reconciler, migration 250 guard, driver payout history), and of `drivers.total_rides` / `drivers.rating`. Results in §4.
- [x] **Migration linter**: `backend/scripts/check_migration.py` — naming, sequence, RLS, rollback comment, dangerous-ops all pass.
- [x] **Conventions reviewed**: money (Decimal-only throughout; floats only at the JSONB/DB boundary), migrations (append-only, rollback comment, index shipped with its query pattern), PIPEDA (report prints counts and `CB…` booking codes only; provenance blob holds opaque IDs only — asserted by test), state machine (`completed` only; no transition emitted).
- [ ] **Not feature-flagged** — justification: the import is an operator-run CLI, not a code path that activates on deploy. Nothing changes until someone runs `--commit`, and the dry run is the gate. The one code change (rating guard) is a narrow rejection on rows that do not exist until the import runs.

## 10. What was NOT verified

State explicitly, so silence does not imply coverage:

- **Not run against live or staging Supabase.** All verification used an in-memory fake client and the real CSVs. The dry run against real infrastructure (which resolves the real service area and vehicle type, and reports the true phone match rate) has not been performed — that is step 2 of the runbook and must be done before `--commit`.
- **Real phone match rate is unknown.** The dry run assumed every legacy Canadian party exists in Spinr. In production some will not match; those rides import with a NULL link (by design) or are skipped if neither party matches. Actual counts come from the real dry run.
- **No production build was run** for `admin-dashboard` / `rider-app` / `driver-app` — this change adds no frontend code. Imported rows travel existing, unmodified read paths; the fields those surfaces treat as non-optional (`vehicle_type_id`, addresses, fares, `created_at`) are all populated, which was asserted in tests but not exercised through a built client.
- **No visual/snapshot regression tooling exists** for the rider Activity tab or driver earnings screens, so "old trips render correctly" was reasoned about from the read-path code and asserted at the data level — not screenshotted. Standing gap, not specific to this change.
- **`duration_minutes` is estimated for 1 of 224 rides** (no `start_ride_at` in the export) and derived from timestamps for the rest; the previous app's own duration figure was not exported, so these values cannot be reconciled against it.
- **The legacy fare is not reconstructible line-by-line.** The export has no distance/time split, so the receipt shows a single "Ride fare" residual rather than an itemized distance/time breakdown. Totals are exact; the itemization is coarser than a natively-booked Spinr ride.
- **Tip inclusion in `total_amount` was inferred**, not documented by the previous vendor: 217 of 220 cross-checkable rows show `earnings.booking_amount == bookings.total_amount`, with the deltas being tip rows. Only 5 rides carry a tip ($16.10 total), so the exposure is small either way.
