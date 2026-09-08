# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-08 |
| Author | Claude (session implementing PR #5085's hardening plan follow-up) |
| Surface(s) | backend |
| Domain (Sentry tag) | payments |
| PR / commit link | branch `claude/pr-5085-5079-hardening-c86-stripe-loop` |
| Related issue or gap ID | C86, re-triaged and closed on this branch; originally found during PR #5085's validation of PR #5079's F8 finding |

## 1. Issue / gap identified

F8 (fixed earlier in `routes/webhooks.py`) established that a synchronous Stripe SDK call inside an `async def` function blocks the whole worker process's event loop for the HTTP round-trip — not just the coroutine that made the call, every concurrent request and background loop on that worker. C86 tracked 9 more call sites across the codebase with the same shape, left unfixed pending triage.

Re-triaging found the original list was partly stale: 2 of the 9 (`services/stripe_payout_sync_service.py`, `services/stripe_mapping_import_service.py`) were already correctly wrapped by their own callers — the original grep-based pass found the bare `stripe.X.Y(` text but didn't check whether the enclosing function was itself thread-wrapped from outside. 1 (`services/legacy_payout_correction_service.py`'s `fire_ready_transfers`) has no live caller anywhere in the codebase — its own module docstring states it "is not wired into any route, CLI entry point, or background loop... every call is manual," so there is no shared event loop for it to block. The remaining 5 were genuinely blocking and are fixed here.

## 2. Root cause

Same as F8: none of these call sites were wrapped when written, before the event-loop-blocking cost of a synchronous Stripe SDK call was established as a project-wide convention.

## 3. Fix / remediation

Five call sites now go through `asyncio.to_thread`:

- **`utils/payment_retry.py`** (the `payment_retry (5min)` background loop) — three calls (`PaymentIntent.retrieve`, `.confirm`, `.capture`) each individually wrapped as `await asyncio.to_thread(stripe.PaymentIntent.X, args...)`.
- **`utils/reconciliation.py`**'s `_sum_stripe_intents` and **`utils/stripe_reconcile.py`**'s `_run_reconciliation_tick` — both paginate through `PaymentIntent.list()` (a manual `while` loop and `.auto_paging_iter()` respectively), issuing one blocking call per page. A single-call wrap isn't enough here since the whole loop blocks; both now extract the pagination loop into a named nested function and run the whole thing via `asyncio.to_thread(name)` — the same idiom already used successfully in `stripe_payout_sync_service.py`.
- **`services/stripe_kyc_sync.py`**'s `get_legal_name_and_address_from_stripe` — single `Account.retrieve` call, same wrap as payment_retry.py. Its caller (`routes/admin/compliance.py`'s annual T4A export) invokes this once per driver inside a loop over the whole qualifying set — infrequent (once a year) but every call in that loop blocks all other traffic on the worker while the export runs, which the original C86 note ("caller frequency not yet traced") undersold.
- **`routes/admin/dispute_evidence_submission.py`**'s `admin_submit_dispute_evidence` — single `Dispute.modify` call, a request-path admin route handler.

`backend/tests/test_stripe_event_loop_offload.py` (a pre-existing shared AST-based static checker asserting a file has zero un-wrapped `stripe.X.Y(...)` calls) was generalized to recognize a second safe idiom — a named nested function whose only caller is `asyncio.to_thread(name)` — alongside the pre-existing lambda idiom, since a multi-statement pagination loop can't be inlined as a lambda. Added coverage for all 5 fixed files, a lock-in regression test for the 2 already-correctly-wrapped files found during re-triage, and 3 synthetic tests proving the checker's own logic isn't vacuously true.

## 4. Risk & impact on existing functionality

- **Blast radius**: 5 files, each a self-contained execution-model change (sync call → threaded call). No argument, idempotency key, retry/claim sequencing, or error-handling branch was altered — only how the call executes.
- **Idempotency keys unchanged**: `payment_retry.py`'s `f"ride-confirm-{ride_id}-{intent.amount}-retry-{attempt}"` and `f"ride-capture-{ride_id}-{capture_cents}"` keys are still computed identically before the call, just passed as kwargs to `asyncio.to_thread` instead of directly to the Stripe call.
- **Atomic-claim-before-Stripe-call ordering unchanged** in `payment_retry.py`: the `db.update_one` claim (`payment_status: "retrying"`) still happens before any of the three threaded Stripe calls; nothing about the claim/release sequencing moved.
- **Exception propagation confirmed unaffected**: `asyncio.to_thread` re-raises a threaded exception into the awaiting coroutine, so every existing `try/except` block (stripe_reconcile.py's `except Exception: logger.error(...); return`; stripe_kyc_sync.py's `except Exception: ...; return None`; dispute_evidence_submission.py's `except Exception as exc: ...; await _release_claim(dispute_id)`) still fires on the same conditions as before.
- **No test behavior changes needed for the 5 application fixes**: existing tests patch `stripe.PaymentIntent.retrieve`/`.confirm`/`.capture`/`stripe.Account.retrieve`/`stripe.Dispute.modify` as module attributes; `asyncio.to_thread` resolves and calls whatever the patched attribute currently is, so every existing mock-based test kept working unmodified. Confirmed via the full 273-test run in §9.
- **`spinr-money-auditor` review**: requested for this exact concern set (behavior-neutrality, exception propagation, claim/idempotency-key integrity) — see verdict recorded below once available.

## 5. User-experience effect

None directly visible. Internal effect: the event loop no longer blocks for these five Stripe HTTP round-trips, keeping dispatch, other requests, and other background loops responsive on the same worker while a payment retry, reconciliation tick, KYC sync, or dispute-evidence submission is in flight.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/payment_retry.py` | 3 Stripe calls wrapped in `asyncio.to_thread` | Stop blocking the event loop during the 5-minute payment-retry loop |
| `backend/utils/reconciliation.py` | Pagination loop extracted to `_list_and_sum`, run via `asyncio.to_thread` | Same, for the daily reconciliation tick's PaymentIntent pagination |
| `backend/utils/stripe_reconcile.py` | Pagination loop extracted to `_list_stripe_pis`, run via `asyncio.to_thread` | Same, for the other daily Stripe-reconcile tick |
| `backend/services/stripe_kyc_sync.py` | `Account.retrieve` wrapped in `asyncio.to_thread` | Stop blocking the event loop during the admin T4A export's per-driver loop |
| `backend/routes/admin/dispute_evidence_submission.py` | `Dispute.modify` wrapped in `asyncio.to_thread` | Stop blocking the event loop on this admin request path |
| `backend/tests/test_stripe_event_loop_offload.py` | Generalized the AST checker to recognize the named-nested-function idiom; added coverage for the 5 fixes, a lock-in test for 2 already-fixed files, and 3 checker self-tests | Regression protection matching the existing convention for this class of fix |

## 7. Before / after

```python
# Before (utils/payment_retry.py)
intent = stripe.PaymentIntent.retrieve(payment_intent_id, api_key=stripe_secret)
```

```python
# After
intent = await asyncio.to_thread(stripe.PaymentIntent.retrieve, payment_intent_id, api_key=stripe_secret)
```

```python
# Before (utils/stripe_reconcile.py) -- pagination inline
stripe_pis: Dict[str, Any] = {}
for pi in _stripe.PaymentIntent.list(created={...}, limit=100).auto_paging_iter():
    stripe_pis[pi["id"]] = pi
```

```python
# After -- whole pagination loop runs in a thread
def _list_stripe_pis() -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for pi in _stripe.PaymentIntent.list(created={...}, limit=100).auto_paging_iter():
        out[pi["id"]] = pi
    return out

stripe_pis: Dict[str, Any] = await asyncio.to_thread(_list_stripe_pis)
```

## 8. Rollback plan

`git revert` — pure execution-model change (sync call → threaded call) at each of the 5 sites, no schema/data/flag coupling, no behavior change to what any call returns, raises, or is keyed by.

## 9. Verification performed

- [x] Automated tests: `pytest tests/test_stripe_event_loop_offload.py tests/test_payment_retry.py tests/test_payment_retry_coverage.py tests/test_replay_safety_payment_loops.py tests/test_payment_exhausted_alert_once.py tests/test_reconciliation.py tests/test_stripe_reconcile.py tests/test_stripe_kyc_sync.py tests/test_stripe_kyc_sync_coverage.py tests/test_compliance_reports_http.py tests/test_admin_dispute_evidence_submission.py -q` — 273 passed, 0 failed, zero test-behavior changes needed for the 5 application fixes.
- [x] `ruff check`/`ruff format --check` on all 6 edited files — clean.
- [x] Traced every existing `try/except` block around each of the 5 fixed call sites by hand to confirm the exception path is unchanged (see §4).
- [x] Grepped every caller of the 5 fixed functions to confirm none of them relies on the previous synchronous timing (none do — all are `await`ed already).
- [x] `spinr-money-auditor` subagent review: **verdict SAFE TO MERGE, no blockers, no warnings, no findings.** Independently confirmed all 5 diffs are byte-for-byte argument-identical execution-model changes; confirmed exception propagation unchanged at the two sites with existing `try/except` blocks around the now-threaded code (`stripe_reconcile.py`'s `except Exception: logger.error(...); return` and `dispute_evidence_submission.py`'s `except Exception as exc: ...; await _release_claim(dispute_id)`); confirmed `payment_retry.py`'s atomic claim still executes and returns before any of the 3 threaded Stripe calls (no reordering); confirmed both idempotency keys (`ride-confirm-...-retry-{attempt}`, `ride-capture-...`) still reference the same values computed the same way; confirmed the two pagination-loop nested functions correctly close over their pre-computed variables (`day_start`/`day_end`/`stripe_key`, `window_start`/`window_end`). Ran the full affected-test set independently (332 passed, 1 skipped) plus a targeted `dispute_evidence_submission` run (15 passed, 1 skipped) and spot-checked `stripe_payout_sync_service.py` directly to confirm it does use the idiom the generalized checker recognizes.

## What was NOT verified

Not exercised against a real Stripe API call or a real multi-worker deployment — the "no longer blocks the event loop" claim is reasoned from `asyncio.to_thread`'s documented semantics (consistent with the same claim already made and reviewed for F8's fix in `routes/webhooks.py`), not observed via an actual before/after latency measurement (no staging/production access from this session). The admin T4A export's real-world per-driver call volume (how many drivers actually qualify in a given year) was not measured — the fix is correct regardless of that count, but the practical benefit scales with it.
