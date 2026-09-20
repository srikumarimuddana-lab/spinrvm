# Change Impact & Risk Log — `/wallet/pay` charges `grand_total`, not the pre-tax subtotal

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-20 |
| Author | Claude Code session (2026-09-20 review, Phase 1 item C4) |
| Surface(s) | backend |
| Domain (Sentry tag) | payments |
| PR / commit link | branch `claude/loving-thompson-amjk8b` |
| Related issue or gap ID | 2026-09-20 review, 🚨 C4 |

## 1. Issue / gap identified

`POST /wallet/pay` was **structurally unsatisfiable on any ride carrying tax**. The route pinned the accepted amount to `total_fare` ±$0.01, while the `wallet_pay_for_ride` RPC it calls guards on `COALESCE(grand_total, total_fare, 0) − 0.02`. `total_fare` is the *pre-tax subtotal* (`services/fare_service.py`), so the two windows are disjoint whenever tax exceeds $0.02: sending `grand_total` returned `ERR_FARE_EXCEEDED` from the route, sending `total_fare` raised `fare_underpaid` inside the RPC and surfaced as `ERR_FARE_UNDERPAID`.

## 2. Root cause

Migration 111 hardened the RPC to read `grand_total` (its header records the prior gap: "`/wallet/pay` only rejected amounts ABOVE total_fare"). The route-level band was never updated to match, and no test covered it — grep for `ERR_FARE_EXCEEDED` found only the two source lines, no assertion anywhere.

## 3. Fix / remediation

`server_fare` now mirrors the RPC's COALESCE exactly:

```python
_grand = ride.get("grand_total")
server_fare = _d(_grand if _grand is not None else ride.get("total_fare", 0))
```

An explicit `is not None` check, **not** `or`: a legitimately zero `grand_total` (a fully discounted / free ride) must not fall back to a stale pre-tax subtotal and demand payment for it.

**Alternative considered:** loosen the route band to accept anything between `total_fare` and `grand_total` and let the RPC arbitrate. Rejected — it would let a rider underpay the tax on any ride where the RPC's 0.02 tolerance happened to absorb the difference, and it leaves two different definitions of "what is owed" in the codebase. One source of truth, matching the locked row the RPC reads, is the smaller and safer change.

## 4. Risk & impact on existing functionality

- **Blast radius:** `POST /wallet/pay` only. Grepped every caller of `wallet_pay_for_ride`: `routes/wallet.py` (this route) and `services/payment_service.py::settle_wallet`, which passes `grand_total` already and is untouched. The normal in-app settlement path is `routes/rides/payments.py::process_payment` → `settle_wallet`; this endpoint is the legacy/direct one migration 111 calls "the legacy `/wallet/pay` bypass".
- No schema, no migration, no background loop, no state-machine write. The RPC, its locking and its idempotent `already_paid` no-op are untouched.
- **Regression risk:** a client that was (uselessly) sending `total_fare` now gets a clear `ERR_FARE_UNDERPAID` at the route instead of a 400 from the RPC — same status and same detail string, so no client-visible contract change. A client sending `grand_total` now succeeds where it previously 400'd.
- The tip parameter is unused by this route (`WalletPayRequest` has no tip field, and `wallet_pay_for_ride` is called without one), so the RPC's `p_amount − p_tip_amount` guard reduces to `p_amount`, which is what the new band produces.

## 5. User-experience effect

- **Rider:** paying a taxed ride from the in-app wallet works. Previously it failed with a fare error no matter what the client sent. Visible immediately to anyone who has been unable to settle a wallet ride.
- **Driver / corporate / admin:** none.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/wallet.py` | `server_fare` reads `grand_total` with COALESCE semantics | match the RPC; make the endpoint satisfiable |
| `backend/tests/test_wallet.py` | four tests: taxed ride accepts `grand_total`, rejects the pre-tax subtotal, legacy no-`grand_total` row still settles, zero `grand_total` is not treated as missing | the band had no coverage at all |
| `docs/change-log/2026-09-20-wallet-pay-grand-total.md` | this file | |

## 7. Before / after

```python
# Before
server_fare = _d(ride.get("total_fare", 0))          # pre-tax subtotal
```

```python
# After
_grand = ride.get("grand_total")
server_fare = _d(_grand if _grand is not None else ride.get("total_fare", 0))
```

Concrete scenario: $40.00 fare + $4.40 tax (5% GST + 6% PST), `grand_total` $44.40.
**Before** — client sends $44.40 → route `ERR_FARE_EXCEEDED`; client sends $40.00 → RPC `fare_underpaid` → `ERR_FARE_UNDERPAID`. No path succeeds.
**After** — client sends $44.40 → route accepts, RPC's floor is $44.38, debit succeeds.

## 8. Rollback plan

Code-only, no data written, no flag: `git revert` + deploy restores the previous behaviour (which is a broken endpoint, so the rollback direction is strictly worse but harmless — no money moves incorrectly either way, because the RPC's own guard is the backstop and is unchanged).

## 9. Verification performed

- `ruff check` / `ruff format` / `py_compile` clean.
- The band was simulated directly with the interpreter for all four test cases, cross-checked against the RPC's guard arithmetic from `migrations/111_wallet_pay_for_ride_security_hardening.sql`: taxed@44.40 passes the route and clears the RPC's $44.38 floor; taxed@40.00 is refused at the route; legacy row (no `grand_total`) unchanged; zero `grand_total` refuses a $15 payment instead of accepting it.
- Traced `wallet_pay_for_ride`'s other caller (`settle_wallet`) to confirm it already passes `grand_total` and is unaffected.

## 10. What was NOT verified

- **pytest was not run** — this sandbox cannot reach PyPI, so `pytest`/`fastapi` are absent. CI is the gate: `tests/test_wallet.py::TestWalletPay`.
- Not exercised against a real Postgres: the RPC's actual behaviour on the new amount was read from the migration SQL, not observed.
- No client was checked for which amount it currently sends — if the rider app sends `total_fare` today, it will now get a clean `ERR_FARE_UNDERPAID` rather than the RPC's; it should send `grand_total`. Worth confirming before relying on this endpoint.
