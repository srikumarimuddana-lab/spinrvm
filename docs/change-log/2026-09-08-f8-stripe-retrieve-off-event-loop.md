# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-08 |
| Author | Claude (session implementing PR #5085's hardening plan) |
| Surface(s) | backend |
| Domain (Sentry tag) | payments |
| PR / commit link | branch `claude/pr-5085-5079-hardening-5a2aj7` |
| Related issue or gap ID | F8, validated in PR #5085 (`docs/audit/2026-09-07-pr-5079-validation-and-hardening-plan.md`, "PR-4"); tracked as `ACTION_ITEMS.md` C86 (secondary bare Stripe sites elsewhere, not touched here) |

## 1. Issue / gap identified

Two synchronous Stripe SDK calls (`stripe.Invoice.retrieve`, `stripe.Subscription.retrieve`) were called directly inside `async def` webhook-handler coroutines, blocking the event loop for the duration of the Stripe HTTP round-trip. CLAUDE.md states a <500ms Stripe-webhook-processing SLA.

## 2. Root cause

Neither call site was ever wrapped off the event loop when added — a synchronous SDK call inside an `async def` function blocks the whole worker's event loop, not just the current coroutine.

## 3. Fix / remediation

Both call sites now go through `asyncio.to_thread`: `_extract_invoice_payment_intent`'s call site in `_handle_ride_invoice_paid` (the helper itself stays sync — several tests call it directly and patch `stripe.Invoice.retrieve` as a module attribute), and the subscription-recovery `Subscription.retrieve` call in `_dispatch_stripe_event`.

## 4. Risk & impact on existing functionality

- **Blast radius**: isolated to these two call sites in `routes/webhooks.py`. Grepped for other `stripe.*.retrieve(` calls in this file — none. ~9 other bare synchronous Stripe SDK calls exist elsewhere in the codebase (background loops, admin routes) — tracked separately as `ACTION_ITEMS.md` C86, not touched in this change.
- **Executor**: `asyncio.to_thread` uses the event loop's default executor via `loop.run_in_executor(None, ...)`. Reviewed by `spinr-performance-sla-reviewer`, who corrected an initial assumption (carried over from the plan doc) that this shares a thread pool with `repositories/_base.py`'s DB calls — it does not: `_base.py` uses its own dedicated `_DB_EXECUTOR`, a separate pool. The default executor is already used by ~130 other `asyncio.to_thread` sites across the codebase (an existing, pre-established pattern), so this adds negligible new load to it.
- **Exception propagation confirmed unaffected**: at the invoice-PI site, the try/except lives inside `_extract_invoice_payment_intent` itself (unchanged); at the subscription-recovery site, the enclosing `try/except Exception` wraps the `await` directly, and `asyncio.to_thread` reliably re-raises a threaded exception into the awaiting coroutine.
- **Thread safety**: both call sites pass `api_key=stripe_secret` explicitly per-call rather than mutating global `stripe.api_key` state — no shared mutable SDK state at risk from running on a worker thread.
- No new race from the added `await` point: `_dispatch_stripe_event`'s branch already had multiple `await`s (DB `find_one`/`update_one`) before this change.

## 5. User-experience effect

None directly visible. Internal effect: the event loop no longer blocks for the Stripe HTTP round-trip during these two paths, keeping other concurrent webhook/request processing responsive.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/webhooks.py` | Two `stripe.*.retrieve(...)` calls wrapped in `asyncio.to_thread` | Stop blocking the event loop on a synchronous Stripe HTTP call |

## 7. Before / after

```python
# Before
payment_intent_id = _extract_invoice_payment_intent(invoice, stripe_secret)
...
_sub_obj = _stripe.Subscription.retrieve(stripe_sub_id, api_key=stripe_secret)
```

```python
# After
payment_intent_id = await asyncio.to_thread(_extract_invoice_payment_intent, invoice, stripe_secret)
...
_sub_obj = await asyncio.to_thread(_stripe.Subscription.retrieve, stripe_sub_id, api_key=stripe_secret)
```

## 8. Rollback plan

`git revert` — pure execution-model change (sync call → threaded call), no schema/data/flag coupling, no behavior change to what either call returns or raises.

## 9. Verification performed

- [x] Automated tests: `pytest tests/test_webhooks_helpers_coverage.py tests/test_webhooks_coverage_gap.py tests/test_webhooks_main.py tests/test_routes_webhooks_coverage.py tests/test_outbox_receipts.py -q` — 209 passed, 0 failed, including `test_invoice_paid_recovers_row_via_subscription_metadata` (already exercised the `Subscription.retrieve` path via a module-attribute patch, confirming the patch is still respected through `to_thread`) and multiple tests patching `stripe.Invoice.retrieve` for `_extract_invoice_payment_intent`'s fallback.
- [x] `ruff check`/`ruff format --check` — clean.
- [x] `spinr-performance-sla-reviewer` review: verdict "within SLA," confirmed exception propagation, thread safety, and executor-sharing claims (with one correction to the plan's original assumption, noted in §4 above).
- [x] Grepped for other `stripe.*.retrieve(` sites in this file — confirmed exactly these two.

## What was NOT verified

Not measured against a real production webhook burst — the "no longer blocks the event loop" claim is reasoned from `asyncio.to_thread`'s documented semantics and confirmed by a reviewing subagent, not by an actual before/after latency measurement (no staging/production access from this session).
