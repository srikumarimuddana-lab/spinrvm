# Change Impact & Risk Log — Pickup OTP brute-force hardening

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-05 |
| Author | Claude Code (agent session) |
| Surface(s) | backend |
| Domain (Sentry tag) | rides (secondary: safety, drivers) |
| PR / commit link | branch `claude/pickup-otp-payment-fixes-5a8dnk` |
| Related issue or gap ID | `docs/audit/2026-09-05-engineering-director-review-round3.md` §1.6 (critical #6); prior `SPINR_CODE_REVIEW.md` major #35 |

## 1. Issue / gap identified

`POST /drivers/rides/{id}/verify-otp` had no per-ride attempt limit, no input
validation, and crashed on a NULL stored code. The only limiter was the global
100/minute-per-IP default, so the **assigned driver** could brute-force the
4-digit pickup OTP in roughly 50 minutes from one IP and flip the ride to
`in_progress` — opening Insurance Period 3 (passenger aboard, full TNC
commercial coverage) and starting the meter with no rider in the car.

Found while fixing the above: `POST /rides/{id}/start` (`routes/rides/lifecycle.py`)
let the assigned driver make the same `driver_arrived → in_progress` transition
with **no OTP at all and no production gate**. Its sibling
`POST /drivers/rides/{id}/start` has been 410-in-production since it was added;
this twin was not. That bypass made any attempt limit on verify-otp moot, so it
is closed in the same change.

## 2. Root cause

Three independent gaps, all in the same request path:

1. `RideOTPRequest.otp: str` carried no constraint, so `""`, non-digits and
   arbitrary-length strings all reached the comparison.
2. No failure counter existed anywhere — no `otp_attempt` / `pickup_otp_fail`
   key in Redis, no column, nothing. The rider-facing phone OTP has had a
   lockout since A-P3-2; the pickup OTP never got the equivalent.
3. `ride.get("pickup_otp", "")` returns `None` (not `""`) when the column is
   SQL NULL, because the key *exists* with a NULL value. `hmac.compare_digest(None, ...)`
   raises `TypeError` → unhandled 500. And when the stored value was genuinely
   `""`, an empty submitted OTP compared equal — a legacy/admin-created ride
   could be started with no code at all.

The `/rides/{id}/start` bypass is a router-split artefact: the production gate
was added to the `drivers` copy of the route and never mirrored onto the `rides`
copy.

## 3. Fix / remediation

- `RideOTPRequest.otp` is now `Field(min_length=4, max_length=6, pattern=r"^\d+$")`.
  The max is deliberately looser than the 4-digit generator so lengthening the
  code later needs no coordinated client release; the digit-only pattern is the
  security-relevant half.
- A per-ride Redis counter (`spinr:ride:{id}:otp_fail`) locks the ride's OTP
  (`spinr:ride:{id}:otp_lock`) for 15 minutes after 5 wrong codes, emits
  `spinr_rides_pickup_otp_lockout_total`, logs at `error` (→ Sentry) and pushes
  a WS + push notification to the **rider**, who is the party whose code is
  being probed and the only one able to react.
- A NULL/blank stored `pickup_otp` is now a 409 with an `error`-level log,
  never a 500 and never an accidental match.
- `rider_start_ride` gains the same production 410 its `drivers` twin has.

Keyed per **ride**, not per driver: a ride is a one-shot object with its own
code, so 5 attempts per ride makes targeted brute force infeasible
(~10,000 codes ÷ 5 per 15 min ≈ 500 hours) while a driver who fat-fingers one
ride's code is unaffected on the next.

## 4. Risk & impact on existing functionality

**Blast radius: isolated to the pickup-verification path.** Greps performed:

- `RideOTPRequest` — 5 hits: the definition, the `drivers/__init__.py` re-export
  pair, and the single handler signature. No other producer or consumer.
- `pickup_otp` (non-test) — 16 hits. Readers are unaffected by this change:
  `services/guest_notification_service.py:180,213,223` (SMS the code to guest
  riders), `services/company_booking_service.py:172` and
  `dependencies/__init__.py:67` (generation), `routes/rides/lifecycle.py:67`
  (returns the code to the rider on arrive), `routes/rides/queries.py:536` and
  `routes/drivers/_shared.py:705` (strip it from responses). None of them write
  the column or verify the code.
- `rider_start_ride` — 3 hits, all the definition plus its `__init__` re-export
  pair. No internal caller.
- Both mobile apps: `driver-app/store/driverStore.ts:699,716` call
  `/drivers/rides/{id}/verify-otp` and `/drivers/rides/{id}/start`. **Neither
  app calls `/rides/{id}/start`**, so gating it changes no shipped client flow.

Interactions considered:

- **Ride state machine** — unchanged. The CAS at the transition is untouched;
  the new checks all run *before* it, and the new 409 path asserts
  `update_one` is never reached (regression test included).
- **Insurance periods** — strictly fewer Period 3 rows, never more: every new
  code path returns before `record_period_transition(..., 3, ...)`. No period
  row is deleted or mutated (append-only rule respected).
- **Background loops** — none read these Redis keys; the namespace
  `spinr:ride:{id}:otp_*` is new and grepped as unused.
- **Redis fallback** — with `REDIS_URL` unset the client uses an in-process
  dict (CLAUDE.md "Redis transparency"), so in that mode the counter is
  per-replica and lost on restart. That weakens but does not remove the control,
  and matches how the existing phone-OTP lockout already behaves.

Regression risk, stated plainly: **a real rider/driver pair who mistype the
code 5 times are locked out of starting their trip for 15 minutes.** That is the
intended trade-off, and it is why the lockout is 15 minutes rather than the
24 hours the phone OTP uses. Their remedy is to wait, or cancel and rebook.

## 5. User-experience effect

- **Driver (visible, mid-session):** a 5th wrong pickup code now returns 429
  "Too many incorrect pickup codes for this ride — try again later" instead of
  another 400 "Invalid OTP", and further attempts are refused for 15 minutes
  *even if the code is correct*. Submitting a non-digit or wrong-length code now
  fails client-side validation (422) instead of reaching the server as a 400 —
  the driver app's numeric OTP input already only produces 4 digits, so this is
  not reachable through the shipped UI.
- **Rider (visible, mid-session):** a **new** push + WS notification
  (`pickup_otp_locked`), "Pickup code locked 🔒 / Your driver entered the wrong
  pickup code too many times. Check you are with the right driver — you can
  cancel if something feels wrong." Specific, non-technical, actionable, and it
  deliberately does **not** echo the code (PIPEDA).
- **Nobody** sees a difference on the `/rides/{id}/start` gate — no client calls it.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/drivers/_shared.py` | `RideOTPRequest` gains digit/length `Field` constraints; adds `pickup_otp_locked_exc`, `check_pickup_otp_lockout`, `record_pickup_otp_failure`, `clear_pickup_otp_failures` and their tuning constants | Field validation + the per-ride lockout primitives, kept beside the model they guard |
| `backend/routes/drivers/ride_flow.py` | `verify_pickup_otp` checks the lockout, 409s on a missing stored code, records failures and clears on success; adds `_notify_rider_pickup_otp_lockout` | Wires the lockout into the only production start path |
| `backend/routes/rides/lifecycle.py` | `rider_start_ride` gains the production 410 gate | Closes the OTP-free bypass that made the lockout moot |
| `backend/tests/test_pickup_otp_bruteforce_lockout.py` | New | Pins all three gaps + the bypass gate |

## 7. Before / after

```python
# Before — routes/drivers/ride_flow.py
stored_otp = ride.get("pickup_otp", "")          # None when the column is NULL
if not hmac.compare_digest(stored_otp, request.otp):   # TypeError -> 500
    raise HTTPException(status_code=400, detail="Invalid OTP")   # unlimited retries
```

```python
# After
await check_pickup_otp_lockout(ride_id)          # 429 while locked

stored_otp = ride.get("pickup_otp") or ""
if not stored_otp:
    logger.error("verify_pickup_otp: ride %s has no pickup_otp; ...", ride_id)
    raise HTTPException(status_code=409, detail="This ride has no pickup code — contact support to start it")

if not hmac.compare_digest(stored_otp, request.otp):
    failures = await record_pickup_otp_failure(ride_id, driver["id"])
    if failures >= _PICKUP_OTP_MAX_FAILURES:
        _notify_rider_pickup_otp_lockout(ride)
        raise pickup_otp_locked_exc()
    raise HTTPException(status_code=400, detail="Invalid OTP")

await clear_pickup_otp_failures(ride_id)
```

```python
# Before — routes/rides/lifecycle.py::rider_start_ride
"""Start a ride. Restricted to the assigned driver only (R-P1-17)."""
if not current_user.get("is_driver"):        # no OTP, no production gate
    raise HTTPException(status_code=403, detail="ERR_DRIVER_ONLY")
```

```python
# After
if _settings.ENV.lower() == "production":
    raise HTTPException(status_code=410, detail="Use POST /drivers/rides/{ride_id}/verify-otp ...")
if not current_user.get("is_driver"):
    raise HTTPException(status_code=403, detail="ERR_DRIVER_ONLY")
```

## 8. Rollback plan

No migration, no schema change, no live-data mutation — nothing is written that
would need data-level remediation, so a code revert **is** a complete rollback
here (the only durable side effects are Redis keys with their own 15-minute /
1-hour TTLs, which expire on their own).

Without a redeploy, an operator can neutralise the lockout immediately by
deleting the keys: `DEL spinr:ride:<id>:otp_lock spinr:ride:<id>:otp_fail`
(or `spinr:ride:*:otp_*` to clear all), which unblocks any stuck ride within one
request. There is deliberately **no** feature flag: the change removes an
unbounded-brute-force hole, and a flag to turn that back on is a hole of its own.

## 9. Verification performed

- [x] Blast-radius grep performed — `RideOTPRequest`, `pickup_otp`,
      `rider_start_ride`, `spinr:ride:` key namespace, and both mobile apps'
      call sites (listed in §4).
- [x] Reviewed against `CLAUDE.md` conventions: ride state machine (CAS
      untouched, new returns all precede it), insurance periods (append-only,
      strictly fewer Period 3 rows), PIPEDA (no code/PII in logs, metrics or
      notification payloads), observability (metric named
      `spinr_rides_pickup_otp_lockout_total`, security event at `error` level,
      stdlib `logging` in this package so `%s` placeholders are correct — not
      loguru).
- [x] `ruff check` and `ruff format --check` clean on all four files.
- [ ] **Automated tests NOT run** — see §"What was not verified".
- [ ] Not feature-flagged — justified in §8.
- [ ] Not manually reproduced in staging.

## What was NOT verified

**The test suite was not executed.** This environment's network policy blocks
PyPI (`https://pypi.org/simple/` returns 403), so `fastapi`, `pydantic` and the
rest of `backend/requirements.txt` could not be installed and no `pytest` run
was possible. `backend/tests/test_pickup_otp_bruteforce_lockout.py` is written
but **has never been run**, and the pre-existing
`test_driver_ride_flow_coverage.py::TestVerifyPickupOtp` cases were not re-run
against these changes either — in particular `test_400_when_otp_mismatch` now
also exercises the new counter, and its shared `_RIDE_ID` could interact with the
in-process Redis fallback across tests in the same session. **Both files need a
real `pytest` run before merge.**

Also not verified: no staging repro; no live Redis exercised (only the
in-process fallback path is reachable in a local run); the driver app's 429
handling for this new status on the verify-otp call was reasoned about from
`getApiErrorMessage(err, 'Invalid OTP')` in `driver-app/store/driverStore.ts:696-711`
(it surfaces the server `detail`, so the lockout copy reaches the driver) but
not exercised on a device; and no visual-regression tooling exists for
driver-app at all, though this change adds no driver-app UI.
