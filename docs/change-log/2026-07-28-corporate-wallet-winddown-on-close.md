# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-28 |
| Author | Claude (session), reviewed with @vikas |
| Surface(s) | backend |
| Domain (Sentry tag) | corporate |
| PR / commit link | branch `claude/corporate-module-review-6eh65j` |
| Related issue or gap ID | Corporate module review — gap #2 ("wallet wind-down on offboarding") |

## 1. Issue / gap identified

Closing a corporate account (`status='closed'`, terminal — cannot reopen, see the 409 check in `change_company_status`) had zero effect on the company's master wallet **balance**. Only `auto_topup_enabled` was disabled. Any remaining balance was left in `corporate_wallets.balance` with no path back to the company — a closed account can never top up, spend, or reopen, so that money was silently stranded.

## 2. Root cause

`routes/corporate_accounts.py::change_company_status` only ever wrote the account status and (on suspend/close) flipped `auto_topup_enabled` off. Nothing in the close path read the wallet balance or triggered a refund — the wallet-freeze logic was written to stop *future* charges, not to wind down *existing* funds. This was a missing step in the offboarding flow, not a bug in any one function.

## 3. Fix / remediation

New `services/corporate_wallet_winddown_service.py::refund_wallet_balance_on_close`, called from `change_company_status` **only when the target status is `closed`** (not `suspended` — suspend is reversible and deliberately untouched):

- Reads the company's master wallet balance. If ≤ 0, no-op.
- Refunds the balance via Stripe against the original top-up `PaymentIntent`(s) recorded in `corporate_wallet_transactions` (type=`topup`), most-recent-first, each refund capped to the remaining amount owed and idempotency-keyed on `wallet_id` + transaction id.
- Debits the ledger via the existing `corporate_wallet_service.apply_adjustment` (same RPC-backed, row-locked path already used for support adjustments) by exactly what was actually refunded via Stripe — **never** by the full balance if Stripe refunds only partially covered it.
- Any portion that can't be traced to a Stripe top-up (e.g. balance built from a manual support adjustment with no underlying charge) is left in the wallet and reported back in the result (`unrefundable_amount`) — never silently written off.
- A Stripe error stops further refunds immediately and is surfaced in the result (`stripe_error`); the route logs it with `logger.error` — it does not roll back the already-committed account closure, and does not get swallowed.
- Gated behind new `app_settings.corporate_close_refunds_wallet_balance`, **default `False`** — this moves real Stripe money, so it ships dark and must be verified in staging before an admin flips it on, per CLAUDE.md's payments rollout rule (unlike gap #1's flag, which defaulted `True` because doing nothing there was already a bug — here doing nothing is the current/safe behavior).

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to the close-status branch of `change_company_status` and a new service file.** Grepped every other caller:
  - `services/corporate_wallet_service.py::apply_adjustment` — also called from `routes/corporate_wallet.py` (manual support adjustments) and `services/payment_service.py` (ride settlement paths). Not modified; the new service only calls it the same way an admin-triggered adjustment already does, so no shared-code risk.
  - `db_supabase.get_corporate_wallet_by_company` / `get_rows` — read-only calls, same helpers used throughout `routes/corporate_wallet.py`, `routes/corporate_rider.py`, `routes/corporate_company.py`, `utils/allowance_reset.py`; none of those are touched.
  - No other code calls `refund_wallet_balance_on_close` — it's new and only wired into one call site.
- Interaction with the 16 background loops: none. `allowance_reset.py` and the auto-topup/low-balance loops don't run against closed companies (existing status checks), so no race with this one-shot refund.
- Money impact: real Stripe refunds move only when the flag is explicitly on, only on `closed` (never `suspended`), and only up to the wallet's actual balance — capped via `floor=Decimal("0")` on the ledger-side adjustment so a double-invocation (e.g. a retried request) can debit the ledger at most to zero, not negative.
- **Known accepted race**: if two concurrent close requests for the same company both pass the "not already closed" pre-check before either commits, both could invoke the refund path. The Stripe-side `idempotency_key` (`wallet_id` + topup transaction id) prevents duplicate Stripe refunds for the same underlying charge; the ledger-side `floor=0` prevents the balance going negative. This mirrors the same unaddressed race already documented for gap #1's ride-cancellation path — not newly introduced here, and low-probability (admin-only, human-paced action).

## 5. User-experience effect

- **Corporate admin (billing/owner)**: none directly — happens automatically on the existing "Close account" action already in the admin dashboard. No new UI.
- **Internal admin/finance**: the `change_company_status` audit log entry now includes a `wallet_winddown` object (refunded amount, Stripe refund IDs, any unrefundable remainder or error) for every close — new visibility, no UI change (existing audit log table/endpoint).
- **Rider/driver**: no effect — this only touches the corporate master wallet, not rider/driver wallets or ride flows.
- Not visible mid-session to anyone — this only fires on an already-terminal admin action (account close), which itself already has no mid-session visibility.
- Flag defaults off, so **no behavior change ships in this PR** until an admin explicitly opts in via `app_settings`.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/services/corporate_wallet_winddown_service.py` | New file: `refund_wallet_balance_on_close` | Core fix — Stripe refund + ledger debit on close |
| `backend/routes/corporate_accounts.py` | `change_company_status` calls the new service when status→closed, gated by the new flag; audit log records the result | Wiring + rollback flag + audit visibility |
| `backend/schemas.py` | New `AppSettings.corporate_close_refunds_wallet_balance: bool = False` | No-redeploy opt-in switch (default off — moves real money) |
| `backend/tests/test_corporate_wallet_winddown_service.py` | New: 7 unit tests for the service (no wallet, zero balance, no Stripe customer, single/multi top-up refund, partial-coverage remainder, Stripe error) | Regression coverage |
| `backend/tests/test_corporate_status.py` | +3 tests: flag-off skips wind-down, flag-on refunds and logs, suspend never triggers wind-down | Regression coverage |

## 7. Before / after

```python
# Before — routes/corporate_accounts.py::change_company_status
if transition.status in (CompanyStatus.SUSPENDED, CompanyStatus.CLOSED):
    wallet = await get_corporate_wallet_by_company(normalized_id)
    if wallet and wallet.get("auto_topup_enabled"):
        await update_corporate_wallet_config(wallet_id=wallet["id"], patch={"auto_topup_enabled": False})
# ... nothing else touches the wallet. Balance is never read or refunded.
```

```python
# After
if transition.status in (CompanyStatus.SUSPENDED, CompanyStatus.CLOSED):
    wallet = await get_corporate_wallet_by_company(normalized_id)
    if wallet and wallet.get("auto_topup_enabled"):
        await update_corporate_wallet_config(wallet_id=wallet["id"], patch={"auto_topup_enabled": False})

winddown_result = None
if transition.status == CompanyStatus.CLOSED:
    settings = await get_app_settings()
    if settings.get("corporate_close_refunds_wallet_balance", False):
        try:
            winddown_result = await refund_wallet_balance_on_close(
                company_id=normalized_id,
                stripe_customer_id=row.get("stripe_customer_id"),
                actor_user_id=str(current_admin.get("id") or ""),
            )
            ...  # logged loudly if incomplete
        except Exception:
            logger.error(..., exc_info=True)
            winddown_result = {"skipped_reason": "unhandled_exception"}
```

## 8. Rollback plan

- **Immediate, no-redeploy**: flip `app_settings.corporate_close_refunds_wallet_balance` to `False` from the admin dashboard. This is the primary rollback and requires nothing else — the flag defaults off, so it only needs flipping if it was already turned on.
- **If a bad refund already went out** (wrong amount, wrong customer) while the flag was on: this is a real Stripe money movement and, per CLAUDE.md, `git revert` is not sufficient. Remediation is: (1) flip the flag off immediately to stop further auto-refunds, (2) use the Stripe dashboard to reverse/cancel the specific refund if it hasn't settled, or issue a corrective charge if it has, (3) use the existing `apply_adjustment` support-adjustment path to correct the wallet ledger to match reality. There is no automated "undo" for a completed Stripe refund — this must be a manual finance action.

## 9. Verification performed

- [x] Automated tests run: `pytest tests/test_corporate_wallet_winddown_service.py tests/test_corporate_status.py tests/test_corporate_suspension_service.py tests/test_corporate_settle_suspended_audit_flag.py tests/test_corporate_wallet_freeze.py tests/test_corporate_wallet_routes.py -q` — 39 passed.
- [x] `ruff check` and `ruff format --check` clean on all changed files.
- [ ] Manual repro steps followed in staging — **not done**; no staging Stripe test-mode run was performed in this session. Flag defaults off specifically so this can be verified in staging before being turned on for any live company.
- [x] Blast-radius grep performed: `apply_adjustment`, `get_corporate_wallet_by_company` callers listed in §4.
- [x] Reviewed against relevant CLAUDE.md conventions: money (Decimal-only, Stripe idempotency keys, no float), corporate billing layer isolation, "do not silently swallow errors" (Stripe/DB errors always `logger.error` with full exception, never `warning`-and-continue).
- [x] Feature-flagged (`corporate_close_refunds_wallet_balance`, default off) — this is user-visible-adjacent (moves the company's money) and non-trivial.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (flag flip; manual Stripe/ledger remediation documented for the already-applied-money case)
- [x] Blast radius is stated, not assumed (§4)
- [x] No silent behavior change to an already-shipped flow — the flag defaults off, so this PR ships no behavior change until explicitly enabled

## What was NOT verified

- No real or test-mode Stripe API call was exercised against a live Stripe test account — only mocked `stripe.Refund.create` in unit tests. The actual Stripe refund request shape (metadata field limits, partial-refund-of-a-PaymentIntent semantics, what happens if the original payment method was removed from the customer) has not been confirmed against Stripe's live test API.
- No visual regression tooling exists for the admin dashboard's corporate-accounts page — not applicable here since no UI changed, but noting the standing gap per CLAUDE.md.
- The documented "known accepted race" on concurrent close requests (§4) was reasoned about, not reproduced under load.
