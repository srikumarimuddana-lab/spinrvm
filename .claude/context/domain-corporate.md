# Domain — Corporate

_Load when working on: corporate accounts, corporate membership, corporate wallets/allowances, corporate policy, or any ride/booking code path that reads `corporate_account_id` / `payment_method == "company_allowance"` / `work_profile`._

Corporate billing sits on top of the consumer ride product without modifying core ride/driver logic (CLAUDE.md). This doc is the standing reference for that layer's lifecycle — built from a structured audit (PRs #2615, #2696) after three integration gaps shipped independently; see "Lessons learned" before adding a new lifecycle event.

## Key files

- `backend/routes/corporate_accounts.py` — admin-side company status transitions (`change_company_status`)
- `backend/routes/corporate_company.py` — member management (add/remove/role-change), policy CRUD
- `backend/routes/corporate_signup.py` — company creation (self-serve + admin-created)
- `backend/routes/corporate_wallet.py` — wallet ops: top-up, manual adjustment, auto-topup config
- `backend/routes/corporate_rider.py` — rider-facing corporate endpoints
- `backend/routes/corporate_company_bookings.py` — guest-booking flow (booker pays for a customer)
- `backend/routes/corporate_company_kyb.py` — KYB (Know Your Business) verification
- `backend/services/corporate_suspension_service.py` — company-level pre-pickup ride cancellation on suspend/close
- `backend/services/corporate_member_offboarding_service.py` — member-level equivalent
- `backend/services/corporate_wallet_winddown_service.py` — Stripe refund of remaining balance on close
- `backend/services/corporate_membership_service.py`, `corporate_policy_service.py`, `corporate_wallet_service.py`, `corporate_allowance_service.py`
- `backend/routes/rides/booking.py` — the two corporate booking paths inside `create_ride` (`company_allowance`, `work_profile`)
- `backend/services/payment_service.py::settle_corporate` — corporate settlement + completion-time audit flags
- `backend/utils/allowance_reset.py`, `backend/utils/corporate_low_balance.py` — background loops reading corporate state (see `core/lifespan.py` for the other 14)

## The lifecycle model

Corporate has three independent lifecycle axes that all cascade into the ride/payment layer:

1. **Company** — `pending_verification` → `active` ⇄ `suspended` → `closed` (terminal, cannot reopen)
2. **Membership** — `invited` → `active` ⇄ `suspended` / `removed`
3. **Policy** — created / edited / (soft-)"deleted" via `PATCH` to null fields (no real `DELETE /policy` route exists — an absent policy evaluates as an automatic pass, `corporate_policy_service.py::evaluate_policy`)

**Every lifecycle event on any axis should be checked against this cascade list** before it's considered complete — this is the checklist gaps #1–#3 and Findings 1–9 came from, not exhaustive enumeration in a spec:

| Cascade effect | Where it's enforced |
|---|---|
| Pre-pickup rides cancelled? | `corporate_suspension_service.py` (company), `corporate_member_offboarding_service.py` (member) — **not** wired to policy edits (Finding 7, deliberately) |
| In-progress rides | Always grandfathered — ride state machine forbids cancelling after trip start. Bill normally; flag audit-only in `settle_corporate` |
| Future booking blocked? | `routes/rides/booking.py`'s two corporate paths + `corporate_company_bookings.py::_require_company_active` (guest path) |
| Wallet balance | Only touched on company `close` (refund) — `suspend` only disables auto-topup, does not freeze/read balance |
| Background loop interaction | `allowance_reset_loop`, `corporate_low_balance_loop`, `corporate_autotopup_loop` all independently re-read company/member status fresh each tick — **do not assume one loop's guard covers another** |
| Admin audit log | Every state-changing admin action should call `log_admin_action`/`log_user_action` — several (wallet ops, member invite, policy edit) didn't until batch 2 (PR #2696) |
| Rider/driver notification | WS + push on cancellation only; no notification for booking-eligibility changes or reactivation |

## Flag-gating convention for this domain

Every behavior-changing fix in this domain gets an `app_settings` flag — the **default** is the tell for what kind of fix it is:

- **Default `true`**: the un-flagged behavior was the bug (fail-open, silent drift). Ships live immediately, flag exists purely as an emergency kill-switch. Examples: `corporate_suspend_cancels_pre_pickup_rides`, `corporate_member_removal_blocks_booking`, `corporate_inactive_company_blocks_booking`.
- **Default `false`**: the fix moves real money or is otherwise disruptive enough to need staging verification before going live. Ships dark. Example: `corporate_close_refunds_wallet_balance` (real Stripe refund).
- **No flag at all**: pure audit-trail visibility with zero behavior change to any response/settlement outcome (e.g. `policy_changed_since_booking`, the missing `log_admin_action` calls in batch 2) — a flag would be theater since there's nothing to roll back.

## Testing conventions specific to this domain

- Patch target for booking-path tests: `backend.routes.rides._deps.db_supabase` (a `MagicMock`, not auto-async) — any new DB call in `routes/rides/booking.py`'s corporate paths (e.g. a new `get_corporate_account_by_id` check) **will break every existing test that exercises that path** unless you also add `mock_supabase.<new_call> = AsyncMock(...)` to each one. This has happened twice (gap #3, Finding 1) — grep for `corporate_account_id=` and `work_profile=True` across `tests/*.py` before adding a new DB read to either corporate booking block.
- `settle_corporate` tests live in `test_corporate_settle_suspended_audit_flag.py` — the established pattern for a new completion-time audit-only flag is a `_patches()` helper + `contextlib.ExitStack`, see `policy_changed_since_booking` for the template.
- Background-loop tests (`allowance_reset.py`, `corporate_low_balance.py`) mock every DB read individually; adding a new read (e.g. company status) breaks existing tests the same way as the booking-path issue above — same fix, add the mock everywhere the loop is tested.

## Lessons learned

Three integration gaps (mid-ride company deactivation, wallet stranding on close, member offboarding not revoking booking access) shipped independently over time, each found by someone noticing the *specific* missing cascade, not by checking the lifecycle systematically. A structured audit (lifecycle event × cascade-effect matrix, described above) done as a one-time pass afterward found 9 more findings across the same 3 axes, including two more P0s (booking-time company-status check, policy-change audit visibility) that were the *same shape* of bug as the original three.

**When adding a new lifecycle event or a new corporate-adjacent feature**: check it against every row in the cascade table above before considering it done. When adding a new background loop that reads corporate state, verify independently whether it needs its own company/member-status guard — don't assume an existing loop's check covers it (this is exactly how `corporate_low_balance_loop` shipped with zero status check while `corporate_autotopup_loop` had one).

## Common pitfalls

- Don't assume cancelling existing rides on suspend/close also stops *new* bookings — those are two separate checks (this was Finding 1, live for months before caught).
- Don't assume a booking-time policy/membership check protects a *second* booking path — `company_allowance` and `work_profile` are two independent code blocks in `create_ride` with separate checks; a fix to one doesn't cover the other.
- Don't trust `list_active_memberships_for_user` to imply the *company* is active — it only filters on the member's own status.
- Don't add a new admin-facing corporate action without an audit-log call — six were found missing in one pass (batch 2).
- Corporate rides never get surge pricing (policy, see `domain-payments.md`) — this is enforced in fare calc, not in this module.
