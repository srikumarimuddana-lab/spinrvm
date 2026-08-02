# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-02 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | corporate, rides |
| PR / commit link | branch `claude/spinrvm-schedule-ride-review-2jsank` |
| Related issue or gap ID | Scheduled Rides gap review — Finding #19 |

## 1. Issue / gap identified

The company-portal's booking-cancel endpoint (`POST /company/{id}/bookings/{ride_id}/cancel`)
404'd on any ride not flagged `guest_booking=True` — but the booking list
it's attached to shows both guest bookings and employees' own self-booked
rides side by side, with no visual distinction. A company admin spotting a
self-booked ride they need to stop had no way to act on it from the one
screen built for that job.

**A second, related bug found while implementing the fix**: this endpoint
unconditionally delegated to `cancel_ride_rider`, whose cancellable-states
list doesn't include `scheduled` — so a not-yet-dispatched *guest* scheduled
booking (which *does* pass the `guest_booking` check) was **also** broken,
just with a different failure (a 400/409 from the state guard rather than a
404), before ever reaching this fix.

## 2. Root cause

- The `guest_booking` check was written when this endpoint's only purpose
  was guest bookings; self-booked employee rides were never in scope for
  it, even though they later started appearing in the same list.
- The delegate was always `cancel_ride_rider`, written for the generic
  dispatched-ride cancel case; nobody updated it when `cancel_scheduled_ride`
  was introduced as the dedicated pre-dispatch handler elsewhere in the
  codebase.

## 3. Fix / remediation

In `backend/routes/corporate_company_bookings.py::cancel_booking`:
- Removed the `not ride.get("guest_booking")` exclusion from the lookup
  guard — the existing ownership check (admin role, or
  `corporate_member_id` match) already applies correctly to both booking
  shapes, no change needed there.
- Branch on `ride.get("is_scheduled")`: a scheduled ride now delegates to
  `cancel_scheduled_ride` (which itself falls through to `cancel_ride_rider`
  once the ride has actually dispatched) instead of unconditionally calling
  `cancel_ride_rider` directly — mirroring exactly what the rider-app's own
  `DELETE /rides/scheduled/{id}` route does.
- The guest-SMS notification call (`notify_guest_cancelled`) is left
  unconditional — it already no-ops for a non-guest rider internally
  (checks `user.is_guest`), so no additional guard was needed there.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to `cancel_booking`'s guard and delegate
  selection.** `cancel_ride_rider` and `cancel_scheduled_ride` themselves
  are unmodified — this fix only changes which one gets called, and for
  which rides the endpoint now proceeds instead of 404ing. Grepped for
  other callers of `cancel_booking` — none beyond the route registration
  itself.
- A guest scheduled booking now correctly reaches `cancel_scheduled_ride`
  instead of failing against `cancel_ride_rider`'s state guard — this is a
  bug fix for the guest path too, not just new coverage for the self-booked
  path.
- No interaction with money — both delegate functions already own their
  own fee logic (unchanged); this fix only changes routing.

## 5. User-experience effect

**Corporate-admin-facing.** A company admin (or the employee themselves,
for their own booking) can now cancel a self-booked employee ride through
the company portal — previously a hard 404. A guest scheduled booking that
was silently broken (couldn't be cancelled at all, pre-dispatch) now works
correctly through the same button. No rider/driver-facing change — the
underlying cancel mechanics (WS events, notifications) are unchanged,
delegated to the same functions the rider-app already uses directly.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/corporate_company_bookings.py` | Removed the `guest_booking` exclusion; branch on `is_scheduled` to pick `cancel_scheduled_ride` vs `cancel_ride_rider` | Cover self-booked rides; fix the pre-existing guest-scheduled-booking bug found in the process |
| `backend/tests/test_corporate_company_bookings_routes.py` | Replaced the now-incorrect `test_cancel_booking_non_guest_ride_is_404` with a test asserting the ride IS cancellable; added a scheduled-ride delegation test | Pin the new, correct behavior |
| `backend/tests/test_corporate_company_bookings_coverage.py` | Same replacement for the duplicate test in this file | Same reason, second coverage file |

## 7. Before / after

```python
# Before
ride = await db_supabase.get_ride(ride_id)
if not ride or ride.get("corporate_account_id") != ctx["company_id"] or not ride.get("guest_booking"):
    raise HTTPException(status_code=404, detail="Booking not found")
...
result = await cancel_ride_rider(ride_id, reason="Cancelled by company", request=request, current_user=guest_user)
```

```python
# After
ride = await db_supabase.get_ride(ride_id)
if not ride or ride.get("corporate_account_id") != ctx["company_id"]:
    raise HTTPException(status_code=404, detail="Booking not found")
...
if ride.get("is_scheduled"):
    result = await cancel_scheduled_ride(ride_id, request=request, current_user=customer)
else:
    result = await cancel_ride_rider(ride_id, reason="Cancelled by company", request=request, current_user=customer)
```

## 8. Rollback plan

Plain code change, no migration, no data written. `git revert` fully
restores prior behavior (both bugs would return). No feature flag —
this fixes a broken/incomplete existing endpoint (a 404 or a failed cancel
attempt) rather than introducing new user-visible behavior; the "correct"
outcome (a cancel succeeding) is strictly better than the prior broken
state for every affected case, so there's no meaningful "ship dark" version
of a bug fix like this.

## 9. Verification performed

- [x] Automated tests: `backend/tests/test_corporate_company_bookings_routes.py`
      + `backend/tests/test_corporate_company_bookings_coverage.py`, 41
      passed (37 prior, with 2 updated to assert corrected behavior, + 2
      genuinely new) via the session's venv.
- [x] `ruff check` on all three touched files — clean.
- [ ] Manual repro in staging — not performed, no staging access.
- [x] Blast-radius grep performed (see §4).
- [x] Reviewed against CLAUDE.md's corporate-billing conventions.
- [x] Not feature-flagged — reasoning stated in §8 (bug fix, not new
      behavior).

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed — routing-only change, delegate
      functions unmodified
- [x] No silent behavior change to a working flow — both changes fix
      currently-broken behavior (a 404 that shouldn't happen, and a failed
      cancel attempt that shouldn't fail); nothing that worked before stops
      working

## What was NOT verified

Not tested against a live/staging Supabase instance — only mocked
`db_supabase` calls and mocked delegate functions.

**Confirmed, not left open**: checked whether the admin-dashboard
company-portal UI (`admin-dashboard/src/app/company-portal/[id]/bookings/page.tsx`)
gates its Cancel button on `guest_booking` — it doesn't. The button is
gated purely on ride *status* (`PRE_TRIP.has(r.status)`, matching
`scheduled`/`searching`/`driver_assigned`/`driver_accepted`/`driver_arrived`),
so it was already rendering for self-booked rows and calling this endpoint,
which then 404'd. This backend-only fix is therefore sufficient — no
frontend change is needed to actually surface the corrected behavior.
