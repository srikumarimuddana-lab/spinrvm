# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-11 |
| Author | Claude Code (spinr platform) |
| Surface(s) | backend |
| Domain (Sentry tag) | corporate, payments |
| PR / commit link | (this branch — see commit adding this file) |
| Related issue or gap ID | Corporate scheduled-ride audit (`spinr-corporate-billing-reviewer`), P0 finding #2, plus a regression found by `spinr-migration-reviewer` during this fix's own review |

## 1. Issue / gap identified

Two issues, addressed together because fixing the first required redefining the same Postgres
function the second lives in:

1. **No ride-scoped idempotency.** `corporate_wallet_apply_delta` and `corporate_allowance_apply_delta`
   — the two RPCs that move money for a corporate ride settlement — had no way to tell "this ride's
   debit already happened" from "this ride hasn't been charged yet." A retried `settle_corporate`
   call for the same ride could debit the allowance and/or the master wallet a second time.
2. **Per-employee allowance cap silently unenforced.** Found while tracing the *true current* body of
   `corporate_allowance_apply_delta` for fix #1 (migration history is non-linear — see §2): migration
   277 based its `CREATE OR REPLACE` on migration 248's body instead of 258's/261's, silently dropping
   the per-member ceiling guard (`v_cap` / `allowance_cap_exceeded`) migration 258 had added. This has
   been live since 277 shipped.

## 2. Root cause

**Idempotency:** `settle_corporate` (`services/payment_service.py`) has no outer `try/except`.
Several steps after the wallet/allowance debits succeed are unguarded (the `ride_payment_sources`
insert, the final `payment_status='paid'` write) — a failure there leaves the ride debited but stuck
at `payment_status='processing'`. Today a caller-side guard in `routes/rides/payments.py` (skip
re-driving `'processing'` for non-wallet payment methods) happens to prevent a second call from ever
landing — but that guard also means the ride never reconciles, and provides zero protection at the
RPC layer: a future fix that re-drives stuck corporate rides (a plausible fix for exactly this
symptom) would reintroduce a real double-debit.

**Cap regression:** `corporate_allowance_apply_delta` has been redefined via `CREATE OR REPLACE`
five times across its history (29 → 203 → 214 → 248 → 258 → 261 → 277). Migration 277 needed to fix
an unrelated bug (grant-funded rides being double-charged) and copied its base body from 248 — the
migration two before the one (258) that had added the cap guard — rather than from 261 (the most
recent at the time, which still carried the guard). No test caught this: `test_corporate_allowance_cap_race.py`
tests a hand-ported copy of the locked-section algorithm, not the real function, so it kept passing
throughout.

## 3. Fix / remediation

New migration `297_corporate_rpc_ride_idempotency.sql` (DROP + `CREATE OR REPLACE`, required because
both functions' `RETURNS TABLE` shape changes):

1. `corporate_wallet_apply_delta`: the existing `p_ride_id` parameter is now also used for dedup
   (previously storage-only). A repeat call with the same `(wallet_id, ride_id, type, scope)` —
   when `p_stripe_pi` is absent — returns the original transaction untouched.
2. `corporate_allowance_apply_delta`: gains a new `p_ride_id UUID DEFAULT NULL` parameter, threaded
   into both ledger rows it writes (master + member). Same dedup semantics, keyed on
   `(wallet_id, ride_id, type, member_id)`.
3. Both functions return a new `deduped BOOLEAN` output column (mirrors `wallet_apply_delta`,
   migration 249).
4. Both functions' dedup checks now run **after** the row lock (`FOR UPDATE`), not before — closes a
   TOCTOU window where two concurrent identical calls could both pass the dedup check before either
   had inserted.
5. **Restored** the migration-258/261 per-member allowance-cap guard (`v_cap` / `allowance_cap_exceeded`)
   that migration 277 had silently dropped — bundled into this same redefinition rather than a
   separate migration, per explicit confirmation (this function was already being redefined for
   fix #1; restoring the guard here is a ~15-line addition with no extra schema risk, versus leaving
   a known live money-cap gap open for a second migration cycle).

Service-layer changes (`services/corporate_wallet_service.py`, `services/corporate_allowance_service.py`,
`services/payment_service.py`): thread `ride_id` from `settle_corporate` into `apply_ride_debit`,
`apply_adjustment`, and `apply_ride_debit_reversal`. `corporate_allowance_service.py`'s `_apply()`
only includes the `p_ride_id` key in the RPC payload when a caller actually passes one — see §4 for
why this matters.

## 4. Risk & impact on existing functionality

- **Blast radius, idempotency change: two RPCs, three service-layer call sites, one caller
  (`settle_corporate`).** Grepped every caller of `apply_adjustment`/`apply_ride_debit`/
  `apply_ride_debit_reversal`/`apply_grant`/`apply_reset`/`apply_rollback`/`apply_topup`/`apply_refund`
  across the codebase: `corporate_wallet_winddown_service.py` (`apply_adjustment`, no `ride_id` —
  unaffected), `routes/corporate_wallet.py` (admin manual adjustment, no `ride_id` — unaffected),
  `routes/corporate_rider.py` + `routes/corporate_company.py` (`apply_grant`, no `ride_id` —
  unaffected), `utils/allowance_reset.py` (`apply_reset`, no `ride_id` — unaffected),
  `routes/webhooks.py` (`apply_topup`, uses `stripe_payment_intent_id` dedup, unaffected),
  `scripts/seed_corporate_test_data.py` (`apply_topup`, dev-only — unaffected). Only
  `services/payment_service.py`'s three settlement calls now pass `ride_id`.
- **Blast radius, cap-guard restoration: `corporate_allowance_apply_delta` callers only** — the same
  three service-layer functions above. The guard only fires for `p_type = 'ride_debit'`, which only
  `apply_ride_debit` sends. `apply_grant`/`apply_reset`/`apply_rollback` are unaffected by this half
  of the fix.
- **This is a real behavior change for the cap-guard half, not purely additive.** A ride that would
  push a member's `used` over their allowance `amount` now raises `allowance_cap_exceeded` again —
  `payment_service.py`'s existing exception handler for this exact string (routes the fare to the
  master wallet instead) has been live/tested since migration 258 originally shipped; restoring the
  guard reactivates that handler rather than requiring new application code. See §7 for the exact
  code path.
- **Deploy-ordering hazard (found by `spinr-migration-reviewer` review of this fix).**
  `corporate_allowance_apply_delta`'s new `p_ride_id` parameter doesn't exist in the pre-297 function
  signature. PostgREST resolves RPC calls by exact named-parameter match — a call carrying a key the
  function doesn't declare fails to resolve entirely (`function ... does not exist`), not "ignores
  the extra key." If the new backend code deploys to an environment before migration 297 has been
  applied there, every `ride_debit`/`ride_debit_reversal` call (i.e. every corporate ride settlement)
  fails until the migration lands. Mitigated two ways: (a) `corporate_allowance_service.py`'s `_apply()`
  now only includes the `p_ride_id` key when a real ride_id is passed, so `apply_grant`/`apply_reset`/
  `apply_rollback` are safe regardless of deploy order; (b) a new runbook
  (`docs/runbooks/deploy-migration-297.md`) documents the mandatory apply-migration-before-deploy
  sequence, mirroring `docs/runbooks/deploy-migration-64-65.md`'s precedent for this exact class of
  risk. `corporate_wallet_apply_delta` is unaffected either way — its `p_ride_id` parameter has
  existed since migration 28.
- **No interaction with the ride state machine, WS events, or insurance periods.** Purely a
  money-ledger change at settlement time, which happens after a ride reaches `completed`.
- **Idempotency dedup does not change what a fresh (non-retried) settlement does** — `deduped` is
  `FALSE` and the full apply path runs exactly as before for every first-time call.

## 5. User-experience effect

- **Corporate riders / company admins:** none directly visible mid-ride. The cap-guard restoration
  means a ride that would breach a capped employee's per-period allowance now correctly falls back to
  billing the company's master wallet instead of silently over-spending the employee's cap — this is
  a billing-correctness fix, not a new rider-facing flow; the rider experience (ride completes, gets
  a receipt) is unchanged either way.
- **Internal/finance ops:** a retried settlement for a stuck-`processing` corporate ride will now
  resolve safely (logged at `info` level: "was already applied (idempotent retry, migration 297)")
  instead of risking a double-charge if that re-drive capability is ever added.
- Not visible mid-session to anyone already using the app.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/297_corporate_rpc_ride_idempotency.sql` | New migration: ride-scoped dedup + `deduped` output column on both corporate money RPCs; lock-then-check reordering; restores the migration-258/261 allowance-cap guard | Close the double-debit landmine (P0 #2) and, once discovered, the cap regression in the same redefinition |
| `backend/services/corporate_wallet_service.py` | `apply_adjustment` gains a `ride_id` parameter, threaded to the RPC | Enable dedup for the master-wallet fallback debit |
| `backend/services/corporate_allowance_service.py` | `_apply()` gains a `ride_id` parameter, only included in the RPC payload when set; `apply_ride_debit`/`apply_ride_debit_reversal` gain a `ride_id` parameter | Enable dedup for the allowance debit/reversal, without breaking calls against a not-yet-migrated DB |
| `backend/services/payment_service.py` | `settle_corporate` passes `ride_id=ride_id` to all three settlement calls; logs `info` when a call reports `deduped=True` | Actually engage the new dedup path; give ops visibility into safely-absorbed retries |
| `backend/tests/test_corporate_rpc_ride_idempotency.py` | New file: service-layer param-threading tests, `settle_corporate`-level tests (including a simulated deduped retry), migration-SQL contract tests (dedup ordering, cap-guard presence, sign-contract preservation) | Cover the new logic; guard against future silent regression of the cap check the way 277 caused one |
| `backend/tests/test_corporate_allowance_cap_race.py` | Docstring update noting the 277 regression and pointing to the new real-SQL contract test | This file alone gave false assurance during the actual regression; make that limitation explicit and cross-referenced |
| `docs/runbooks/deploy-migration-297.md` | New runbook: mandatory migration-before-backend-deploy sequence | Prevent the deploy-ordering hazard from becoming a real outage |

## 7. Before / after

```sql
-- Before (migration 277, current-until-this-migration body) — no ride scoping,
-- no cap guard:
SELECT used INTO v_used
FROM corporate_member_allowances WHERE id = p_allowance_id FOR UPDATE;
...
v_used_new := v_used + v_used_delta;
-- (no cap check)
UPDATE corporate_wallets SET balance = v_master_new ...
```

```sql
-- After (migration 297) — cap read alongside used, guard restored, ride-scoped dedup added:
SELECT used, amount INTO v_used, v_cap
FROM corporate_member_allowances WHERE id = p_allowance_id FOR UPDATE;
-- [ride-scoped dedup check here, using the now-held lock]
...
v_used_new := v_used + v_used_delta;
IF p_type = 'ride_debit' AND v_cap IS NOT NULL AND v_used_new > v_cap THEN
    RAISE EXCEPTION 'allowance_cap_exceeded: used_new=% cap=%', v_used_new, v_cap;
END IF;
UPDATE corporate_wallets SET balance = v_master_new ...
```

```python
# Before (services/payment_service.py)
await corporate_allowance_service.apply_ride_debit(
    wallet_id=corp_wallet["id"], allowance_id=allowance["id"], member_id=membership["id"],
    amount=_f(allowance_debit), actor_user_id=..., notes=f"ride:{ride_id}:allowance", floor=0.0,
)
```

```python
# After
_allowance_debit_result = await corporate_allowance_service.apply_ride_debit(
    wallet_id=corp_wallet["id"], allowance_id=allowance["id"], member_id=membership["id"],
    amount=_f(allowance_debit), actor_user_id=..., notes=f"ride:{ride_id}:allowance",
    ride_id=ride_id, floor=0.0,
)
if isinstance(_allowance_debit_result, dict) and _allowance_debit_result.get("deduped"):
    logger.info("[PAYMENT] allowance debit for ride {} was already applied ...", ride_id)
```

## 8. Rollback plan

- **No migration, direct DB feature flag exists** for either half of this change — both are pure
  function-body/parameter changes, not feature-flagged product behavior. Rollback is a function
  revert, not a flag flip:
  - `DROP FUNCTION` on the new signatures + re-apply migration 214's `corporate_wallet_apply_delta`
    body and migration 277's `corporate_allowance_apply_delta` body verbatim (both reproduced in
    migration 297's own header comment) + their `EXECUTE` grants.
  - **Rolling back to 277's body also removes the restored cap guard** — see migration 297's
    "IMPORTANT #1" comment. If only the idempotency logic needs reverting, prefer a new migration
    that keeps 258/261's guard and only reverts the dedup/lock-ordering changes, over a full rollback.
  - **Roll back the backend code before or with the DB rollback** — see "IMPORTANT #2." Reverting the
    DB function first while ride-settlement code still sends `p_ride_id` reintroduces the exact
    deploy-ordering failure this migration's own forward path was built to avoid, in reverse.
- This does touch already-applied-to-live-data money paths (wallet balances, allowance `used`
  counters) going forward — a `git revert` of the *migration* alone is not sufficient without also
  reverting/coordinating the backend code per the above; no historical ledger rows are altered by
  either the deploy or the rollback.

## 9. Verification performed

- [x] Automated tests run: `pytest backend/tests/test_corporate_rpc_ride_idempotency.py backend/tests/test_corporate_allowance_cap_race.py backend/tests/test_allowance_cap_fallback.py backend/tests/test_allowance_rpc_sign_contract.py -q --no-cov` — **28 passed, 0 failed**. Broader sweep: `pytest backend/tests/ -k "corporate or scheduled or allowance or payment" -q --no-cov` — **1442 passed, 3 skipped (pre-existing, unrelated), 0 failed**.
- [ ] Manual repro steps followed in staging — not performed, no staging/Supabase access in this environment. The SQL itself has not been executed against a real Postgres instance — verified by static analysis (migration-SQL contract tests parsing the actual file text) and by a dedicated `spinr-migration-reviewer` agent review, not by running it.
- [x] Blast-radius grep performed: every caller of the affected service functions (§4).
- [x] Reviewed against relevant CLAUDE.md conventions: money arithmetic (Decimal-only, unchanged), Stripe idempotency (unaffected, separate dedup key), corporate billing priority (rider wallet → card → allowance → master fallback order unchanged), migration conventions (append-only, RLS n/a, indexes — existing partial index on `ride_id` sufficient, reviewed by `spinr-migration-reviewer`).
- [x] `spinr-migration-reviewer` agent review performed on the migration file — found and this log documents the fix for: (1) the deploy-ordering hazard, (2) the pre-existing cap-guard regression, (3) an incorrect rollback signature in an early draft (fixed before commit).
- [x] Dry-run / before-after scenario: §7 above; the concrete retry scenario (`test_deduped_retry_of_both_debits_still_settles_successfully`) simulates `settle_corporate` being called twice for the same ride with the RPC reporting `deduped=True` on the second call.

## What was NOT verified

- Not run against a real Supabase/Postgres instance — the SQL syntax and logic were verified by careful reading, by a dedicated migration-reviewer agent, and by contract tests that parse the migration file's actual text (not just an intended design), but none of that is a substitute for an actual `psql` execution or the integration test tier this repo's conventions describe as a separate, not-yet-wired-up tier for this kind of change.
- No load/concurrency test exercises the new `FOR UPDATE`-then-dedup ordering under real concurrent traffic — the TOCTOU-closing reasoning is architectural (matches `wallet_apply_delta`'s established pattern) and unit-tested for the ordering-in-source-text sense, not concurrency-tested.
- The deploy-ordering runbook (`docs/runbooks/deploy-migration-297.md`) describes the required sequence but its steps have not been executed against a real environment — no live deploy happened in this session.
