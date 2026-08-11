# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-11 |
| Author | Claude Code (spinr platform) |
| Surface(s) | backend, rider-app |
| Domain (Sentry tag) | rides |
| PR / commit link | (this branch) |
| Related issue or gap ID | Scheduled-ride audit (`spinr-dispatch-reviewer`), P1 finding #4 |

## 1. Issue / gap identified

The backend's DST spring-forward-gap and fall-back-ambiguity guard for scheduled rides (`CreateRideRequest.validate_scheduled_time`) only runs when the request includes a `scheduled_timezone` field. The rider app never sent one — confirmed by grep, zero matches in `rider-app/`. A rider picking a DST-ambiguous or non-existent local time got silently resolved to whichever instant the device's `Date` construction defaulted to, with no warning, no rejection, and no record of which interpretation was chosen — the exact regulatory concern the guard was built to close (an undetermined trip-log instant) was live for every real booking.

While implementing the rider-app fix, a second, more severe bug was found and fixed in the same change: the backend validator's own contract requires `scheduled_time`'s raw digits to represent the rider's **local** wall-clock time (not a pre-converted UTC instant) — that's the only way to detect an ambiguous/non-existent local time at all, since a UTC instant has already had that ambiguity silently resolved by the client before it ever reaches the server. But even when `scheduled_timezone` **was** supplied (e.g. in existing tests), the validator passed DST-safety and then returned the **local digits mislabeled as a UTC instant**, unconverted — meaning any real caller who wired up `scheduled_timezone` correctly per the validator's own contract would have gotten a ride dispatched at the wrong real-world time, off by the zone's UTC offset (up to 7 hours for Saskatchewan).

## 2. Root cause

`scheduled_timezone` was added to `CreateRideRequest` and the DST-guard logic in an earlier gap-review pass (PR #3283), but two things were missed:

1. No caller was ever wired up to actually send `scheduled_timezone` — the field existed and was tested directly against the Pydantic model, but never reached from the real rider-app client (confirmed: the rider app calls `scheduledTime.toISOString()`, which always includes a `Z` UTC suffix, so `scheduled_timezone` was never populated and the whole guard branch was dead code in production).
2. The validator's own return statement was never updated to convert the validated local time to the true UTC instant it represents — `grep` of `backend/` outside `schemas.py` for `scheduled_timezone` returns zero results, confirming nothing downstream (storage, dispatch) ever re-derives a corrected UTC value from it either.

## 3. Fix / remediation

**Backend (`backend/schemas.py`)**: `validate_scheduled_time` now converts the validated local wall-clock time to its true UTC instant (`local.astimezone(utc_tz)`) and returns *that*, instead of the local digits mislabeled as UTC. The lead-time/advance-window checks were moved to run against this corrected value too (previously they ran against the mislabeled value, which for a Saskatchewan-zone booking could wrongly reject a valid near-term booking as "too soon," or wrongly accept one that was actually outside the window, by up to 7 hours). Behavior for callers that don't send `scheduled_timezone` (the sole real caller today, until this fix) is completely unchanged — the fix only activates on the `scheduled_timezone`-present branch.

**Rider app (`rider-app/store/rideStore.ts`)**: `createRide` now sends `scheduled_time` as the rider's **local** wall-clock digits (via a new `formatLocalNaiveIso` helper — the Date's `getFullYear`/`getMonth`/.../`getSeconds`, not `toISOString()`'s UTC conversion) plus a new `scheduled_timezone` field populated from `Intl.DateTimeFormat().resolvedOptions().timeZone` (the device's own IANA zone — correct for the normal case of booking a pickup where the rider currently is).

**Known remaining gap, not fixed here**: `backend/ai/tools_booking.py`'s own separate `_validate_scheduled_time` (used by the AI booking assistant) has no timezone parameter or DST logic at all. This was already a documented gap before this fix (`docs/change-log/2026-08-02-scheduled-rides-track-a-10-dst-fallback.md`'s own risk section) and is out of scope for this P1, which was specifically about the rider-app client path.

## 4. Risk & impact on existing functionality

- **Blast radius, backend**: `validate_scheduled_time` is a Pydantic field validator on `CreateRideRequest`, used by every ride-creation entry point (`routes/rides/booking.py`'s `create_ride`, and indirectly anything constructing this model). Grepped for other `scheduled_timezone` readers — none exist outside this validator, so no other code path is affected by the corrected return value; every consumer of `body.scheduled_time` downstream (ride storage, the scheduled dispatcher) simply receives a *more correct* value when a timezone was supplied, and an *identical* value to before when it wasn't.
- **Blast radius, rider-app**: `formatLocalNaiveIso`/`scheduled_timezone` are only added to the scheduled-ride branch of `createRide`'s payload construction (`if (scheduledTime) { ... }`) — immediate (non-scheduled) ride creation is untouched.
- **This is a real behavior change for `scheduled_time`'s wire format**, not purely additive: any OTHER caller of `POST /rides` that already sends its own `scheduled_timezone` (none currently exist in production, confirmed) would see the returned/stored value change from "local digits mislabeled as UTC" to "the correct UTC instant" — a correctness fix, not a regression, since the former was never right.
- **No interaction with the ride state machine, dispatch atomic claim, or money paths.** This only affects what UTC instant gets stored in `rides.scheduled_time` at booking time.

## 5. User-experience effect

- **Riders**: booking a scheduled ride for a DST-ambiguous or non-existent local time (relevant only in Lloydminster, the one part of Spinr's SK service area that observes DST) now correctly gets rejected with a clear message ("does not exist" / "is ambiguous") instead of silently dispatching at an undetermined instant. For every other booking (the overwhelming majority, in non-DST-observing SK zones or non-ambiguous times), there is no visible change — the ride dispatches at exactly the local time picked, same as before.
- Not visible mid-session; only affects the moment a scheduled ride is booked.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/schemas.py` | `validate_scheduled_time`: converts to true UTC after DST validation passes (when `scheduled_timezone` given); window checks moved to run against the corrected value | Fix a latent wrong-dispatch-time bug that would have shipped the moment any caller correctly used `scheduled_timezone` |
| `rider-app/store/rideStore.ts` | New `formatLocalNaiveIso` helper; `createRide` sends local digits + `scheduled_timezone` instead of `toISOString()` | Make the DST guard reachable from the real client for the first time |
| `backend/tests/test_p2_scheduled_rides.py` | Strengthened 2 existing tests to assert the exact converted UTC value (previously only checked `is not None`); added a dedicated window-check-uses-converted-value regression test | Existing tests would not have caught the mislabeled-return bug |
| `rider-app/store/__tests__/rideStore.test.ts` | New test asserting the scheduled-ride payload shape (naive local `scheduled_time`, non-empty `scheduled_timezone`) | Cover the new client-side wiring |

## 7. Before / after

```python
# Before (backend/schemas.py) — passes DST-safety, returns the ORIGINAL value unchanged
...DST gap/ambiguity checks against `naive`...
return value  # local digits, still labeled as UTC -- wrong by the zone's UTC offset
```

```python
# After
...DST gap/ambiguity checks against `naive`, using `converted_utc = local.astimezone(utc_tz)`...
v_utc = converted_utc  # window checks below now use the corrected value too
...
return v_utc if tz_name else value  # corrected UTC when tz_name given; unchanged otherwise
```

```typescript
// Before (rider-app/store/rideStore.ts)
rideData.scheduled_time = scheduledTime.toISOString();  // true UTC; DST guard unreachable

// After
rideData.scheduled_time = formatLocalNaiveIso(scheduledTime);  // local digits
rideData.scheduled_timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
```

## 8. Rollback plan

- Both halves must roll back together (or not at all) — reverting only the rider-app half while keeping the backend fix is safe (backend behavior for no-`scheduled_timezone` callers is unchanged, so this just reopens the "guard unreachable" gap, not a new bug). Reverting only the backend half while the rider-app still sends local-digit `scheduled_time` + `scheduled_timezone` would reopen the wrong-dispatch-time bug — do not do this independently.
- `git revert` is safe and sufficient for either or both — no migration, no data touched, no feature flag.

## 9. Verification performed

- [x] Automated tests (backend): `pytest backend/tests/test_p2_scheduled_rides.py -q --no-cov` — **19 passed, 0 failed**, including 3 new/strengthened tests locking in the exact converted UTC value and the corrected window-check ordering.
- [x] Automated tests (rider-app): `npx jest store/__tests__/rideStore.test.ts` — **25 passed, 0 failed**, including the new scheduled-ride payload-shape test.
- [x] Blast-radius grep performed on both sides (§4) — confirmed no other backend reader of `scheduled_timezone`, confirmed the rider-app change is scoped to the scheduled-ride branch only.
- [ ] Not manually tested against a real device date-picker or a live backend — verified via unit tests with fixed/frozen dates on both sides.

## What was NOT verified

- `backend/ai/tools_booking.py`'s separate scheduled-time validation path was NOT touched — it remains a known, previously-documented gap (no DST logic at all for AI-assisted bookings). Flagged here, not silently left unmentioned.
- Did not verify against a real device's `Intl.DateTimeFormat().resolvedOptions().timeZone` behavior across iOS/Android — reasoned from the JS spec's guaranteed IANA-name return shape, not device-tested.
- No visual/UI change in this fix, so no screenshot/build verification applies to the rider-app half beyond the unit test.
