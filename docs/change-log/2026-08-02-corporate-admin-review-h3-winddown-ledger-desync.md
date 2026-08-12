# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-02 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | corporate, payments |
| PR / commit link | branch `claude/spinrvm-schedule-ride-review-2jsank` |
| Related issue or gap ID | Corporate + Admin Portal Review — High #3 |

## 1. Issue / gap identified

If the wallet-ledger debit failed after one or more Stripe refunds had
already succeeded during a corporate account close, the exception
propagated uncaught out of `refund_wallet_balance_on_close`, and the
already-computed `refunded_total`/`stripe_refund_ids` were lost entirely —
the caller recorded only `{"skipped_reason": "unhandled_exception"}`, with
no trace that real money had already left the platform's Stripe balance.

## 2. Root cause

`services/corporate_wallet_winddown_service.py::refund_wallet_balance_on_close`
called `apply_adjustment` (the ledger debit) with no surrounding
try/except. The function's own docstring already states the intended
contract — "Returns a summary dict — never raises for a partial/failed
refund past the first Stripe error" — but that contract was only actually
implemented for Stripe-side failures (a `stripe.error.StripeError` during
the refund loop is caught and recorded in `result["stripe_error"]`), not for
a failure in the ledger write that happens after the Stripe refunds already
succeeded.

## 3. Fix / remediation

Wrapped the `apply_adjustment` call in its own try/except. On failure:
logs an error with the full exception, the refunded amount, and the Stripe
refund IDs that already succeeded; sets a new `result["ledger_write_failed"]
= True` flag; and — critically — does not clear or lose
`refunded_total`/`stripe_refund_ids`, which are still populated from the
successful Stripe calls further down in the function regardless of this
new branch. The function now genuinely never raises past a Stripe or ledger
failure, matching its own documented contract in both cases, not just one.

Also extended the existing loud-log condition in the caller
(`routes/corporate_accounts.py::change_company_status`) to include the new
`ledger_write_failed` flag alongside the pre-existing `stripe_error`/
`unrefundable_amount` checks, so this new failure mode surfaces in logs the
same way the two it already handled do.

## 4. Risk & impact on existing functionality

- **Blast radius: one function's single new try/except, and one caller's
  existing loud-log condition extended by one clause.** No change to the
  Stripe refund loop itself, the refund ordering (LIFO), or any other
  branch of the function.
- Every existing test in `test_corporate_wallet_winddown_service.py` mocks
  `apply_adjustment` as a plain `AsyncMock()` (succeeds by default) — none
  of them exercise the failure path this fix adds, so none needed changes;
  confirmed by running the full file (8 tests, 1 new).
- The caller's `winddown_result` dict is passed straight into the audit-log
  `details` blob (`log_admin_action(..., details={"wallet_winddown":
  winddown_result, ...})`) unchanged in shape — the new `ledger_write_failed`
  key is simply an additional field in that same dict, so audit-log
  consumers see the new information automatically without any schema change.

## 5. User-experience effect

**Internal/finance-facing only** — this changes what appears in server logs
and the admin audit log when closing a corporate account, not anything a
rider, driver, or corporate customer sees. Before this fix, a ledger write
failure after a successful Stripe refund was indistinguishable in the logs
from a total, unexplained failure. After this fix, ops/finance can see
exactly what was actually refunded via Stripe even when the internal ledger
failed to record it, making manual reconciliation possible instead of
starting from nothing.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/services/corporate_wallet_winddown_service.py` | Wrapped the ledger debit in try/except; added `ledger_write_failed` to the result dict | Preserve the real Stripe outcome when the ledger write fails after refunds already succeeded |
| `backend/routes/corporate_accounts.py` | Extended the wind-down loud-log condition to include `ledger_write_failed` | Surface the new failure mode the same way the two pre-existing ones already are |
| `backend/tests/test_corporate_wallet_winddown_service.py` | New test `test_ledger_write_failure_after_successful_refund_is_not_lost` | Cover the new failure path and lock in that Stripe outcome data survives it |

## 7. Before / after

```python
# Before
if refunded_total > 0:
    await apply_adjustment(
        wallet_id=wallet_id,
        amount=-refunded_total,
        notes=(...),
        actor_user_id=actor_user_id or "system",
        floor=Decimal("0"),
    )

result["refunded_total"] = str(refunded_total.quantize(_CENTS))
```

```python
# After
if refunded_total > 0:
    try:
        await apply_adjustment(
            wallet_id=wallet_id,
            amount=-refunded_total,
            notes=(...),
            actor_user_id=actor_user_id or "system",
            floor=Decimal("0"),
        )
    except Exception as ledger_exc:
        logger.error(
            "Corporate wallet close: %s in Stripe refunds succeeded (ids=%s) but the "
            "ledger debit failed for company=%s wallet=%s ...",
            refunded_total.quantize(_CENTS), stripe_refund_ids, company_id, wallet_id, ledger_exc,
            exc_info=True,
        )
        result["ledger_write_failed"] = True

result["refunded_total"] = str(refunded_total.quantize(_CENTS))  # always set, regardless of ledger outcome
```

## 8. Rollback plan

Plain code change, no migration, no schema change. `git revert` fully
restores the prior (unwrapped) behavior. No feature flag — this closes a
real information-loss gap in error handling; there is no meaningful
dark-ship version of "don't lose data you already have when logging a
failure."

## 9. Verification performed

- [x] Automated tests: `test_corporate_wallet_winddown_service.py` (8 tests,
      1 new), `test_corporate_accounts_lifecycle.py` (29 tests, unaffected),
      `test_corporate_status.py` (11 tests, unaffected) — 48 passed total,
      via the session's `/tmp/spinr_venv` venv.
- [x] `ruff check` on all three touched files — clean.
- [ ] Manual repro in staging — not performed, no staging access.
- [x] Blast-radius grep performed (see §4): the one caller of
      `refund_wallet_balance_on_close`, every test mocking `apply_adjustment`
      in this service's test file.
- [x] Dry-run scenario: a company with a $100 wallet balance funded by one
      Stripe top-up is closed. The Stripe refund succeeds ($100 refunded,
      `re_1` recorded). The subsequent ledger debit then fails (e.g. a
      transient DB error). Before this fix: the whole function raises,
      caller logs only `{"skipped_reason": "unhandled_exception"}` — no
      record that $100 was actually refunded via Stripe or which refund ID.
      After this fix: the function returns
      `{"refunded_total": "100.00", "stripe_refund_ids": ["re_1"],
      "ledger_write_failed": true, ...}`, logged loudly by the caller and
      captured in the audit log's `wallet_winddown` field — finance can now
      see exactly what happened and reconcile the wallet balance manually.

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed — one function, one caller
      condition, one test file, all confirmed unaffected beyond the
      intended new behavior
- [x] No silent behavior change to a working flow — the happy path (ledger
      write succeeds) is byte-for-byte unchanged; only the failure path,
      which previously lost data, now preserves it

## What was NOT verified

Not tested against a live/staging Supabase or Stripe instance — only mocked
responses for both. Did not implement automated reconciliation for a
`ledger_write_failed` state (e.g. a background job that retries the ledger
write or alerts finance automatically) — this fix makes the failure
visible and diagnosable, not self-healing; a human still needs to act on
the log line / audit entry it now produces.
