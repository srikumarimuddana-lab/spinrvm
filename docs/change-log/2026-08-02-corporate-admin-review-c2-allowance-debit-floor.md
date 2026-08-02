# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-02 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | corporate, payments |
| PR / commit link | branch `claude/spinrvm-schedule-ride-review-2jsank` |
| Related issue or gap ID | Corporate + Admin Portal Review — Critical #2 |

## 1. Issue / gap identified

Allowance-funded corporate rides had no floor protection on the master
wallet — a company's real wallet balance could be pushed to any negative
number with no limit, silently.

## 2. Root cause

`services/payment_service.py::settle_corporate` calls
`corporate_allowance_service.apply_ride_debit` for the allowance-covered
portion of a ride's fare, and separately calls
`corporate_wallet_service.apply_adjustment` for the master-fallback portion.
The master-fallback call explicitly passes `floor=0.0`. The allowance-debit
call, three lines above it in the same function, passed no `floor` at all —
`apply_ride_debit`'s underlying RPC (migration 258) only enforces a floor
`IF p_floor IS NOT NULL`, so with no floor passed the check never engaged.
The per-member allowance ceiling (also migration 258) bounds one member's
spend per period, but does nothing to protect the shared master wallet
balance across the whole company — and `unlimited`-type allowances skip the
per-member cap entirely, so this path was fully unbounded for any company
using unlimited allowances.

## 3. Fix / remediation

- Pass `floor=0.0` to the `apply_ride_debit` call, matching the sibling
  master-fallback call in the same function.
- The RPC can now raise a `wallet_below_floor` exception from this call site
  (previously only possible from the master-fallback call). Extended the
  existing exception handler — which already distinguishes
  `allowance_cap_exceeded` (reroute to master wallet) — with a new branch for
  `wallet_below_floor`: rerouting to the master-fallback debit would be
  pointless here (it's the same wallet, it would hit the identical floor
  immediately), so this case fails the settlement cleanly instead — ride left
  `payment_status="pending"`, a clean `PaymentResult(success=False,
  status_code=503)` returned, nothing left uncaught.

## 4. Risk & impact on existing functionality

- **Blast radius: one function, `settle_corporate`, specifically its
  allowance-debit branch and its exception handler.** No other caller of
  `corporate_allowance_service.apply_ride_debit` exists outside this file
  (grepped `apply_ride_debit` across `backend/services/*.py` and
  `backend/routes/*.py`).
- **Found and fixed one existing test that explicitly asserted the old,
  buggy behavior**: `test_allowance_cap_fallback.py::test_non_cap_error_still_raises`
  used `wallet_below_floor` as its example of "an error that must not be
  swallowed and must still raise" — that was accurate for the *old* code,
  but is now the exact case this fix changes on purpose. Renamed to
  `test_unrecognized_error_still_raises` and switched its example error to a
  genuinely unrecognized one (`"wallet not found: wallet_1"`), preserving the
  original invariant ("truly unknown errors still raise") without asserting
  the now-intentionally-changed behavior. Added a new test,
  `test_allowance_debit_below_floor_fails_cleanly_instead_of_rerouting`, for
  the new graceful-failure path.
- Every other test that mocks `apply_ride_debit` either doesn't assert exact
  call kwargs (only checks `member_id` or call-count) or mocks it as an
  unconditional success — grepped all call sites across `backend/tests/*.py`
  — so adding the new `floor` kwarg doesn't affect them.
- No interaction with the C1 fix beyond sharing the same function; both are
  independent, narrowly-scoped changes to `settle_corporate`.

## 5. User-experience effect

**Corporate riders on companies whose master wallet is already at or below
its floor**: an allowance-funded ride that would have previously succeeded
by silently overdrawing the company's wallet now fails at settlement with a
retry-able error, instead of the company's balance going further negative
with no visibility. This is the same outcome the master-fallback path
already produced in the equivalent situation — this fix makes the allowance
path consistent with it, not a new failure mode. **No effect** on any
company with a healthy wallet balance.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/services/payment_service.py` | `apply_ride_debit` call now passes `floor=0.0`; exception handler gained a `wallet_below_floor` branch that fails cleanly instead of rerouting or raising uncaught | Close the unbounded-negative-wallet gap; handle the new exception path gracefully |
| `backend/tests/test_allowance_cap_fallback.py` | Renamed/repurposed the stale "non-cap error still raises" test to use a genuinely unknown error; added a new test for the graceful floor-breach path | Fix a test that explicitly asserted the old buggy behavior; cover the new behavior |

## 7. Before / after

```python
# Before
await corporate_allowance_service.apply_ride_debit(
    wallet_id=corp_wallet["id"],
    allowance_id=allowance["id"],
    member_id=membership["id"],
    amount=_f(allowance_debit),
    actor_user_id=membership.get("user_id") or ride.get("rider_id"),
    notes=f"ride:{ride_id}:allowance",
)
```

```python
# After
await corporate_allowance_service.apply_ride_debit(
    wallet_id=corp_wallet["id"],
    allowance_id=allowance["id"],
    member_id=membership["id"],
    amount=_f(allowance_debit),
    actor_user_id=membership.get("user_id") or ride.get("rider_id"),
    notes=f"ride:{ride_id}:allowance",
    floor=0.0,
)
```

```python
# Before — exception handler only recognized one error shape
if "allowance_cap_exceeded" in f"{_cap_err} {_detail}":
    master_debit = total
    allowance_debit = _round(Decimal("0"))
else:
    raise
```

```python
# After — a floor breach fails cleanly instead of rerouting or raising uncaught
if "allowance_cap_exceeded" in _cap_err_text:
    master_debit = total
    allowance_debit = _round(Decimal("0"))
elif "wallet_below_floor" in _cap_err_text:
    await db_supabase.update_ride(ride_id, {"payment_status": "pending"})
    return PaymentResult(success=False, error="Corporate payment failed — please retry.", status_code=503)
else:
    raise
```

## 8. Rollback plan

Plain code change, no migration, no data written by this fix itself. `git
revert` fully restores prior behavior. No feature flag — floor protection
either applies or it doesn't; there's no meaningful dark-ship version of "the
master wallet floor check now also runs for the allowance-debit path." A
company already sitting below zero from before this fix is unaffected by the
fix itself (it doesn't retroactively touch existing balances) — reconciling
an already-negative balance is a separate, manual finance action, not
something this code change performs.

## 9. Verification performed

- [x] Automated tests: `test_allowance_cap_fallback.py` (4 tests, 1 renamed,
      1 new), `test_corporate_ride_payment.py` (19 tests, unaffected),
      `test_corporate_settle_suspended_audit_flag.py` (6 tests, unaffected),
      `test_allowance_rpc_sign_contract.py` (4 tests, unaffected) — 30 passed
      total, via the session's `/tmp/spinr_venv` venv.
- [x] `ruff check` on both touched files — clean.
- [ ] Manual repro in staging — not performed, no staging access.
- [x] Blast-radius grep performed (see §4): every caller of
      `apply_ride_debit`, every test that mocks or asserts on it.
- [x] Dry-run scenario: a company's master wallet balance is $0.00 (at
      floor). A member with a `fixed_recurring` allowance that still has
      remaining budget on paper takes a $20 ride. Before this fix: the
      allowance debit succeeds unconditionally, the wallet goes to -$20.00,
      nothing alerts. After this fix: the RPC raises `wallet_below_floor`,
      the ride is left `pending`, the rider/ops sees a retry-able failure
      instead of an invisible negative balance.

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed — one function, one exception
      handler, one test file with a stale assertion identified and fixed
- [x] No silent behavior change to a working flow — a healthy-balance
      company sees no difference; only the specific overdraft case changes,
      which is the fix's entire purpose

## What was NOT verified

Not tested against a live/staging Supabase instance — only mocked RPC
responses; the actual Postgres function's floor-check SQL (migration 258)
was read but not executed against a real database in this session. Did not
audit whether any company's master wallet is currently already negative in
production as a result of this gap — that's a one-off data question for
finance/ops, separate from this code fix, and this fix does not retroactively
correct any existing negative balance.
