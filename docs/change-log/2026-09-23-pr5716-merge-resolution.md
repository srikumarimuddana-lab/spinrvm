# PR 5716 payment reconciliation merge resolution

| Field | Value |
|---|---|
| Date | 2026-09-23 |
| Surface / domain | Backend / payments |
| PR | https://github.com/srikumarimuddana-lab/spinrvm/pull/5716 |
| Issue | Main's Stripe SDK compatibility fix conflicts with PR 5716's exact settlement-evidence checks. |
| Root cause | Both branches changed the recovery PaymentIntent validation; the old single-intent check no longer matches the aggregate-ledger/frozen-tip contract. |
| Remediation | Preserve per-component exact proof, use `stripe_get` for each SDK object, and remove the superseded whole-fare check. Retain main's reconciliation date-window/backfill changes. |

## Risk, consumers, and user effect

The changed helper is called by `_maybe_heal_stuck_processing`, which is consumed by the daily reconciliation tick and the stale-processing path in `payment_retry.py`. These recover payment status and receipts for riders/drivers. A bad merge could reject valid split settlement or weaken incomplete-payment protection. Grep of backend Python callers confirmed these paths. No new charge, refund, wallet operation, migration, or feature flag is introduced by this resolution. Existing `stripe_auto_heal_processing` remains default-off.

Taking either branch wholesale was rejected: preserve main's SDK compatibility and the PR's stronger payment evidence together. No screen/copy changes; enabling existing recovery may finish an eligible stuck payment and issue its receipt mid-session.

## Resolution files

| File | Change | Reason |
|---|---|---|
| `backend/utils/stripe_reconcile.py` | Resolve validation conflict; use `stripe_get` inside the component loop | Preserve SDK compatibility and exact payment evidence |
| `backend/tests/test_stripe_reconcile.py` | Supply owned ledger evidence/receipt mock to the SDK test; add real SDK split-success, short-capture, and unfinished-capture cases | Exercise the combined behavior |
| This document | Record impact and verification | Reviewable merge resolution |

Other files in the merge commit come from main unchanged by this resolution.

## Before / after

```python
# Old main recovery checked one PI against the ride obligation.
expected_cents = _expected_capture_cents(ride)
# Resolved recovery preserves the PR's ledger/frozen-tip checks, then:
for component_pi, component_cents in component_amounts.items():
    pi = await asyncio.to_thread(stripe_mod.PaymentIntent.retrieve, component_pi)
    status = stripe_get(pi, "status")
    amount_received = stripe_get(pi, "amount_received", 0) or 0
    if status != "succeeded" or amount_received != component_cents:
        return False
```

Example: a $25 obligation represented by $20 + $5 succeeded components can recover; $20 + $4.99 or an unfinished $5 component remains unpaid and sends no receipt.

## Rollback

Disable `stripe_auto_heal_processing` to stop new automated recovery without redeploying. Already-settled financial history must be audited, not reversed blindly or charged again. Revert the resolution in source if needed while the flag stays off. No production flag or financial state was changed during this task.

## Verification

- Independent money auditor reviewed the resolved code, SDK tests, and auto-merged webhook guards: no blocker in the narrow resolution.
- 156 tests passed: test_stripe_reconcile.py, test_payment_retry.py, and test_webhooks_main.py (pytest, --no-cov). Global plugin autoload was disabled and asyncio/anyio/cov explicitly loaded after an unrelated xonsh plugin prevented initial startup. Two environment/config warnings remained; no test failures. git diff --check passed.
- No production API/SQL mutations or deployment; no live Stripe/Supabase integration, mobile build, or end-to-end ride test performed. This backend-only resolution has no UI changes requiring visual verification.
