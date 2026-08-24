# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-24 |
| Author | Claude Code session (see PR for session link) |
| Surface(s) | backend |
| Domain (Sentry tag) | safety, payments |
| PR / commit link | (this branch) |
| Related issue or gap ID | Found during a migration-batch-readiness / Mongo-import audit this session; no prior tracked issue |

## 1. Issue / gap identified

`services/booking_import_service.py` (the legacy Mongo booking importer) writes zero
`driver_insurance_periods` rows for any newly-imported completed ride — the regulatory
Period-2/Period-3 audit trail migration 332 backfilled once, manually, for the 186
already-imported rides was never folded into the importer itself, so every future delta
import would silently repeat the same gap. Separately, `rides.airport_fee` was hardcoded
to `0.0` on every legacy-imported ride, even when the rider was actually charged an
airport pickup/dropoff fee (correctly preserved elsewhere, in `area_fees_breakdown`) —
any surface reading `airport_fee` directly (e.g. `routes/admin/rides.py`'s ride-detail
endpoint) shows $0 on a real airport trip.

## 2. Root cause

The importer was built (2026-07-29) before `driver_insurance_periods` existed as a
concept the import path needed to populate; migration 332 (2026-08-18) fixed the
already-imported rows via a one-time direct SQL backfill but nobody wired the same logic
into the importer's ongoing commit path. `airport_fee` was set to `0.0` with a code
comment acknowledging the charge instead lives in `area_fees_breakdown` — correct for
the rider-facing total, but nobody updated the dedicated column at the same time.

## 3. Fix / remediation

- Added `_plan_insurance_periods()` to `booking_import_service.py`, called for every
  newly-imported `status='completed'` ride (both the real-earnings path and the
  anomalous $0-fare "looks completed" path) that has a matched `driver_id` and the
  relevant timestamps. Mirrors migration 332's own logic exactly: Period 2
  (`arrived_at` → `started_at`), Period 3 (`started_at` → `completed_at`), each row
  marked `is_reconstructed = true`. A ride missing a timestamp for one leg (e.g. no
  `arrived_pickup_loc_at`) simply gets no row for that leg — never a fabricated
  boundary, matching migration 332's own exclusion rule.
- `commit_plan()` now batch-inserts `plan.insurance_periods_to_insert` into
  `driver_insurance_periods`, after the rides insert (FK dependency) and before the
  payouts insert.
- `airport_fee` is now `parse_money(airport_pickup_charges) + parse_money(airport_drop_charges)`
  instead of a hardcoded `0.0`, on the real-earnings completed path only. The anomalous
  $0-fare path's `airport_fee: 0.0` is left untouched — deliberate, since that row was
  never actually charged anything (0/225 legacy `failed` bookings have a `payments.csv`
  record, per the existing anomalous-row disposition decision).
- Added `insurance_periods_planned` to `plan.stats` / `print_report()` for
  auditability; flows through automatically to the admin dry-run/commit API response
  (`routes/admin/booking_import.py` already does `dict(plan.stats)`, no route change
  needed).

## 4. Risk & impact on existing functionality

- **What else reads/writes the same table/state:** `driver_insurance_periods` is
  written elsewhere only by `utils/insurance_periods.py`'s `record_period_transition()`
  (live, real-time transitions via an RPC that uses "now", not explicit timestamps —
  confirmed not reusable for historical backfill, which is why this follows migration
  332's direct-INSERT pattern instead). This importer never calls
  `record_period_transition()` and never touches the `driver_insurance_periods_open`
  partial unique index (every row it writes has `ended_at` set — closed, historical —
  same as migration 332). `rides.airport_fee` is read by `routes/admin/rides.py`'s
  ride-detail endpoint and several other admin/service-area files (grepped:
  `routes/admin/service_areas.py`, `routes/rides/receipts.py`, `routes/rides/booking.py`,
  `services/fare_service.py`, `routes/rides/estimates.py`, `routes/rides/payments.py`) —
  all read it as a plain numeric column; none assume it's always 0 for legacy rides, so
  populating it correctly can only fix under-reporting, not introduce a new failure mode.
- **Could this regress a flow that currently works?** No — both changes are purely
  additive for newly-imported rows going forward. Already-committed legacy rides
  (the 186 covered by migration 332, and any imported between 332 and this fix) are
  untouched; this only changes what a *future* import run writes.
- **Blast radius:** isolated to `services/booking_import_service.py` and the
  `driver_insurance_periods`/`rides.airport_fee` writes it performs. No other importer
  (`rider_import_service.py`, `driver_import_service.py`) or live ride-flow code path
  changed.
- **Interaction with background loops / ride state machine / money:** none. No
  background loop reads legacy-import state; the ride state machine is untouched (rides
  still land as `status='completed'`/`'cancelled'` exactly as before); no money/wallet
  delta changed — `airport_fee` populating correctly does not change `total_fare`,
  `grand_total`, or any payout amount, since the charge was already counted once via
  `area_fees_breakdown`/`area_fees_total`.
- **Dependency:** requires migration 332 (which adds the `is_reconstructed` column) to
  already be applied to whatever database this importer runs against. Migration 332 was
  applied to production 2026-08-18 per `ACTION_ITEMS.md` G2; a future run against a
  fresh/staging database would need migrations applied through at least 332 first (the
  importer's own `INSERT` would fail with an unknown-column error otherwise, loudly, not
  silently).

## 5. User-experience effect

Nobody — this only affects data written by an admin-only, offline import tool
(`routes/admin/booking_import.py`), not any live rider/driver/corporate-admin flow. The
only visible effect is that a future admin running a legacy delta import will see
correct `driver_insurance_periods` rows and `airport_fee` values in the DB/admin ride
detail afterward, instead of a silent gap.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/services/booking_import_service.py` | Added `_plan_insurance_periods()`, wired into both completed-ride branches; `commit_plan()` inserts the new rows; `airport_fee` now parsed from real charges instead of hardcoded 0.0; module docstring and `print_report()`/stats updated | Close the two gaps found in this session's migration-import audit — see Issue/gap above |
| `backend/tests/test_booking_import_service.py` | 5 new tests: Period 2+3 reconstruction, missing-`arrived_at` exclusion, unmatched-driver exclusion, `airport_fee` populated/zero cases, `commit_plan` writes to `driver_insurance_periods` | Regression coverage for both fixes |
| `backend/tests/test_booking_import_cancelled_failed.py` | 2 new tests: anomalous $0-fare branch gets Period 3 only (no `arrived_at` in that fixture), normal cancelled/failed rows never get insurance periods | Regression coverage for the two `status='completed'` branches plus the negative case |
| `docs/change-log/2026-08-24-legacy-import-insurance-periods-and-airport-fee.md` | This file | Change Impact & Risk Log, mandatory for a safety/insurance-adjacent live-tested surface |

## 7. Before / after

```python
# Before
"airport_fee": 0.0,  # legacy airport charges ride in area_fees
...
plan.rides_to_insert.append(ride)
# (no driver_insurance_periods row ever written for this ride)
```

```python
# After
airport_fee = parse_money(b.get("airport_pickup_charges", "")) + parse_money(b.get("airport_drop_charges", ""))
...
"airport_fee": float(airport_fee),
...
plan.rides_to_insert.append(ride)
_plan_insurance_periods(plan, ride, driver_id, arrived_at, started_at, completed_at)
```

## 8. Rollback plan

- **Code:** `git revert` is sufficient and complete — both changes are additive-only
  code paths in an admin-triggered offline import tool, not a live-tested request path.
  No feature flag exists or is needed (the tool is already gated behind an admin-only
  route with no automatic/scheduled trigger).
- **Data already written by a future import run before a revert:** the
  `driver_insurance_periods` rows this writes are append-only (migration 64's
  immutability trigger) and marked `is_reconstructed = true`, identical in shape to
  migration 332's own backfill rows — if a specific batch's reconstruction is later
  found wrong, the same remediation path migration 332's own rollback comment
  describes applies: `DELETE FROM driver_insurance_periods WHERE is_reconstructed = true
  AND ride_id IN (...)`, scoped to the specific batch's ride ids. `airport_fee` values
  written can be corrected with a simple `UPDATE rides SET airport_fee = ... WHERE id =
  ...` per affected row if ever needed — no destructive action required either way.

## 9. Verification performed

- [x] Automated tests run — unit only: `pytest backend/tests/test_booking_import_service.py
  backend/tests/test_booking_import_cancelled_failed.py
  backend/tests/test_booking_import_rides_numeric_no_float_cast.py
  backend/tests/test_admin_booking_import.py -q --no-cov` → 114 passed, 0 failed, 0
  regressions (77 in the two directly-modified files, including 7 new).
- [x] Blast-radius grep performed — searched for every reader of
  `driver_insurance_periods` (only `record_period_transition`, confirmed not reusable
  for historical backfill) and every reader of `rides.airport_fee` (7 files, all plain
  numeric reads, listed in section 4).
- [x] Reviewed against relevant `CLAUDE.md` convention — insurance-period rules (Period
  2 starts at `driver_arrived_at`/is a disclosed approximation, matches the existing
  migration-332 precedent explicitly; append-only `driver_insurance_periods` never
  mutated, only inserted), Decimal-only money math (`parse_money` returns `Decimal`,
  cast to `float` only at the JSON-serialization boundary, same pattern as every other
  field in this file).
- [ ] Manual repro steps followed in staging — **not done**. No real Supabase/staging
  environment access from this session; verified only against the file's existing
  in-memory fake-Supabase-client test harness. A real run against a database with
  migration 332 applied (to confirm the `is_reconstructed` column exists and the insert
  succeeds against real RLS/constraints) has not been performed.
- [ ] Feature-flagged — not applicable, this is an admin-only offline tool with no
  live-traffic exposure.

## What was NOT verified

- No real Supabase/Postgres run — the `driver_insurance_periods` insert's actual
  behavior against the live schema (RLS policies, the `period_3_requires_ride` CHECK
  constraint, the partial unique index) is exercised only by the in-memory fake in
  tests, not a real database.
- Whether any *already-imported* rides between migration 332 (2026-08-18) and this fix
  landing also lack insurance-period rows is unconfirmed — this fix only changes what a
  *future* import run writes; a residual gap for that window (if any bookings were
  imported in it) would need its own targeted backfill, not covered here.
- `python -m backend.server` / a real production build was not run (backend-only Python
  change; no admin-dashboard/rider-app/driver-app build applicable).

## 10. Sign-off

- [x] Rollback plan is concrete and testable — plain `git revert` for code; scoped
  `DELETE`/`UPDATE` for any already-written data, matching migration 332's own
  precedent.
- [x] Blast radius is stated, not assumed — isolated to this importer's own write path;
  every other reader of the two affected columns/table enumerated above.
- [x] No silent behavior change to an already-shipped flow — this is a previously-inert
  code path (the importer's insurance-period/airport-fee gap) becoming correct, not a
  change to any live/already-working flow.
