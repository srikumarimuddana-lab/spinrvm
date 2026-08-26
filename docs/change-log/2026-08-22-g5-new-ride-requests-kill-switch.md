# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-22 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | dispatch |
| PR / commit link | (this branch: `claude/g5-new-ride-requests-kill-switch`) |
| Related issue or gap ID | ACTION_ITEMS.md G5 |

## 1. Issue / gap identified

None of the existing E5 kill switches (`scheduled_dispatch_enabled`,
`surge_engine_enabled`, `promo_redemption_enabled`, `corporate_billing_enabled`)
stop new ride requests generally — they gate scheduled dispatch, surge, promo
redemption, and corporate billing specifically. The existing supply-side lever
(`saskatoon-launch.md` §N-4's bulk `is_online = false` driver action) stops
drivers from taking new offers, but doesn't stop the rider app from submitting
new booking requests in the first place. There was no way to pause new ride
requests specifically without forcing every driver offline.

## 2. Root cause

Not a bug — a genuine gap in the incident-response toolkit, identified during
launch-readiness review (2026-08-21) rather than found as a live defect.

## 3. Fix / remediation

Added `new_ride_requests_enabled: bool = True` to `AppSettings` (`schemas.py`),
following the exact same pattern as the four E5 flags. Checked at the very top
of `POST /rides` (`create_ride`, `routes/rides/booking.py`), before
`validate_ride_location` or any DB read/write — flipping it off returns a
clean `503 "Ride requests are temporarily unavailable. Please try again
shortly."` instead of a raw error. Fails open on a settings-read error, same
convention as `settle_corporate`'s `corporate_billing_enabled` check and every
other kill switch in this codebase. Added the field to `SettingsUpdateRequest`
(`routes/admin/settings.py`) so it's admin-settable via the existing generic
PUT handler, same as its siblings — no new endpoint, no super-admin gate
(plain boolean, no credential/destination masking needed).

Also updated `docs/runbooks/saskatoon-launch.md` §N-4 to document this as a
third kill-switch option alongside the existing bulk `is_online=false` action
and the (not-yet-implemented) service-area `is_active=false` option.

## 4. Risk & impact on existing functionality

- **Blast radius**: isolated to `POST /rides`'s entry point. Grepped
  `backend/` for every other caller of `create_ride` — none; it's only
  reachable via its route. Does not affect any other endpoint, does not touch
  ride state for already-created rides, does not affect the four E5 flags
  (independent settings keys, independently checked).
- **Default is `true`** (current, always-on behavior) — adding this flag does
  not change any existing behavior until an admin explicitly flips it off.
- **Fail-open on settings-read failure**: a degraded `app_settings` read
  during an actual incident must not itself compound the incident by blocking
  all bookings — matches the existing convention for every other flag in this
  file.
- **In-flight rides unaffected**: the guard only runs at ride-*creation* time;
  it does not read or write anything on an existing ride, so a ride already in
  `searching`/`driver_assigned`/etc. when the flag is flipped off continues
  through the state machine normally.

## 5. User-experience effect

Rider-facing, but **only when an admin deliberately flips the flag off**
during an incident — no behavior change under normal operation (default
`true`). When active: riders attempting to book see a clean "temporarily
unavailable" message instead of the booking flow, and instead of whatever
raw/confusing error a genuine platform incident might otherwise produce.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/schemas.py` | Added `new_ride_requests_enabled: bool = True` to `AppSettings` | New kill-switch field, G5 |
| `backend/routes/rides/booking.py` | Added the guard at the top of `create_ride`, before any DB write | Enforce the flag at the demand-side chokepoint |
| `backend/routes/admin/settings.py` | Added `new_ride_requests_enabled: Optional[bool] = None` to `SettingsUpdateRequest` | Admin-settable via existing PUT handler |
| `backend/tests/test_kill_switch_flags.py` | Added `new_ride_requests_enabled` to the shared `_FLAGS` list | Schema-default/round-trip/non-super-admin coverage, same as its siblings |
| `backend/tests/test_booking_new_ride_requests_kill_switch.py` | New file, 4 tests | Booking-path enforcement: flag-off rejects before any DB call, flag-omitted defaults to enabled, settings-lookup failure fails open |
| `docs/runbooks/saskatoon-launch.md` | §N-4 updated with the new demand-side option | Document the new lever alongside the existing supply-side one |
| `docs/change-log/2026-08-22-g5-new-ride-requests-kill-switch.md` | New change-log entry | Required per CLAUDE.md |

## 7. Before / after

```python
# Before (routes/rides/booking.py::create_ride)
async def create_ride(body, request=None, current_user=Depends(get_current_user)):
    _deps.validate_ride_location(...)
    # ... idempotency check, ban/suspension check, etc.
```

```python
# After
async def create_ride(body, request=None, current_user=Depends(get_current_user)):
    try:
        _g5_settings = await _deps.get_app_settings()
        if not _g5_settings.get("new_ride_requests_enabled", True):
            raise HTTPException(status_code=503, detail="Ride requests are temporarily unavailable. Please try again shortly.")
    except HTTPException:
        raise
    except Exception:
        logger.warning("[BOOKING] app_settings lookup failed for new_ride_requests_enabled; proceeding as enabled")

    _deps.validate_ride_location(...)
    # ... unchanged
```

## 8. Rollback plan

Two independent levers, neither requires a deploy:
- **To disable the new feature entirely** (revert this change's effect): flip
  `new_ride_requests_enabled` back to `true` via the admin settings API — it's
  the default, so simply never setting it off has the same effect.
- **To revert the code itself**: `git revert` — pure additive code (new
  schema field, new guard clause, new admin-settable field), no migration, no
  live-data footprint. The flag defaulting to `true` means even an in-flight
  incident where the flag was flipped `false` resolves cleanly on revert
  (the flag simply stops being read/enforced, equivalent to being `true`).

## 9. Verification performed

- [x] `pytest tests/test_booking_new_ride_requests_kill_switch.py tests/test_kill_switch_flags.py tests/test_create_ride_remaining_branches.py tests/test_coverage_rides.py -q --no-cov` — 211 passed, 0 failed.
- [x] Full backend suite run: `pytest tests/ -q --no-cov` — see PR body for final pass/fail counts.
- [x] Blast-radius grep performed: `create_ride` has no other callers; the new field is independent of the four E5 flags.
- [x] Reviewed against CLAUDE.md conventions: fail-open on settings-read error (matches every other kill switch), Decimal/money rules not implicated (no money math here), dual-import pattern unaffected.
- [x] Feature-flagged — this IS the feature-flag/kill-switch itself, defaults to `true` (no behavior change), matches CLAUDE.md's pre-merge release gate #3 exactly (additive/flagged rollout for anything user-visible and non-trivial).

## 10. What was NOT verified

- Not exercised against a real Supabase `app_settings` row — mocked throughout, matching this repo's existing convention for this class of test.
- No staging environment exists for this repo (tracked separately, ACTION_ITEMS E1) — not manually reproduced against a live booking flow outside the test suite.
- Did not implement the service-area `is_active=false` option also mentioned in §N-4 (marked "once H is implemented" in the runbook) — out of scope for this item, which is specifically the app-settings-flag lever.
