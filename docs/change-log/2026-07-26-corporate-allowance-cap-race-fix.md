# Change Impact & Risk Log

> **Backfilled 2026-07-28** — this entry documents commit `2d9e673` /
> migration 258, merged 2026-07-26. It shipped with a good commit message
> but without the structured Change Impact & Risk Log CLAUDE.md requires for
> money-touching fixes. Written after the fact from the actual diff and
> migration SQL (`git show 2d9e673`, `backend/migrations/258_corporate_allowance_cap_in_rpc.sql`) —
> not reconstructed from memory. Identified as a gap by the 2026-07-28
> structured SDLC audit of the Corporate billing module.

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-26 (original fix), backfilled 2026-07-28 |
| Author | Claude (original fix) |
| Surface(s) | backend |
| Domain (Sentry tag) | corporate / payments |
| PR / commit link | `2d9e673bd8d5c31445bf911a43f9423d07dfff24` |
| Related issue or gap ID | P1 fix at the time; flagged retroactively as a Change-Impact-Log gap by the 2026-07-28 structured audit |

## 1. Issue / gap identified

`settle_corporate` (fare settlement for corporate-account rides) computed `allowance_debit = min(remaining, total)` from a **non-locking read** of `corporate_member_allowances.used`, then called the `ride_debit` RPC. The RPC held a row lock but, prior to this fix, only guarded the master-wallet floor — it never checked `used` against the per-member allowance ceiling (`amount`).

## 2. Root cause

Two rides for the same corporate member settling concurrently could both read the same stale `used` value before either write landed, both independently decide "this debit fits under the cap," and both apply it. Because the ceiling check lived in application code reading a value that could go stale between read and write, `used` could end up above `amount` — silently bypassing the per-employee allowance cap. This is a classic check-then-act race: the row lock existed (it protected the master wallet floor), but the specific invariant that mattered here (per-member ceiling) wasn't inside the locked section.

## 3. Fix / remediation

Migration 258 moves the ceiling check **inside** `corporate_allowance_apply_delta`, under the `FOR UPDATE` row lock the function already takes on `corporate_member_allowances` (it now reads `amount` alongside `used` in the same locked `SELECT`). On breach it `RAISE`s `allowance_cap_exceeded`. `settle_corporate` catches that specific exception and reroutes the **entire fare** to the company master wallet — the existing fallback path for allowance-insufficient rides — so total dollars billed stays correct; only the per-member ceiling enforcement changes. Unlimited allowances (`amount IS NULL`) are never capped. `flag_violation` (an `allowance_only` policy-breach flag) is recomputed *after* the fallback resolves, since the reroute itself can newly trigger that policy violation.

## 4. Risk & impact on existing functionality

- **What else reads/writes `corporate_member_allowances`/`corporate_wallets`?** `corporate_allowance_service.py` (all CRUD on allowances), `corporate_wallet_service.py` (master wallet CRUD — deterministic lock order is master-wallet-first, then allowance, to avoid deadlock with this function), the 2 background loops `utils/corporate_autotopup.py` and low-balance nudge, admin wallet views, and T4A/billing statement export. None of these were modified by this fix; the RPC signature and `RETURNS TABLE` shape are unchanged (`CREATE OR REPLACE`), so no caller needed updating.
- **Could this regress a working flow?** The intended, desired regression is: a corporate ride that would previously have silently over-spent a capped allowance now instead routes to the master wallet and (if the company policy is `allowance_only`) gets flagged. This is the fix working as intended, not a side effect.
- **Blast radius:** single-surface (backend), isolated to the corporate fare-settlement path. No ride-state-machine interaction (`settle_corporate` fires only at `completed`→payment settlement, doesn't touch ride status).
- **Background loop interaction:** none — this is a synchronous settlement-path fix, not a background loop.
- **Money/wallet deltas:** yes, directly — this is the core of the change. Deltas are still correctly summed (total billed = allowance_debit + master_debit either way); what changes is which bucket absorbs the charge under contention.

## 5. User-experience effect

- **Who sees a difference:** corporate admin (billing view — an over-cap employee ride now shows as master-wallet-charged instead of allowance-charged, and may trigger an `allowance_only` policy-violation flag it previously wouldn't have). No rider/driver-facing change — the ride itself settles identically from their point of view.
- **Mid-session visible?** No — this only affects the settlement computation at trip-end; a rider or driver mid-ride sees nothing different.
- **Copy/notification change:** none.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/258_corporate_allowance_cap_in_rpc.sql` | `CREATE OR REPLACE` on `corporate_allowance_apply_delta`: reads `amount` alongside `used` under the existing row lock; raises `allowance_cap_exceeded` on breach for `ride_debit` type only, skipped for unlimited (`amount IS NULL`) allowances | Move the ceiling check inside the atomic locked section so it can't be bypassed by a stale application-side read |
| `backend/services/payment_service.py` | `settle_corporate`: wraps the `apply_ride_debit` call in try/except; on `allowance_cap_exceeded` specifically, reroutes the full fare to the master wallet and zeroes the allowance debit; moved `flag_violation` computation to after this resolves | Handle the new exception path without breaking the settlement — total billed stays correct, only the allowance/master split changes under contention |
| `backend/tests/test_allowance_cap_fallback.py` | New: `test_cap_exceeded_routes_fare_to_master`, `test_non_cap_error_still_raises` | Verify the Python-side catch/fallback/re-raise behavior |

## 7. Before / after

```python
# Before
flag_violation = master_debit > 0 and corp_policy.get("allowed_payment_source") == "allowance_only"

allowance_applied = False
if allowance_debit > 0 and allowance.get("id") and corp_wallet.get("id"):
    await corporate_allowance_service.apply_ride_debit(
        wallet_id=corp_wallet["id"],
        allowance_id=allowance["id"],
        member_id=membership["id"],
        amount=_f(allowance_debit),
        actor_user_id=membership.get("user_id") or ride.get("rider_id"),
        notes=f"ride:{ride_id}:allowance",
    )
    allowance_applied = True
```

```python
# After
# flag_violation is computed AFTER the allowance debit resolves — the cap
# fallback below can flip master_debit from 0 to the full fare, which is
# itself an allowance_only policy breach that must be flagged.

allowance_applied = False
if allowance_debit > 0 and allowance.get("id") and corp_wallet.get("id"):
    try:
        await corporate_allowance_service.apply_ride_debit(...)
        allowance_applied = True
    except Exception as _cap_err:
        # allowance_cap_exceeded means a concurrent settle already filled the
        # cap between our non-locking read and the RPC's row lock — reroute
        # the whole fare to master wallet instead of over-spending.
        if "allowance_cap_exceeded" in f"{_cap_err} {...}":
            logger.warning("corporate allowance cap hit under contention...")
            master_debit = total
            allowance_debit = _round(Decimal("0"))
        else:
            raise

flag_violation = master_debit > 0 and corp_policy.get("allowed_payment_source") == "allowance_only"
```

## 8. Rollback plan

Migration SQL's own top comment specifies the rollback explicitly: "re-apply migration 248's body (the version without the `ride_debit` ceiling guard). No schema change — this only replaces the function body." That is a `CREATE OR REPLACE` back to the prior function definition — safe to run against production traffic in flight per the append-only/forward-compatible migration convention.

**Caveat inherited from CLAUDE.md's own money-rollback rule:** rolling back the function does *not* undo any allowance/master-wallet deltas already applied while either version was live. If this fix itself needs to be rolled back after having caught real contention (i.e., after having correctly rerouted some fares to master wallet), those already-settled rides are not retroactively re-split — a `git revert` / migration rollback alone only stops the *behavior* going forward, consistent with the documented limitation that money-state changes need more than a code revert once applied.

## 9. Verification performed

- [x] Automated tests run: unit — `backend/tests/test_allowance_cap_fallback.py` (2 tests: cap-exceeded fallback path, non-cap-error re-raise path).
- [ ] Manual repro steps followed in staging — not documented in the original commit.
- [ ] Blast-radius grep performed — not documented in the original commit (this backfill's own §4 above supplies it retroactively via a fresh grep in the 2026-07-28 audit, not from the original PR).
- [x] Reviewed against relevant CLAUDE.md convention(s): money arithmetic uses `Decimal`/`_round`/`_f()` throughout (verified in the diff — `master_debit = total`, `allowance_debit = _round(Decimal("0"))`); RPC remains `SECURITY DEFINER` with `search_path` pinned (unchanged from migration 248).
- [ ] Feature-flagged — not applicable; this is a correctness fix to prevent an existing double-spend path, not a new user-visible feature, so a flag would only reintroduce the bug when disabled.

## 10. What was NOT verified (then or in this backfill)

- **No test exercises the actual Postgres-level race** — `test_allowance_cap_fallback.py` tests the Python-side catch/fallback given that the RPC *already raised* `allowance_cap_exceeded`; it does not spin up two concurrent calls against a real (or even simulated) `FOR UPDATE` lock to prove the SQL fix itself prevents the race, since the test suite mocks Supabase rather than hitting real Postgres. This is tracked separately as an open P0 item from the 2026-07-28 audit (add a locking/race regression test for `corporate_allowance_apply_delta`).
- Not verified whether any other call site applies a similar non-locking-read-then-RPC-call pattern against `corporate_wallets`/`corporate_member_allowances` outside `settle_corporate` — `corporate_wallet_service.py`'s `_apply`/`apply_topup`/`apply_adjustment`/`apply_refund` all appear to call the RPC directly without a pre-computed application-side split, but this was not independently re-traced line-by-line in this backfill.
- Production incident history was not checked — this backfill does not establish how many real corporate rides (if any) were affected by the original race before the fix shipped.

## 11. Sign-off

- [x] Rollback plan is concrete and testable (documented in the migration's own top comment; caveat about already-applied deltas stated above).
- [x] Blast radius is stated, not assumed (§4, backfilled from a fresh grep).
- [x] No silent behavior change to an already-shipped flow without the UX field filled in (§5 above).
