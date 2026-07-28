# Spinr Risk Log (Recon Audit)

Read-only static audit, 2026-07-25. Companion to `module-map.md` and
`trip-state-machine.md`. Findings are tagged **severity**: high / medium / low,
each with `file:line`. This is a sampled pass over a large codebase (317
migrations, ~150 test files) - treat as a prioritized starting point, not an
exhaustive inventory.

---

## 1. Duplicated ride-state-guard logic (4+ independent implementations)

**Severity: high** - the single most repeated pattern-inconsistency in the codebase, directly touching the ride state machine invariants CLAUDE.md calls out as mandatory.

There are at least four separately-written "check current status, then atomically transition" implementations, each with subtly different exception types and race-guard shapes:

1. `backend/routes/rides/_shared.py:290` - `_require_ride_in_state_rider` (rider-side), raises `SpinrException`/`RideNotFoundException`.
2. `backend/routes/drivers/_shared.py:558` - a second, near-identical `_require_ride_in_state` (driver-side) with the same docstring/structure but raises `HTTPException` directly instead of the shared exception hierarchy.
3. `backend/routes/rides/lifecycle.py:82-100,145-161` - `rider_start_ride`/`rider_complete_ride` do not call either helper above; they inline a `ride.get("status") != X` check followed by a `.update(...).eq("status", expected)` atomic guard.
4. `backend/routes/drivers/ride_complete.py:621` - driver-side complete uses yet another inline filter-based atomic guard, also bypassing `_require_ride_in_state`.
5. `backend/utils/stuck_ride_sweeper.py:60-62` - a raw Supabase `.eq("status","searching").lt("ride_requested_at", cutoff)` claim, written directly against the Supabase client rather than through `db_supabase.py`/`db` helpers, bypassing both guard functions entirely.

Risk: any future change to the state-machine invariants has to be found and updated in 4-5 places by hand; the driver-side guard raising a different exception type than the rider-side one means error responses to driver vs rider clients are inconsistent for the same underlying failure mode. Recommendation: consolidate around one canonical guard + one canonical atomic-update helper.

## 2. String-literal status write bypasses the RideStatus enum

**Severity: medium**

`backend/services/dispatch_service.py:448` - `DispatchService.assign_driver_to_ride` writes the literal string `"driver_assigned"` rather than `RideStatus.DRIVER_ASSIGNED.value` (enum defined at `backend/models/ride_status.py`, used elsewhere e.g. `utils/scheduled_rides.py`, `routes/rides/cancellation.py`). A future rename/refactor of the enum values could silently desync this call site with no type-checker signal. No test found asserting the literal matches the enum.

## 3. Three independent _d/_round Decimal-helper definitions (money handling not centralized)

**Severity: medium**

`backend/utils/money.py` (55 lines, fully read) is the documented canonical Decimal helper module and explicitly documents the `Decimal(str(x))` vs `Decimal(x)` float-drift pitfall. However:

- `backend/services/fare_service.py` (top of file) defines its own local `_d`/`_round`/`_f`/`_fd`, not imported from `utils/money.py`.
- `backend/routes/rides/_shared.py:324-330` defines a third local `_d`/`_round`.
- `backend/services/payment_service.py` (top of file) defines a fourth set: `_d`/`_round`/`_f`/`_money_str`.

No call site found this pass imports `utils/money.py`. Functionally the reimplementations look similar, but a fix to the float-drift guard in one file won't propagate to the others. Recommendation: converge all three on `utils/money.py`, or confirm/remove it if genuinely unused.

## 4. Float arithmetic occurring mid-calculation, not purely at serialization boundary

**Severity: medium**

`utils/money.py`'s own stated design intent is that float conversion should happen only at serialization boundaries, not during money math. Sampled violations:

- `backend/routes/rides/_shared.py:608` - `ride_fare = float(base + dist_surged + time_surged + uplift)` - sums Decimal fare components then converts to float **before** further capping logic (`capped_discount = min(raw_discount, ride_fare)` at line 609) - the promo-discount cap math runs in float space.
- `backend/routes/rides/_shared.py:599-611` - `raw_discount = float(ride["discount_amount"])`, then `min(raw_discount, ride_fare)`, with the result written directly into the fare-breakdown response as `-capped_discount` (line 609) without re-quantizing through `_round`.
- `backend/routes/rides/queries.py:562-572` - builds a running `tax` total via `tax += float(ln.get("amount") or 0)` inside a loop (566-572) - summation happens in float space rather than Decimal-then-convert-once.
- `backend/routes/rides/matching.py:755` - `ba = float(inc.get("bonus_amount") or 0)`, used in subsequent incentive-total arithmetic (not fully traced past this sampled pass).

These are read-path/receipt-display cases (no direct ledger-write corruption confirmed), which is why severity is medium not high, but they are borderline violations of the stated Decimal-only convention. Recommend a `spinr-money-auditor` pass on `_shared.py:560-612` and `queries.py:500-575`.

## 5. backend/features.py - legacy duplicate ride-status writes alongside routes/rides/ package

**Severity: medium**

`backend/features.py:461,1174,1205,1877,1893` writes `RideStatus.IN_PROGRESS`/`SCHEDULED`/`CANCELLED`/`SEARCHING` directly - a "legacy/shared grab-bag module" (per module-map.md) coexisting with the newer, purpose-split `routes/rides/` package. Two write paths for the same state machine increase the chance a future invariant change is applied to one path and not the other. Needs a follow-up to confirm whether these write paths are still reachable in production routing or are vestigial.

## 6. Inconsistent exception-handling style across trip-lifecycle code

**Severity: low-medium**

The driver-side vs rider-side state-guard divergence in Finding 1 (`HTTPException` directly vs `SpinrException`/`RideNotFoundException`) is itself evidence of inconsistent error-handling convention within the same domain. No violation of "do not silently swallow errors" was found in files read this pass - the one intentional swallow (`utils/insurance_periods.py`, DB-write failure on period-transition audit rows) is explicitly documented as a reasoned compliance trade-off, not an oversight. Recommend a dedicated grep for bare `except:`/`except Exception:` blocks across `backend/routes/` and `backend/services/` as a follow-up (not completed this pass).

## 7. backend/routes/admin/rides.py - largest file in the tree (3430 lines), only sampled

**Severity: medium**

Given its size and that it's an admin-only surface with override capability over the trip state machine, this file was only grep-sampled for status literals, not read end-to-end. It is a plausible location for a fifth variant of Finding 1's pattern, and for the "at most one active ride" invariant to be skipped for admin overrides (which may be by design, but that exemption needs explicit verification). Flagged as a required follow-up read, not confirmed as a live bug.

## 8. Thin test coverage: routes/drivers/ride_flow.py

**Severity: medium**

`ride_flow.py` (accept/arrive/start - exactly the transitions CLAUDE.md's insurance-period table maps to Period 2, TNC primary commercial liability) is referenced by only 1 test file this pass, versus 8-20+ for `dispatch_service.py`, `lifecycle.py`, `payments.py`, `payment_service.py`, `webhooks.py`. This is a coverage gap on a regulatory-adjacent code path. Recommend an explicit test (or extending `test_ride_state_machine.py`) covering accept -> arrive -> start with the race-guard conditions from Finding 1.

## 9. GPS/location integrity check ordering

**Severity: low**

`backend/routes/drivers/location.py:329-359` (`update_location_batch`) selects `points[-1]` as the authoritative point before `check_location_integrity` is invoked (import at line 338, called after line 358). Not fully traced whether a failed integrity check actually blocks the write or only annotates it. Test coverage exists (`test_location_batch.py`, `test_p3_background_location.py`, `test_drivers_extended.py`), so this is a "verify the assertion path" item, not a coverage gap.

## 10. utils/offer_expiry_reaper.py - inverted dependency (utils -> routes)

**Severity: low**

`backend/utils/offer_expiry_reaper.py` imports and calls `routes/rides/matching.py`'s `process_expired_offer` - utils importing from routes is backwards for a typical layered architecture, self-documented as deliberate in the reaper's own docstring. Flagged for architectural awareness only, not a bug.

## 11. Notification/live-activity send guarantee not verified across all 5 transition-writing sites

**Severity: low**

`backend/utils/live_activity.py`'s `send_live_activity_update` is, per module-map.md, "called at nearly every status transition point" - but with 4+ independent state-transition implementations (Finding 1), it was not verified whether every one of those call sites correctly fires the WS event + push/live-activity update exactly once per transition, as CLAUDE.md requires. This is a plausible consequence of Finding 1 rather than an independently confirmed bug.

## 12. backend/utils/money.py appears to have zero production importers

**Severity: low**

No call site found this pass imports `utils/money.py` despite it being the apparent canonical Decimal helper (see Finding 3). If confirmed dead in a full-tree follow-up grep, this is either genuinely dead code that should be removed, or evidence the "canonical" designation is aspirational and the three duplicate implementations are the de facto standard.

## 13. Race-condition surface: driver location writes vs. dispatch claim

**Severity: low-medium**

`backend/services/dispatch_service.py`'s `claim_driver`/`claim_any_driver` perform an atomic `is_available=True->False` claim, but driver location writes (`routes/drivers/location.py:update_location_batch`) are a separate, unsynchronized write path against the same `drivers` row. Not confirmed as a live bug (no evidence of a column-level conflict - location fields and availability fields are logically disjoint), but flagged because CLAUDE.md's `is_available => is_online` invariant depends on multiple independent writers (`go_online`/`go_offline`, dispatch claim, presence heartbeat) touching the same row; a follow-up should confirm no code path updates `is_online`/`is_available` from the location-update handler in a way that could race with a concurrent dispatch claim.

---

## Test-coverage cross-check summary (money / auth / trip-state)

Per-file test-file-reference counts (rough proxy for coverage breadth, not depth):

| File | Referencing test files (approx) | Note |
|---|---|---|
| `services/payment_service.py` | 17 | Reasonable breadth |
| `routes/payments.py` | 21 | Reasonable breadth |
| `routes/webhooks.py` | 10 | Reasonable breadth |
| `services/dispatch_service.py` | 8 | Moderate |
| `routes/auth.py` | 123 | Very high (touched by most integration tests) |
| `routes/rides/lifecycle.py` | 20 | Reasonable breadth |
| `routes/drivers/ride_flow.py` | 1 | Thin - see Finding 8 |
| `utils/insurance_periods.py` | 3 | Thin given regulatory significance; module is small (164 lines) so may be adequate - flag for follow-up |
| `services/corporate_wallet_service.py` | 6 | Moderate |

No Stripe webhook type was checked individually against "every Stripe webhook type before hitting production" (CLAUDE.md testing convention) - recommend a dedicated pass enumerating `routes/webhooks.py` event-type branches against `test_webhooks_main.py`/`test_stripe_*` coverage; not completed here due to scope.
