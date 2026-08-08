# Change Impact & Risk Log — Flagged atomic card settlement

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-07 |
| Author | Claude Code (agent), branch `claude/stripe-rider-payment-arch-o5vyes` |
| Surface(s) | backend |
| Domain (Sentry tag) | payments |
| Related issue or gap ID | W3 in the error-free plan; migration 288 (`9036934`) |

## 1. Issue / gap identified

After a successful Stripe capture/charge, settlement finished with two separate DB writes: the `financial_events` header, then `update_ride` to `paid`. Process death between the Stripe call and those writes — or between the two writes — left either a paid ride with no 7-year tax-ledger row, or a header with the ride stuck in `processing`. The 2026-08-06 retry work narrowed this window; it could not close it.

## 2. Root cause

Two independent PostgREST round-trips can never be atomic from the client side. Both rows live in the same Postgres, so the fix is a SECURITY DEFINER function — the same pattern `wallet_pay_for_ride` already established for the wallet path.

## 3. Fix / remediation

- **`repositories/ledger_repo.py`** — bespoke wrapper for the migration-288 RPC. `SettleRpcUnavailable` subclasses **ValueError deliberately**: `run_sync` wraps every exception except ValueError into a generic `DatabaseError`, which would erase the fallback signal (the same reason `wallet_repo` translates RPC errors to ValueError — discovered when the test caught the wrapped exception). `retry_policy="idempotent_write"`: a transport retry re-sends the same `p_event_id`, which the RPC dedupes, shrinking the ambiguous-error surface.
- **`payment_service._finalize_card_settlement`** — shared post-charge finalizer for both success paths (capture-hold and fresh-charge), guaranteeing **exactly one header per settlement**:
  - Flag off (default) → the legacy sequence, byte-compatible.
  - Flag on → the RPC owns both writes; `record_payment_event` and the money `update_ride` are skipped. Display-only fields (card repoint, released-hold marker) follow best-effort.
  - RPC absent → legacy + `atomic_settle_fallback` warning (partial-deploy safe).
  - Ambiguous RPC error → re-read decides: paid ⇒ committed (header verified by `ref` as defence-in-depth, repaired if impossibly missing); not paid ⇒ legacy sequence; **re-read also fails ⇒ 503 "confirmation failed"** — never a blind legacy run, because if the RPC had committed that would write a second header (each attempt carries a fresh event id; the cross-attempt dedup is the RPC's paid-gate, not `ON CONFLICT(id)`).
- **`schemas.py`** — `ledger_atomic_settle_enabled: bool = False`.
- RPC `None` (already paid) → `already_paid` result, no WS/receipt re-send.

## 4. Risk & impact on existing functionality

**Blast radius:** the two `settle_card` success paths only. Wallet (`wallet_pay_for_ride`) and corporate (`corporate_wallet_apply_delta`) settles were already atomic and are untouched; webhook settles route through `record_payment_event` unchanged.

- **Flag off — production today — is the legacy sequence verbatim**: same writes, same order, same stuck-processing 503 contract. 339 tests across the settle battery pass unchanged.
- Flag interaction matrix stays clean: the RPC never writes legs; the projection never reads this flag.
- The hold path's DB-failure log line is now shared with the fresh path ("Charge {pi} succeeded…" rather than "Capture {pi} succeeded…") — log-text drift only, same level/structure.
- SLA: the flag-on path replaces two sequential awaited writes with one RPC round-trip — P95 fare settlement (< 1 s target) improves.
- Money arithmetic: cents cross as `int`, Decimals as `str` — never float (asserted in the wrapper test).

## 5. User-experience effect

Nobody — backend-only. Failure shapes seen by the rider app are unchanged (same codes/messages).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/repositories/ledger_repo.py` | **New.** RPC wrapper + `SettleRpcUnavailable(ValueError)` | Money-path error translation |
| `backend/services/payment_service.py` | `_charge_event_metadata` extracted; `_finalize_card_settlement` + `_atomic_settle_enabled` + `_send_payment_completed_ws`; both success paths rewired | One header per settle, flag + fallback |
| `backend/schemas.py` | `ledger_atomic_settle_enabled: bool = False` | DB-backed kill switch |
| `backend/tests/test_atomic_settle.py` | **New.** 12 tests: flag matrix, fallback, all four ambiguous-error outcomes, wrapper translation, str-not-float | |

Note: the lazy dual imports inside the new helpers are deliberate — the module-level `except ImportError` import list is rewritten by a formatter hook that strips additions (observed twice this branch); function-body imports are immune.

## 7. Before / after

```python
# Before — two writes; death between them loses one side
await record_payment_event(...)          # header
await db_supabase.update_ride(ride_id, {"payment_status": "paid", ...})
```

```python
# After (flag on) — one transaction commits both or neither
event_id = str(uuid.uuid4())
result = await ledger_repo.settle_ride_card_payment(
    ride_id=..., event_id=event_id, amount_cents=..., tip_amount=..., metadata=..., ...
)  # → event id | None (already paid) | SettleRpcUnavailable → legacy fallback
```

## 8. Rollback plan

`ledger_atomic_settle_enabled = false` in app_settings — callers return to the legacy path within the 60 s settings cache TTL, no deploy. Rows written by either path are identical to every reader (reconciliation, receipts, admin). Function drop SQL in migration 288's header.

## 9. Verification performed

- New `test_atomic_settle.py` — 12 passed, covering the full flag matrix, all four ambiguous-error outcomes, and wrapper error translation. The wrapper test **caught a real bug pre-commit**: `run_sync` was wrapping `SettleRpcUnavailable` into `DatabaseError`, which would have turned every missing-function fallback into an ambiguous-error path.
- Settle battery (`test_atomic_settle`, `test_settle_card_capture`, `test_process_payment_card`, `test_coverage_payments`, `test_coverage_rides`, `test_ledger_service`, `test_ledger_projection`, `test_payment_retry`, `test_stripe_charge_coverage`) — **339 passed**.
- `ruff check` / `format --check` clean.
- Full backend suite (~10k tests): **started before the push, still running when the
  branch was pushed** (feature branch, not a merge). Result recorded in a follow-up
  commit — see `docs/change-log/2026-08-07-full-suite-result.md`. Targeted batteries
  for this change were green before commit (listed above).

## 10. What was NOT verified

> **UPDATE 2026-08-08 — the database layer of this gap is now CLOSED.** The repo owner applied migrations 286–291 to a real Postgres and ran `backend/scripts/verify_migrations_286_291.sql`; **all checks passed**. See `docs/change-log/2026-08-08-migration-verification-result.md` for exactly what that proved and what it did not. The items below are corrected in place; anything still outstanding is called out there.


- ~~The RPC itself has never executed against a real Postgres.~~ **Executed and asserted 2026-08-08**: the paid-gate returns NULL on replay without writing a second header, the header lands in the same transaction as the ride flip, a downward tip correction claws back `driver_earnings`, earnings clamp at zero, and unknown-ride / negative-amount are rejected. This was the one piece of new logic whose money-correctness claim rested purely on code review. **Still required before production enablement: a real settle with the flag on** — the Python↔RPC round trip (notably `p_metadata` JSONB encoding through supabase-py, and `ledger_repo`'s error translation against a real PostgREST error) is still only exercised against mocks.
- The ambiguous-error recovery is exercised against mocks; a genuine mid-transaction connection drop against live Supabase has not been reproduced.
- `p_metadata` JSONB round-tripping through supabase-py's RPC param encoding is assumed (dict → JSON), not verified against a live PostgREST.
