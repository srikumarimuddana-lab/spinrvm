# Change Impact & Risk Log — Durable ledger writes for cancellation fees

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-07 |
| Author | Claude Code (agent), branch `claude/stripe-rider-payment-arch-o5vyes` |
| Surface(s) | backend |
| Domain (Sentry tag) | payments |
| Related issue or gap ID | "Still outstanding for the other three writers" in `docs/architecture/payments-rider-stripe.md` gap 2; W1 in the error-free plan |

## 1. Issue / gap identified

The two cancellation-fee ledger writers still used the original insert-and-swallow pattern: one attempt, `except Exception: logger.error(...)`, charge proceeds unrecorded on failure. Exactly the defect fixed for `payment_service` on 2026-08-06, surviving in `routes/rides/cancellation.py` (cancellation fee at `:219`, scheduled-cancel notice-window fee at `:490`). A blast-radius recheck found the third suspected writer (webhooks.py) already routes through the durable path — nothing to migrate there.

## 2. Root cause

The 2026-08-06 durability work deliberately migrated only the two `payment_service` writers to keep that diff reviewable; these two call sites were documented as outstanding.

## 3. Fix / remediation

Both sites now call `ledger_service.record_event` (via a new `record_ledger_event` / `ledger_to_cents` re-export in `routes/rides/_deps.py`, keeping the rides package's patch-through-`_deps` testing model). That buys: 3 retries with client-supplied PK (duplicate-key = success), Sentry escalation `spinr_alert=ledger_write_failed` on exhaustion, never-raises contract. The cancellation-fee event's metadata now also carries `fee_admin` / `fee_driver` — load-bearing for the upcoming double-entry projection loop (W2), which decomposes the fee from metadata rather than re-deriving the split.

No `legs=` is passed at these sites: W2 moves all leg-writing to the projection loop one commit later, and `ledger_double_entry_enabled` is off in production.

## 4. Risk & impact on existing functionality

**Blast radius: two call sites inside one file, plus a re-export module.**

- Both writes sit inside best-effort fee blocks that already guaranteed the cancel itself is never blocked; `record_event` never raises, so that contract is unchanged. Failure behavior *improves*: retried, then escalated, instead of logged-and-lost.
- `record_event` stamps its own `id`/`created_at`; the row shape gains an `id` key and two metadata keys. The only reader that inspects these rows programmatically is the daily reconciliation sum (`delta_cents` by `event_type`) — unchanged semantics, identical `delta_cents` arithmetic (`to_cents` is the same Decimal→cents conversion as the previous `int(_round(fee * 100))`).
- The rides package's other consumers of `_deps` are unaffected — additive re-export only.

## 5. User-experience effect

Nobody — backend-only. Rider-visible cancel flow, fee amounts, and receipts unchanged.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/rides/_deps.py` | Added `record_ledger_event` / `ledger_to_cents` re-exports (both import branches) | Patch-through-`_deps` model |
| `backend/routes/rides/cancellation.py` | Both fee writers → `record_ledger_event`; dropped call-site try/except + `created_at`; added `fee_admin`/`fee_driver` metadata | Durability + W2 decomposition input |
| `backend/tests/test_cancellation_fee_card_charge.py` | Ledger assertion repointed from `_deps.db.insert_one` call-inspection (passed by module-identity accident) to patching `record_ledger_event`; new containment test (ledger loss never blocks cancel / never unmarks paid) | Remove fragile-by-accident patching |
| `backend/tests/test_ride_cancellation_branches.py` | Notice-fee test now patches + asserts `record_ledger_event` (its write previously escaped the test's mocks into the real binding) | Make the ledger write observable |

## 7. Before / after

```python
# Before (both sites) — one attempt, swallowed
try:
    await _deps.db_supabase.insert_one("financial_events", {...})
except Exception:
    logger.error("... charge succeeded but is unrecorded", exc_info=True)
```

```python
# After — durable writer; fee split carried for the projection
await _deps.record_ledger_event(
    event_type="stripe_charge", user_id=..., ride_id=ride_id,
    delta_cents=_deps.ledger_to_cents(total_cancel_fee),
    ref=outcome.payment_intent_id,
    metadata={"source": "cancellation_fee", "driver_id": ...,
              "fee_admin": str(_round(charged_admin)),
              "fee_driver": str(_round(charged_driver))},
)
```

## 8. Rollback plan

`git revert` is sufficient: no schema, no flags, no data mutated — only which code path performs an INSERT whose row shape is compatible in both directions. Rows written by either version are read identically by reconciliation.

## 9. Verification performed

- Targeted: `test_cancellation_fee_card_charge.py` + `test_ride_cancellation_branches.py` + `test_ledger_service.py` — 39 passed; remaining cancellation files (`test_c2_driver_cancel_atomic.py`, `test_e2e_cancellation.py`, `test_preauth_release_on_cancel.py`, `test_scheduled_cancel_notice_fee.py`) — 41 passed.
- `ruff check` / `format --check` clean on all four files.
- Blast-radius grep: `financial_events` across backend/ re-run; confirmed webhooks.py has zero direct writes (routes via `record_payment_event`); the only remaining direct `insert_one("financial_events", ...)` callers are now inside `ledger_service` itself.
- Full backend suite before push.

## 10. What was NOT verified

- Not run against a real Supabase (unit tier only, consistent with the file's existing tests).
- The metadata `fee_admin`/`fee_driver` consumer (W2 projection) does not exist yet; the keys are asserted in tests but nothing reads them until W2 lands.
