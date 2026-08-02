# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-02 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | corporate, dispatch |
| PR / commit link | branch `claude/spinrvm-schedule-ride-review-2jsank` |
| Related issue or gap ID | Scheduled Rides gap review — Finding #16 (highest severity) |

## 1. Issue / gap identified

Suspending/closing a corporate account, or offboarding an individual member,
runs a cleanup sweep intended to stop "dispatching new drivers against the
company account" — but that sweep's status filter (`_PRE_PICKUP_STATUSES`)
excluded `scheduled`. A not-yet-dispatched scheduled ride billed to a
suspended company (or a removed member) was untouched by either sweep and
would dispatch normally and bill the company once its `scheduled_time`
arrived, directly contradicting both modules' stated purpose.

## 2. Root cause

`_PRE_PICKUP_STATUSES` was written for rides already mid-dispatch
(`searching` → `driver_arrived`); a not-yet-dispatched scheduled ride was
never considered, even though it's arguably the case with the *most*
lead time to act on, not the least.

## 3. Fix / remediation

Added `RideStatus.SCHEDULED` to `_PRE_PICKUP_STATUSES` in both
`backend/services/corporate_suspension_service.py` and
`backend/services/corporate_member_offboarding_service.py`. No other
change was needed: `_cancel_one_ride` in both files already handles
`driver_id=None` gracefully (skips the driver-release/period-transition
branch), already notifies the rider (WS + push), and corporate rides carry
no pre-auth hold to release pre-dispatch — so the existing function bodies
were already correct for this case, only the filter needed widening.

## 4. Risk & impact on existing functionality

- **Blast radius: the candidate-query filter and atomic-claim filter in
  both services, nothing else.** Every other branch of `_cancel_one_ride`
  (driver release, insurance-period transition, guest SMS notify, WS
  fan-out) is unconditional on ride content, not on the status value that
  matched — so widening the filter doesn't change what happens to a
  matched ride, only which rides get matched.
- Grepped for other readers of `_PRE_PICKUP_STATUSES` in both files — none
  beyond the two internal call sites (candidate query, atomic claim) in
  each. Grepped for other callers of `cancel_pre_pickup_rides_for_company`/
  `cancel_pre_pickup_rides_for_member` — only `routes/corporate_accounts.py`
  (company status transition) and the equivalent member-removal route
  (not modified here).
- **Interaction with the scheduled-ride dispatcher**: once a scheduled
  ride's status flips to `cancelled` via this sweep, `scheduled_rides.py`'s
  `check_scheduled_rides()` query (`is_scheduled=True, status='scheduled'`)
  no longer matches it on the next tick — no race, no double-processing;
  the atomic claim there is filtered on `status='scheduled'` and would
  simply find zero rows if a tick somehow ran concurrently with this sweep.
- No interaction with money — corporate rides have no pre-auth hold or
  captured charge to unwind pre-dispatch (confirmed by the original gap
  review's B.5/B.2 findings: no reservation/hold mechanism exists for
  corporate scheduled rides at all).

## 5. User-experience effect

**Rider-facing (the affected rider's own scheduled ride is now correctly
cancelled with notification, instead of silently proceeding to dispatch on
a company account they can no longer bill against)**: a rider whose
company account is suspended, or who is personally removed from a company,
now gets an immediate `ride_cancelled` WS message + push notification for
any of their scheduled rides, instead of the ride silently dispatching
(and a driver being sent, and the company being billed) at the originally
scheduled time. No behavior change for anyone whose company/membership
stays active — the filter only added one more status this scenario can
match.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/services/corporate_suspension_service.py` | `RideStatus.SCHEDULED` added to `_PRE_PICKUP_STATUSES` | Close the company-suspension gap |
| `backend/services/corporate_member_offboarding_service.py` | Same addition | Close the member-offboarding gap |
| `backend/tests/test_corporate_suspension_service.py` | 2 new tests: `scheduled` present in the query filter; a scheduled ride cancels correctly with driver-release/period-transition skipped and the rider notified | Pin the fix and the "no driver to release" branch specifically |
| `backend/tests/test_corporate_member_offboarding_service.py` | Same two tests, member-scoped | Same coverage for the companion module |

## 7. Before / after

```python
# Before (both files)
_PRE_PICKUP_STATUSES = (
    RideStatus.SEARCHING,
    RideStatus.DRIVER_ASSIGNED,
    RideStatus.DRIVER_ACCEPTED,
    RideStatus.DRIVER_ARRIVED,
)
```

```python
# After
_PRE_PICKUP_STATUSES = (
    RideStatus.SCHEDULED,
    RideStatus.SEARCHING,
    RideStatus.DRIVER_ASSIGNED,
    RideStatus.DRIVER_ACCEPTED,
    RideStatus.DRIVER_ARRIVED,
)
```

## 8. Rollback plan

Plain code change (one tuple entry, two files), no migration, no data
written beyond the standard cancellation write path both functions already
made before this fix (for other statuses). `git revert` fully restores
prior behavior. No feature flag — this closes an authorization/governance
gap in an already-shipped safety mechanism, not a new behavior needing a
gradual rollout; the "flag it and ship dark" convention is for genuinely
new user-visible features, and this instead makes an existing "stop
dispatching against this account" guarantee actually hold for one more
ride status it was always supposed to cover.

## 9. Verification performed

- [x] Automated tests: `backend/tests/test_corporate_suspension_service.py`
      + `backend/tests/test_corporate_member_offboarding_service.py`, 13
      passed (9 prior + 4 new) via the session's venv.
- [x] `ruff check` on all four touched files — clean.
- [ ] Manual repro in staging — not performed, no staging access.
- [x] Blast-radius grep performed (see §4).
- [x] Reviewed against CLAUDE.md's corporate-billing and background-loop
      conventions — confirmed no race with the scheduled dispatcher (§4).
- [x] Not feature-flagged — reasoning stated explicitly in §8 rather than
      silently omitted (this is a correctness fix to an existing safety
      mechanism, not new behavior).

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed — filter-only change, all
      downstream branches already handled the "no driver yet" case
      correctly
- [x] No silent behavior change to an already-shipped flow for the
      unaffected majority (active companies/members) — behavior changes
      only for the specific scenario this sweep exists to handle, and that
      change is exactly the one this module's own docstring already
      promised

## What was NOT verified

Not tested against a live/staging Supabase instance — only mocked
`db_supabase` calls. Did not verify the `routes/corporate_accounts.py`
company-status-transition endpoint or the equivalent member-removal route
end-to-end (both call the fixed functions but weren't themselves modified
or re-tested in this pass — their existing tests, if any, weren't run as
part of this change since the function signatures and call sites are
unchanged).
