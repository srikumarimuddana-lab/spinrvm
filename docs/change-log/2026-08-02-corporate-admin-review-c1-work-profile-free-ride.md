# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-02 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | corporate, payments, rides |
| PR / commit link | branch `claude/spinrvm-schedule-ride-review-2jsank` |
| Related issue or gap ID | Corporate + Admin Portal Review — Critical #1 |

## 1. Issue / gap identified

A self-serve-signed-up, never-KYB-approved company could book a `work_profile`
ride and have it settle as `payment_status="paid"` with zero money actually
moved, and no error or alert of any kind.

## 2. Root cause

Two independent gaps compounded:

1. The `work_profile` corporate-booking guard in `routes/rides/booking.py`
   only blocked a company whose status was literally `"suspended"` or
   `"closed"` — it never checked for `"pending_verification"`, unlike the
   sibling `company_allowance` path, which already used the shared
   `require_company_bookable` guard that blocks anything not `"active"`.
   A self-serve signup creates the company at `pending_verification` and
   immediately bootstraps the signer as an active owner member — but a
   `corporate_wallets` row is only created on KYB approval. So a brand-new,
   unverified owner could pass the `work_profile` guard.
2. `services/payment_service.py::settle_corporate` computed
   `corp_wallet = {}` for a company with no wallet row. Both the
   allowance-debit branch (gated on `corp_wallet.get("id")`) and the
   master-fallback branch (same gate) were skipped entirely — neither raised,
   neither logged an error — and the function fell through to
   `update_ride(..., {"payment_status": "paid"})`, returning
   `PaymentResult(success=True, ...)`.

## 3. Fix / remediation

- `routes/rides/booking.py`'s `work_profile` block now calls the shared
  `require_company_bookable` guard (already used by the `company_allowance`
  path and the company-portal guest-booking path) instead of its own inline
  status check. The guard's broader "not active blocks" definition now
  correctly rejects `pending_verification` companies on this path too. The
  guard's 403/`{"code": ..., "failed_rules": [...]}` shape is caught and
  re-raised as this path's existing 400/`{"reason": "company_inactive"}`
  shape, so the error contract for `work_profile` callers is unchanged.
- `settle_corporate` now checks `corp_wallet.get("id")` immediately after
  fetching the wallet and, if absent, logs an error, leaves the ride
  `payment_status="pending"`, and returns
  `PaymentResult(success=False, error="Corporate wallet not found",
  status_code=503)` — before either debit branch is reached. This is a
  defense-in-depth backstop: the booking-time fix above should prevent new
  rides from ever reaching settlement in this state, but settlement itself
  no longer trusts that invariant silently.

## 4. Risk & impact on existing functionality

- **Blast radius: the `work_profile` pre-dispatch block in `booking.py`, and
  the top of `settle_corporate` in `payment_service.py`.** Grepped
  `get_corporate_wallet_by_company` across `backend/tests/*.py` (21 files) —
  every existing test that reaches `settle_corporate` already mocks a wallet
  row with a real `id`, so the new early-return is unreachable in all
  existing test paths and does not change their behavior.
- Every other corporate-booking test file that exercises the `work_profile`
  path (`test_create_ride_remaining_branches.py`,
  `test_corporate_ride_payment.py`) mocked the company-status check via the
  old direct `_deps.db_supabase.get_corporate_account_by_id` call site. Since
  `require_company_bookable` does its own local
  `from .. import db_supabase` import that reaches the real
  `backend.db_supabase` singleton (not the whole-module `_deps.db_supabase`
  replace those tests use), 4 tests in `test_create_ride_remaining_branches.py`
  and the entire `_mock_create_ride_deps` fixture in
  `test_corporate_ride_payment.py` needed an additional
  `patch("backend.db_supabase.get_corporate_account_by_id", ...)` /
  `patch("routes.rides._deps.get_app_settings", ...)` to keep testing the
  branch they were meant to test rather than being short-circuited by the
  now-stricter guard. This is the exact same test-fixture pattern already
  fixed twice earlier for the `company_allowance` path in this codebase's
  history — see `domain-corporate.md`'s "Testing conventions" section.
- No interaction with money movement for the booking-time fix — it only
  decides whether a ride is created. The settlement-time fix changes
  behavior only in the specific case of a company-billed ride with no
  wallet row, which per the KYB flow should not exist for a genuinely
  `active` company.

## 5. User-experience effect

**Rider-facing (corporate riders only, `work_profile` self-book path)**: a
rider whose company is `pending_verification` now gets a clear rejection at
booking (`"company_inactive"`) instead of the ride being created and later
silently succeeding with no payment ever collected from the company. This
mirrors the identical, already-shipped behavior on the `company_allowance`
path. **No effect** on any already-`active` company — this only changes
outcomes for companies that were previously incorrectly let through.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/rides/booking.py` | `work_profile` guard now calls `require_company_bookable` instead of its own inline suspended/closed-only check | Close the `pending_verification` gap; remove the third duplicate of this check |
| `backend/services/payment_service.py` | `settle_corporate` now fails loudly (pending + 503) when a company-billed ride has no wallet, instead of silently succeeding | Defense-in-depth backstop against the free-ride settlement path |
| `backend/tests/test_corporate_ride_payment.py` | Added `get_app_settings`/`get_corporate_account_by_id` mocks to `_mock_create_ride_deps`; new `test_work_profile_pending_verification_company_returns_400`; new `test_settle_no_wallet_leaves_pending_and_moves_no_money` | Cover the new guard branch and the new settlement backstop; fix a latent fixture gap that would have made 4 existing tests fail once the guard tightened |
| `backend/tests/test_create_ride_remaining_branches.py` | Added `patch("backend.db_supabase.get_corporate_account_by_id", ...)` to 4 `work_profile` tests | Same fixture-gap fix as above, for this file's tests |

## 7. Before / after

```python
# Before — routes/rides/booking.py (work_profile guard)
if _bk_settings_wp.get("corporate_inactive_company_blocks_booking", True):
    _corp_company_row_wp = await _deps.db_supabase.get_corporate_account_by_id(_corp_company_id)
    if _corp_company_row_wp and (_corp_company_row_wp.get("status") or "").lower() in ("suspended", "closed"):
        raise HTTPException(status_code=400, detail={"reason": "company_inactive"})
```

```python
# After
try:
    await _deps.require_company_bookable(_corp_company_id, settings=_bk_settings_wp)
except HTTPException as _company_bookable_exc:
    raise HTTPException(status_code=400, detail={"reason": "company_inactive"}) from _company_bookable_exc
```

```python
# Before — services/payment_service.py::settle_corporate
allowance = await db_supabase.get_member_allowance(membership["id"]) or {}
corp_wallet = await db_supabase.get_corporate_wallet_by_company(company_id) or {}

total = _round(_d(str(total_charge)))
# ... both debit branches gated on corp_wallet.get("id"), silently skipped if absent ...
await db_supabase.update_ride(ride_id, {"payment_status": "paid", ...})
return PaymentResult(success=True, charged_amount=_money_str(total_charge))
```

```python
# After
allowance = await db_supabase.get_member_allowance(membership["id"]) or {}
corp_wallet = await db_supabase.get_corporate_wallet_by_company(company_id) or {}

if not corp_wallet.get("id"):
    logger.error("[PAYMENT] company %s has no wallet — cannot settle ride %s against it", company_id, ride_id)
    await db_supabase.update_ride(ride_id, {"payment_status": "pending"})
    return PaymentResult(success=False, error="Corporate wallet not found", status_code=503)

total = _round(_d(str(total_charge)))
```

## 8. Rollback plan

Plain code change, no migration, no data written by either fix. `git revert`
fully restores prior behavior. No feature flag — this closes a real,
unconditional free-ride/silent-settlement gap; there is no meaningful
"dark-ship" version of a fix whose entire purpose is to stop an incorrect
success response.

## 9. Verification performed

- [x] Automated tests: `test_corporate_ride_payment.py` (19 tests, 2 new),
      `test_create_ride_remaining_branches.py` (23 tests, 4 modified),
      `test_corporate_settle_suspended_audit_flag.py` (6 tests, unaffected),
      `test_corporate_surge_bypass.py` (16 tests, unaffected),
      `test_coverage_rides.py` (all tests in file, unaffected) — 225 passed
      total, run via the session's `/tmp/spinr_venv` venv.
- [x] `ruff check` on all four touched files — clean.
- [ ] Manual repro in staging — not performed, no staging access.
- [x] Blast-radius grep performed (see §4): every `get_corporate_wallet_by_company`
      mock across the test suite, and every `work_profile=True` occurrence in
      the most-affected test file.
- [x] Dry-run scenario: a company signs up self-serve (status
      `pending_verification`, no wallet row yet). The owner opens the rider
      app, switches to Work mode, and books a ride. Before this fix: booking
      succeeds, ride dispatches, and at completion `settle_corporate` finds
      no wallet, skips both debit branches, and marks the ride paid with $0
      moved. After this fix: booking is rejected at creation with
      `{"reason": "company_inactive"}`, matching what already happens on the
      `company_allowance` path for the same company state.

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed — two functions, four dependent
      test files identified and fixed
- [x] No silent behavior change to a working flow — only companies that were
      incorrectly let through before are affected; an already-`active`
      company with a wallet sees no change

## What was NOT verified

Not tested against a live/staging Supabase instance — only mocked
`db_supabase` responses. Did not verify whether any currently-in-flight
production ride is already in this bad state (a `work_profile` ride against
a `pending_verification` company with no wallet, sitting at
`payment_status="paid"` with no money collected) — that would require a
one-off production data audit outside the scope of this code fix, and is
worth flagging separately to whoever owns finance reconciliation.
