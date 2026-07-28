# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-28 |
| Author | Claude (session), reviewed with @vikas |
| Surface(s) | backend |
| Domain (Sentry tag) | corporate |
| PR / commit link | branch `claude/corporate-module-review-6eh65j` |
| Related issue or gap ID | Corporate module review — gap #3 ("employee/member offboarding — access revocation") |

## 1. Issue / gap identified

Removing a corporate member (`DELETE /company/{id}/members/{member_id}` or `PATCH .../members/{member_id}` with `status=removed`) only flipped `corporate_members.status`. Nothing else happened:

1. A removed member could still get a new `company_allowance` ride **created and dispatched** — the pre-dispatch policy check can pass with no active membership at all (company has no policy row, or `allowed_payment_source != "allowance_only"`), so the ride was only rejected later at *settlement*, landing as an unbilled `payment_status: "pending"` ride instead of being blocked at booking.
2. A removed member's already-`searching`/`driver_assigned`/`driver_accepted`/`driver_arrived` rides were left untouched — no member-level equivalent of gap #1's company-level `cancel_pre_pickup_rides_for_company`.
3. The monthly allowance-reset background loop (`utils/allowance_reset.py`) kept replenishing a removed member's spending budget **indefinitely** — it only checked that the `corporate_members` row existed, never that `status == "active"`.
4. No audit trail of the removal.

## 2. Root cause

`remove_member`/`update_member` in `routes/corporate_company.py` were written as a bare status-column patch with no downstream integration — the same class of gap as #1 (missing integration between corporate-account/member lifecycle and the ride/allowance lifecycle, not a bug in any one function). The allowance-reset loop's `if not member: continue` guard checked existence, not activeness — a narrower version of the same class of oversight.

## 3. Fix / remediation

- **Booking-time fail-closed** (`routes/rides/booking.py`, in `create_ride`'s pre-dispatch corporate check): when the policy check passes but no active `corporate_members` row is found for the rider+company, the booking is now rejected with 403 (`failed_rules: ["membership_inactive"]`) instead of silently proceeding with `corporate_member_id=None`. Gated by new `app_settings.corporate_member_removal_blocks_booking`, **default `True`** — the old behavior (fail open) was a bug, same precedent as gap #1's flag.
- **Member-level ride cancellation**: new `services/corporate_member_offboarding_service.py::cancel_pre_pickup_rides_for_member`, a direct member-scoped port of `corporate_suspension_service.py`'s company-level cancellation (same claim pattern, same notify-rider/driver/guest behavior, no cancellation fee). Wired into both `remove_member` (DELETE) and `update_member` (PATCH `status=`) in `routes/corporate_company.py`, firing only on an actual status **transition** into `removed` or `suspended` (not on an idempotent repeat, and not on unrelated field updates like a role change). Gated by new `app_settings.corporate_member_removal_cancels_pre_pickup_rides`, **default `True`**, same precedent as gap #1.
- **Allowance-reset loop leak** (`utils/allowance_reset.py::run_allowance_reset_tick`): added `if member.get("status") != "active": continue` right after the existing member-exists check. Not flag-gated — this is an unambiguous correctness fix to a background loop (stopping indefinite over-replenishment), not a judgment call, so it ships unconditionally.
- **Audit trail**: both `remove_member` and `update_member` now call `log_user_action` (the same `audit_logs` write path as `log_admin_action`, correctly attributed as a company-user-initiated action rather than a platform-admin one) recording old/new status and the cancelled-ride count.

## 4. Risk & impact on existing functionality

- **Blast radius, booking-time change**: `routes/rides/booking.py::create_ride` is the single consumer-facing ride-creation endpoint; the new branch only executes when `body.payment_method == "company_allowance"` **and** the active-membership re-check (which already existed) comes back empty — every other payment method and every rider with an active membership is unaffected. `test_corporate_surge_bypass.py`'s existing fixture didn't back the `corporate_members` table query at all (it only stubbed `list_active_memberships_for_user`, a different helper) — this surfaced as a real test failure under the new fail-closed check, confirming the gap was real; fixed by backing that table in the fixture, not by weakening the check.
- **Blast radius, member removal**: `update_corporate_member` (`repositories/corporate_repo.py`) has exactly one caller — `routes/corporate_company.py` — so no other code path silently gains new side effects. `cancel_pre_pickup_rides_for_member` is new and only called from these two endpoints.
- **Blast radius, allowance-reset loop**: `run_allowance_reset_tick` has exactly one caller, the `allowance_reset_loop` background task spawned once in `core/lifespan.py`. The added check can only ever *skip* additional rows relative to today's behavior (never process more) — a pure narrowing, no new write paths.
- Interaction with the 16 background loops: only `allowance_reset_loop` is touched, and only to skip inactive members — no change to its replay-safety (CAS-on-`period_end`) guarantees, which are unaffected since the new check runs before that claim.
- Ride state machine: member-level cancellation uses the identical atomic `$in`-status-guard claim as gap #1's company-level cancellation and `cancel_ride_rider` — a ride that raced into `in_progress` between the read and the write is correctly left alone.
- Money impact: no wallet or Stripe money movement in this gap (unlike gap #2) — the allowance-reset fix only prevents a *ceiling* from being replenished, it doesn't touch any already-accrued `used`/balance figures; nothing is charged, refunded, or written off.
- **Known scope boundary, deliberately not done** (per explicit product decision during scoping): the member's `corporate_member_allowances` row itself is not explicitly paused/zeroed on removal — relying instead on (a) the reset-loop fix stopping further replenishment and (b) the booking-time block preventing any further spend against it. No allowance ledger money is real (it's a spending ceiling, not a wallet balance), so this was judged sufficient rather than defense-in-depth.

## 5. User-experience effect

- **Removed member (rider)**: if mid-search or waiting for a driver when their access is revoked, their ride is now cancelled automatically with a clear reason ("Your access to this company account was removed before this ride started.") — new, user-visible interruption, previously silent. Attempting to book a new `company_allowance` ride after removal now gets a clear 403 ("You're no longer an active member of this company account.") instead of a ride that dispatches and later fails to settle.
- **Driver**: if already assigned to a now-cancelled ride, released back to available immediately and notified — identical to gap #1's pattern.
- **Company admin**: none directly — automatic on the existing remove/suspend member action already in the company portal. The action's audit trail now records `pre_pickup_rides_cancelled`.
- **In-progress rides are unaffected**: a member already mid-trip when removed is grandfathered, same rule as gap #1 (ride state machine forbids cancelling after trip start).
- Not visible mid-session to anyone whose ride is already `in_progress`.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/rides/booking.py` | Fail-closed 403 when `company_allowance` booking has no active `corporate_members` match | Close the booking-time gap |
| `backend/services/corporate_member_offboarding_service.py` | New file: `cancel_pre_pickup_rides_for_member` | Member-level ride cancellation (ports gap #1's pattern) |
| `backend/routes/corporate_company.py` | `remove_member`/`update_member` call the new cancellation service + audit log on a status transition into removed/suspended, both flag-gated | Wiring + rollback flags + audit visibility |
| `backend/utils/allowance_reset.py` | Skip members whose `status != "active"` | Stop indefinite allowance replenishment for removed members |
| `backend/schemas.py` | New `corporate_member_removal_blocks_booking: bool = True`, `corporate_member_removal_cancels_pre_pickup_rides: bool = True` | No-redeploy rollback switches |
| `backend/tests/test_create_ride_remaining_branches.py` | +2 tests: fail-closed 403, flag-disabled rollback path | Regression coverage |
| `backend/tests/test_corporate_member_offboarding_service.py` | New: 4 unit tests for the cancellation service | Regression coverage |
| `backend/tests/test_corporate_company_routes.py` | +5 tests: cancellation on remove, flag-off skip, idempotent no-op, suspend also revokes, role-change doesn't | Regression coverage |
| `backend/tests/test_corporate_allowance_reset.py` | +1 test: removed member skipped by reset loop; existing fixtures updated to include `status: "active"` | Regression coverage |
| `backend/tests/test_c_allowance_reset_atomic.py` | Existing fixture updated to include `status: "active"` (needed after the reset-loop fix) | Keep existing CAS-replay tests passing |
| `backend/tests/test_corporate_surge_bypass.py` | Fixture backs the `corporate_members` table query the fail-closed check now depends on | Fixes a real gap the fixture had been masking — confirms the booking-time fix works |

## 7. Before / after

```python
# Before — routes/rides/booking.py (create_ride, pre-dispatch corporate check)
_corp_members = await _deps.db_supabase.get_rows(
    "corporate_members",
    {"company_id": body.corporate_account_id, "user_id": current_user["id"], "status": "active"},
    limit=1,
)
if _corp_members:
    _corp_member_id = _corp_members[0]["id"]
# else: _corp_member_id stays None — ride is still created with
# payment_method="company_allowance" and corporate_account_id set.
```

```python
# After
if _corp_members:
    _corp_member_id = _corp_members[0]["id"]
else:
    settings = await _deps.get_app_settings() or {}
    if settings.get("corporate_member_removal_blocks_booking", True):
        raise HTTPException(status_code=403, detail={
            "message": "You're no longer an active member of this company account.",
            "failed_rules": ["membership_inactive"],
        })
```

```python
# Before — routes/corporate_company.py::remove_member
async def remove_member(company_id, member_id, guard=Depends(require_company_admin)):
    existing = await get_corporate_member_by_id(member_id)
    if not existing or existing.get("company_id") != company_id:
        raise HTTPException(status_code=404, detail="Member not found")
    return await update_corporate_member(member_id, {"status": "removed"}) or existing
```

```python
# After
async def remove_member(company_id, member_id, guard=Depends(require_company_admin)):
    existing = await get_corporate_member_by_id(member_id)
    if not existing or existing.get("company_id") != company_id:
        raise HTTPException(status_code=404, detail="Member not found")
    updated = await update_corporate_member(member_id, {"status": "removed"}) or existing
    await _maybe_revoke_access_on_removal(
        company_id=company_id, member_id=member_id,
        previous_status=existing.get("status"), new_status="removed",
        actor=guard["user"],
    )
    return updated
```

```python
# Before — utils/allowance_reset.py::run_allowance_reset_tick
member = await get_corporate_member_by_id(r["member_id"])
if not member:
    continue
wallet = await get_corporate_wallet_by_company(member["company_id"])
```

```python
# After
member = await get_corporate_member_by_id(r["member_id"])
if not member:
    continue
if member.get("status") != "active":
    continue
wallet = await get_corporate_wallet_by_company(member["company_id"])
```

## 8. Rollback plan

- **Immediate, no-redeploy**: flip `app_settings.corporate_member_removal_blocks_booking` and/or `corporate_member_removal_cancels_pre_pickup_rides` to `False` from the admin dashboard. Either can be toggled independently.
- The allowance-reset loop fix is **not** flag-gated (see §3 — deliberate, it's a pure correctness fix). If it needs to be reverted, that requires a code revert + redeploy; this is an accepted tradeoff because the alternative (a removed member's spending ceiling silently refilling forever) was judged to be strictly worse than any plausible reason to want the old behavior back.
- No Stripe charges, wallet deltas, or already-applied money movements are part of this change — a `git revert` is a sufficient rollback for the allowance-reset piece specifically (nothing here writes real money, unlike gap #2).

## 9. Verification performed

- [x] Automated tests run: `pytest tests/test_corporate_member_offboarding_service.py tests/test_corporate_company_routes.py tests/test_create_ride_remaining_branches.py tests/test_corporate_allowance_reset.py tests/test_c_allowance_reset_atomic.py tests/test_corporate_surge_bypass.py tests/test_corporate_ride_payment.py tests/test_company_guest_booking.py tests/test_coverage_rides.py tests/test_cancellation_fee_card_charge.py tests/test_guest_auto_settle.py tests/test_payment_metrics.py tests/test_admin_extended.py -q` — 330 passed (includes one pre-existing test whose fixture gap this change surfaced and fixed — see §6).
- [x] `ruff check` and `ruff format --check` clean on all changed files.
- [ ] Manual repro steps followed in staging — **not done**; no staging run in this session.
- [x] Blast-radius grep performed: `update_corporate_member` (single caller), `run_allowance_reset_tick` (single caller, `core/lifespan.py`'s one background loop) — listed in §4.
- [x] Reviewed against relevant CLAUDE.md conventions: ride state machine (atomic claim pattern reused verbatim), "do not silently swallow errors" (ride-cancellation failures are `logger.error` with full exception, best-effort per-ride so one failure doesn't block the rest — same as gap #1), background-loop replay-safety (the new check runs before the existing CAS claim, doesn't interact with it).
- [x] Feature-flagged where the change is user-visible and reverses prior (buggy) behavior — both new flags default `True` per the same reasoning as gap #1 (doing nothing was already the bug).

## 10. Sign-off

- [x] Rollback plan is concrete and testable (two independent flags; allowance-loop fix accepted as non-flagged per stated reasoning)
- [x] Blast radius is stated, not assumed (§4)
- [x] No silent behavior change to an already-shipped flow without the UX field filled in (§5)

## What was NOT verified

- No staging/live run — only unit tests against mocked Supabase/Stripe-free paths (this gap doesn't touch Stripe at all).
- The member allowance row itself is not explicitly paused/zeroed (§4, "Known scope boundary") — this was a deliberate scope decision during design, not an oversight, but it means a removed member's `corporate_member_allowances.used` figure is left exactly as it was at removal time, relying on the booking block and reset-loop fix rather than a ledger-level action.
- No email/push notification is sent to the removed member themselves about the access change (only to riders whose in-flight ride was cancelled, via the existing ride-cancellation notification path) — company admins are not separately notified either. This mirrors gap #1's scope (which also only notifies affected riders/drivers, not the company), not a new gap introduced here.
