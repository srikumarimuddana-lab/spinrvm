# Spinr Findings / Risk Log (Recon Audit)

Severity scale: **high** (correctness/compliance/money risk, or a bug class
that could recur), **medium** (maintainability/consistency risk, plausible
but not confirmed to have caused an incident), **low** (cosmetic / minor).

All line numbers are from files actually opened with Read or matched with
Grep in this session (paths relative to `/home/user/spinrvm`).

---

## 1. Duplicated ride-state-guard logic (four independent implementations) — HIGH

The single highest-value finding. The "load ride, verify current status is
in an allowed set, raise 409/404 otherwise" guard is implemented **four
separate times** with subtly different behavior:

1. `backend/routes/rides/_shared.py:290` — `_require_ride_in_state_rider(ride_id, rider_id, allowed_states)`. Raises `SpinrException`/`RideNotFoundException` (structured error codes, `ErrorKeys.RIDE_INVALID_STATUS`).
2. `backend/routes/drivers/_shared.py:558` — `_require_ride_in_state(ride_id, driver_id, allowed_states)`. Nearly identical docstring and structure to (1), but raises plain `HTTPException` with a free-text detail string — no structured error code.
3. `backend/routes/rides/lifecycle.py:82-100,145-161` — `rider_start_ride` and `rider_complete_ride` do **not** call either helper above. Each hand-rolls: `if ride.get("status") != RideStatus.X: raise HTTPException(400, ...)` followed by a separate atomic `update_one(filters={"status": X}, ...)` whose `None` result triggers a **409** with yet another wording ("Ride is not in driver_arrived state" / "Ride is not in progress"). This means the 400-vs-409 status code and message format for what is conceptually the same guard differs between this file and (1)/(2).
4. `backend/utils/stuck_ride_sweeper.py:57-65` — a raw Supabase query `.eq("status", "searching").lt("ride_requested_at", cutoff_iso)` used as an atomic claim, executed directly against the `supabase` client rather than through `db_supabase`/`db` abstraction layers used everywhere else.

Risk: any future change to state-machine semantics (e.g., adding a state, or
changing which prior states permit a transition) has four call sites to find
and update, with three different error-shapes for API clients to handle.
`routes/drivers/ride_complete.py:621` (`_complete_filters = {"id": ride_id,
"driver_id": driver["id"], "status": RideStatus.IN_PROGRESS}`) is a fifth
inline-filter pattern, reinforcing that the atomic-claim idiom is copy-pasted
rather than centralized.

Recommendation: extract one shared state-transition guard (or extend
`_require_ride_in_state`/`_require_ride_in_state_rider` to also perform the
atomic transition, returning a consistent exception type) and have
`lifecycle.py` and `ride_complete.py` call it instead of re-deriving the
pattern.

## 2. `assign_driver_to_ride` uses a bare string literal instead of `RideStatus` enum — MEDIUM

`backend/services/dispatch_service.py:448`:
```
"status": "driver_assigned",
```
Every other status-mutation site sampled in this audit uses
`RideStatus.DRIVER_ASSIGNED` etc. (see the grep hits catalogued in
module-map.md — `routes/rides/matching.py:1032`, `routes/drivers/ride_flow.py:212`,
`routes/rides/cancellation.py:95`, and 40+ others). A future rename or typo
of the literal string won't be caught by any type check that would catch a
misuse of the enum member (RideStatus is `str, Enum` so equality still works
today, but the inconsistency is a correctness landmine for anyone doing a
grep-based refactor of status values).

## 3. Three parallel "Decimal money helper" definitions instead of one — MEDIUM

`backend/utils/money.py` (`to_decimal`, `dollars_to_cents`, `cents_to_dollars`)
exists specifically, per its own docstring, "to prevent float drift" bugs.
However:
- `backend/services/fare_service.py:40-55` defines its own local `_d`, `_round`, `_f`, `_fd`.
- `backend/services/payment_service.py:39-51` defines its own local `_d`, `_round`, `_f`, `_money_str`.
- `backend/routes/rides/_shared.py:324-330` defines its own local `_d`, `_round`.

None of these three call into `utils/money.py`. They are functionally
similar (`Decimal(str(v))`, quantize to 2dp with `ROUND_HALF_UP`) but are
three independent sources of truth for the exact rounding/conversion
behavior CLAUDE.md calls out as previously having caused a real
undercharge bug (`money.py`'s own docstring: "Riders charged $29.98 instead
of $29.99 is a real bug we caught in payments.py"). If one of the three
copies is ever tweaked (e.g., rounding mode) without updating the other two,
fare, payment, and ride-adjustment math can silently diverge.

## 4. `float(...)` used mid-calculation, not only at serialization boundary — MEDIUM

`utils/money.py`'s own docstring implies `float()` should appear only when
handing a value back over JSON/HTTP ("if a float is needed at a
serialization boundary, the caller converts explicitly"). Several call
sites instead convert to `float` and then keep computing:

- `backend/routes/rides/_shared.py:608`: `ride_fare = float(base + dist_surged + time_surged + uplift)` inside fare-breakdown assembly (surrounding context lines 580-611); the float value participates in further logic (line 611 checks `float(ride["tip_amount"]) > 0` afterward) rather than being purely the final return value.
- `backend/routes/rides/_shared.py:357-374`: distance/time-fare math stays in Decimal (`_d`, lines 357-363) but line 374 passes `float(new_total)` into `_deps.calculate_all_fees(...)`, so an external fee/tax calculation runs on a float representation of a Decimal fare total.
- `backend/routes/rides/matching.py:792`: `_surge_mult = float(ride.get("surge_multiplier") or 1.0)`, feeding a float surge multiplier into later fare-adjacent logic in that function.

Not confirmed in this pass to have produced a wrong charge — flagged as
medium because CLAUDE.md states a pre-commit hook "blocking float
arithmetic in fare code," suggesting this is a known recurring risk area;
worth checking whether that hook's rule-set actually catches these lines.

## 5. `backend/features.py` still contains ride-status writes alongside the newer `routes/rides/` package — MEDIUM

`backend/features.py:461,1174,1205,1877,1893` writes `RideStatus.IN_PROGRESS`,
`SCHEDULED`, `CANCELLED`, `SEARCHING` directly. Given `routes/rides/` is
described in its own file docstrings as a "god-file split... pure code
motion" refactor, `features.py` looks like a pre-refactor leftover grab-bag
module that still independently mutates ride state outside the new package
structure. Two places evolving the same state machine is a duplication/
legacy-residue risk: it's easy for one to drift out of sync with the
guard/notification side-effects (WS emit, insurance period transition) that
CLAUDE.md requires on every status change.

Recommendation: confirm (follow-up read) whether `features.py`'s status
writes are dead/superseded code or still-live call paths, and whether they
also emit the required WS event + insurance-period transition.

## 6. Coverage gaps: services without a matching unit-test file under `backend/tests/services/` — MEDIUM

`backend/tests/services/` contains only:
`test_corporate_allowance_service.py`, `test_corporate_membership_service.py`,
`test_corporate_policy_service.py`, `test_corporate_wallet_service.py`,
`test_dispatch_service.py`, `test_fare_service.py`.

`backend/services/` has 16 non-`__init__` modules. The following have **no**
file in `tests/services/`:
`cancellation_service.py`, `company_booking_service.py`,
`driver_import_service.py`, `guest_notification_service.py`,
`guest_user_service.py`, `lms_service.py`, `marketing_consent.py`,
`payment_service.py`, `stripe_kyc_sync.py`, `zoho_desk_db.py`,
`zoho_desk_integration.py`, `zoho_desk_service.py`,
`zoho_ticket_service_area.py`.

Of these, `payment_service.py` (1032 lines, the largest service file) is the
most concerning given CLAUDE.md's stated ≥90% coverage minimum for
`routes/payments.py` / `services/fare_service.py` / `utils/crypto.py`. A
broader grep did find payment-related coverage scattered across top-level
`tests/test_p0_rating_and_payment.py`, `test_e4_d10_payment_3ds_quests.py`,
`test_cancellation_fee_card_charge.py`, `test_instant_payout.py`, and
others — so `payment_service.py` logic is very likely exercised indirectly
through route-level integration tests, but there is no dedicated
`tests/services/test_payment_service.py` unit-test file the way
`fare_service.py` and `dispatch_service.py` get. Not confirmed as an actual
coverage hole (only a naming/organization gap) — flagged for a human to
verify with actual `pytest --cov` output.

## 7. `stuck_ride_sweeper.py` bypasses the `db_supabase`/`db` abstraction — LOW/MEDIUM

`backend/utils/stuck_ride_sweeper.py:57-65` calls `supabase.table("rides")...`
directly (via the raw `supabase_client.supabase` import) rather than going
through `db_supabase.py`'s ~66 helper functions. The module does still route
the *execution* of that built query through `db_supabase.run_sync(_claim,
retry_policy="write")` (line 68), so the H2-GOAWAY retry behavior is
preserved — but the query-building bypasses whatever validation/consistency
helpers `db_supabase.py` centralizes for other callers. Low/medium because
the retry wrapper is still applied; flagged mainly as an inconsistency for
future maintainers expecting all Supabase table access to go through
`db_supabase.py`.

## 8. No stray TODO/FIXME/HACK markers found in `backend/` — LOW (informational)

A repo-wide grep for `TODO|FIXME|HACK` under `backend/` returned exactly one
hit: `backend/tests/test_p1_multi_stop.py:12` — a comment describing a test
marked `xfail` "so CI flags the gap as a living TODO." This is not a code
TODO but a deliberate, already-tracked test gap. No forgotten/stray
TODO-style debt markers were found in this pass (reassuring, not a defect).

## 9. Rider-guard vs driver-guard exception-type inconsistency — MEDIUM (subset of #1, API-contract-visible)

Rider-side 409s raise a structured `SpinrException` with `error_code`,
`details`, and `message_key` (`backend/routes/rides/_shared.py:305-311`).
Driver-side 409s raise a plain `HTTPException(status_code=409, detail=f"...")`
with no structured error code (`backend/routes/drivers/_shared.py:571-579`).
If either client (rider app vs driver app) relies on a structured
`error_code` field to branch UI behavior on a 409, the driver app receives a
strictly less structured payload for the equivalent failure mode. Confirm
with frontend code whether this asymmetry is already compensated for
client-side.

## 10. Files confirmed to read/write `ride.status` (compiled for downstream reference)

Compiled from Grep hits actually returned in this session (not
exhaustive — a small number of `admin/rides.py` and `features.py` sites were
sampled rather than individually enumerated):

- `backend/routes/rides/{booking,lifecycle,cancellation,matching,queries}.py`
- `backend/routes/rides/_shared.py` (`_require_ride_in_state_rider`)
- `backend/routes/drivers/{_shared,ride_flow,ride_cancel,ride_complete,earnings,referrals,payouts,subscriptions}.py`
- `backend/services/dispatch_service.py` (`assign_driver_to_ride`)
- `backend/utils/{offer_expiry_reaper,stuck_ride_sweeper,scheduled_rides,spinr_pass}.py`
- `backend/features.py`
- `backend/routes/{promotions,admin/rides}.py`
- Tests: `backend/tests/test_ride_state_machine.py`, `test_e2e_ride_lifecycle.py`, `test_coverage_rides.py`, `test_scheduled_dispatch_cr.py`, `test_p2_scheduled_rides.py`, and ~15 more (see module-map.md).

This list itself is evidence for findings #1 and #5 — the number of
distinct files independently touching `ride.status` is large enough that a
single centralized transition function (even if just a thin wrapper
enforcing the guard + WS-emit + insurance-period-transition triple that
CLAUDE.md mandates on "every state change") would materially reduce the
risk surface.
