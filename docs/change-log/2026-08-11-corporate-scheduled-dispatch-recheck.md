# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-11 |
| Author | Claude Code (spinr platform) |
| Surface(s) | backend |
| Domain (Sentry tag) | corporate, dispatch |
| PR / commit link | (this branch — see commit adding this file) |
| Related issue or gap ID | Corporate scheduled-ride audit (`spinr-corporate-billing-reviewer`), P0 finding #1 |

## 1. Issue / gap identified

`_corporate_policy_still_allows_dispatch()` — the function that re-validates a corporate-paid
scheduled ride right before it dispatches — never actually checked whether the company was
still active or the rider still an active member, despite its own docstring claiming to cover
"a suspended company the suspension sweep didn't catch in time." It only ran the fare/time-
window/allowance policy rules.

## 2. Root cause

This function was added by PR #3283 (scheduled-rides gap review, Finding #17) and only wired up
`evaluate_policy_for_ride`. `evaluate_policy_for_ride` looks up the rider's membership internally,
but if none is found it just degrades `allowance` to `{}` and still **passes** unless the company
also has an `allowed_payment_source: allowance_only` policy — most companies don't. It never calls
`require_company_bookable` (the function added in the same PR, Finding #20, that actually checks
`company.status == "active"`). Rounds 1 and 2 of the corporate/admin review (#3289, #3341) hardened
the booking-time company/membership checks in `routes/rides/booking.py` further but never touched
this dispatch-time re-check, so it drifted out of sync with its own stated purpose.

Concrete failure path: a rider is removed from a company on day 8 for a ride scheduled day 9. The
offboarding sweep (`corporate_member_offboarding_service.py`) is best-effort per-row and swallows
exceptions on a transient DB error, so a ride can survive it. At dispatch, this function found no
active membership but — absent an `allowance_only` policy — the ride still passed both real gates
that existed. It dispatched, completed, and `settle_corporate` correctly failed closed
(`payment_status: pending`) — but nothing ever re-drives a corporate ride stuck in `pending` (the
Stripe-PI retry loop skips rides with no `payment_intent_id`, which corporate rides never have), so
it sat with no valid payer until someone found it manually.

## 3. Fix / remediation

Added two gates to `_corporate_policy_still_allows_dispatch`, ordered to mirror
`routes/rides/booking.py`'s own sequence exactly:

1. **Gate 1 (company-active)**, before the existing policy check: calls `require_company_bookable`.
   Blocks dispatch (fails closed) only on a confirmed `HTTPException` (company genuinely not
   active); any other error (DB hiccup) fails open and logs.
2. **Gate 3 (membership-active)**, after the existing policy check: re-derives the rider's active
   memberships and checks the specific `corporate_member_id` stamped on the ride (falling back to
   "any active membership at this company" for `work_profile` rides, which don't stamp one).
   Gated by the existing `corporate_member_removal_blocks_booking` app_settings flag (default
   `True`), matching the booking-time check's own kill switch. Fails open on a lookup error.

Both new gates share app_settings that already exist and are already admin-controllable
(`corporate_inactive_company_blocks_booking`, `corporate_member_removal_blocks_booking`) — no new
flag was needed. The duplicated notify/escalate logic (Redis-deduped push + admin broadcast) that
used to live inline for the one existing gate was factored into a shared
`_notify_corporate_dispatch_blocked` helper, called by all three gates now, with no change to its
own behavior (same dedupe key shape, same fail-open-on-Redis-error semantics, same payload shape).

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to `backend/utils/scheduled_rides.py`.** `_corporate_policy_still_allows_dispatch`
  and `_notify_corporate_dispatch_blocked` are private (`_`-prefixed) and have no callers outside
  this file — confirmed by grep. The only caller is `_dispatch_scheduled_ride`, which already gates
  the atomic `scheduled → searching` claim on this function's return value; that gating logic is
  unchanged.
- **`require_company_bookable` now has a fourth call site** (`routes/rides/booking.py` ×2,
  `routes/corporate_company_bookings.py` ×1, now `scheduled_rides.py`) — confirmed via grep this is
  a pure read (fetches the company row, raises or returns) with no side effects, so calling it an
  extra time per dispatch tick doesn't touch any shared mutable state.
- **`list_active_memberships_for_user` now has an additional caller inside the dispatch loop** —
  also a pure read, already called elsewhere (`evaluate_policy_for_ride`, `routes/rides/booking.py`'s
  `work_profile` block, `dependencies/company_guard.py`). No write path touched.
- **Does not touch the ride state machine, WS events, or any wallet/allowance delta.** The gate runs
  strictly before the atomic `scheduled → searching` claim (unchanged ordering) — a blocked ride is
  left exactly as `check_scheduled_rides()` found it, same as the pre-existing policy gate's
  behavior.
- **New failure mode to watch:** a company/membership lookup now runs on every dispatch tick for
  every corporate scheduled ride due that tick, adding two more DB round-trips per ride to the
  dispatch loop. Both are simple point-reads (not table scans) and both fail open on error, so a
  slow/unavailable DB degrades to the pre-fix behavior (no re-check) rather than blocking dispatch
  loop-wide.

## 5. User-experience effect

- **Corporate riders**: a scheduled ride booked while an active company member can now be blocked
  at dispatch time (instead of silently dispatching and failing at settlement) if the company was
  suspended/closed or their membership was removed in the interim. The rider gets a push
  notification ("Your scheduled ride is on hold — contact your company admin") — this is the same
  notification copy the pre-existing policy-rule gate already sends; no new copy was written.
- **Not visible mid-session** to a rider already in an active ride — this only affects rides still
  in `scheduled` status at dispatch time.
- **Admins**: get the same `scheduled_ride_policy_blocked` WS broadcast as before, now also firing
  for the two new reasons (`failed_rules: ["company_inactive"]` / `["membership_inactive"]`).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/scheduled_rides.py` | Added company-active gate (calls `require_company_bookable`) and membership-active gate to `_corporate_policy_still_allows_dispatch`; extracted the shared notify/escalate logic into `_notify_corporate_dispatch_blocked` | Close the gap between this function's documented purpose and what it actually checked |
| `backend/tests/test_scheduled_dispatch_cr.py` | Added an autouse fixture defaulting the two new gates to "pass" so existing policy-rule tests are unaffected; added 8 new tests covering both gates' block/fail-open/kill-switch paths | Prevent the new gates from silently changing existing test behavior; cover the new logic |

## 7. Before / after

```python
# Before — only the fare/policy gate existed; company/membership status was never re-checked.
async def _corporate_policy_still_allows_dispatch(ride: dict) -> bool:
    if (ride.get("payment_method") or "").lower() != "company_allowance":
        return True
    ...
    result = await evaluate_policy_for_ride(...)
    if result.passed:
        return True
    # ... notify + return False
```

```python
# After — company-active gate runs first, membership-active gate runs after the policy gate.
async def _corporate_policy_still_allows_dispatch(ride: dict) -> bool:
    if (ride.get("payment_method") or "").lower() != "company_allowance":
        return True
    ...
    settings = await get_app_settings() or {}
    if settings is not None:
        try:
            await require_company_bookable(corporate_account_id, settings=settings)
        except HTTPException:
            ...  # notify + return False
    result = await evaluate_policy_for_ride(...)
    if not result.passed:
        ...  # notify + return False
    if settings is not None and settings.get("corporate_member_removal_blocks_booking", True):
        memberships = await list_active_memberships_for_user(rider_id)
        member_still_active = (
            any(m.get("id") == corporate_member_id for m in memberships) if corporate_member_id
            else any(m.get("company_id") == corporate_account_id for m in memberships)
        )
        if not member_still_active:
            ...  # notify + return False
    return True
```

## 8. Rollback plan

No migration, no data change — this is pure application logic. To revert without a redeploy:

- Set `corporate_inactive_company_blocks_booking = false` in `app_settings` to disable gate 1
  (company-active re-check) — this is the **same existing flag** `require_company_bookable` already
  respects at booking time, so flipping it off restores fail-open behavior for both booking and
  dispatch simultaneously (a deliberate trade-off: there's no separate dispatch-only flag).
- Set `corporate_member_removal_blocks_booking = false` in `app_settings` to disable gate 3
  (membership-active re-check) only — same flag `routes/rides/booking.py`'s existing fail-closed
  membership check already uses.
- Either takes effect within `_SETTINGS_TTL` (60s, in-process cache in `settings_loader.py`) with no
  redeploy.
- If neither is sufficient, `git revert` is safe here since no data was mutated by this change —
  the gates only ever prevent a `scheduled → searching` claim from happening; they never touch a
  ride, wallet, or membership row.

## 9. Verification performed

- [x] Automated tests run: `pytest backend/tests/test_scheduled_dispatch_cr.py backend/tests/test_p2_scheduled_rides.py backend/tests/test_scheduled_rides_coverage.py backend/tests/test_scheduled_preauth.py -q --no-cov` — **93 passed, 0 failed**.
- [ ] Manual repro steps followed in staging — not performed, no staging access in this environment.
- [x] Blast-radius grep performed: `_corporate_policy_still_allows_dispatch` / `_notify_corporate_dispatch_blocked` (no external callers), `require_company_bookable` (3 other call sites, all read-only), `list_active_memberships_for_user` (multiple other read-only callers) — see §4.
- [x] Reviewed against relevant CLAUDE.md conventions: ride state machine (gate runs before the atomic claim, unchanged ordering), "don't silently swallow errors" (every fail-open path logs at `error` level with the underlying exception, not `warning`), settings-in-DB pattern (reused two existing flags instead of adding new ones).
- [x] Feature-flagged: reuses two existing `app_settings` flags (both default `True`, matching booking-time behavior) rather than introducing new ones — no dark-rollout flag needed since this restores previously-claimed-but-missing behavior rather than adding new product behavior.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (two existing `app_settings` flags, no migration)
- [x] Blast radius is stated, not assumed (grep performed, single-file change, 3 other read-only callers of the reused functions)
- [x] No silent behavior change to an already-shipped flow without the UX field filled in (§5 states the new rider-facing block path explicitly)

## What was NOT verified

- Not exercised against a real Supabase instance — only against `mock_supabase_client` fixtures per this repo's test convention. The `require_company_bookable`/`list_active_memberships_for_user` DB round-trips themselves are unit-tested elsewhere (existing test suites for `corporate_policy_service.py` and `dependencies/company_guard.py`), not re-verified here.
- Did not load-test the two extra DB round-trips added to the dispatch loop's per-ride, per-tick cost — both are point-reads, but their added latency under production scheduled-ride volume wasn't measured.
- No visual/UI change in this fix (backend-only), so no screenshot/build verification applies.
