# Change Impact & Risk Log — real debit mechanism for wallet/corporate late tips

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-17 |
| Author | Claude Code (session) |
| Surface(s) | backend |
| Domain (Sentry tag) | payments, corporate |
| PR / commit link | (filled in on PR creation) |
| Related issue or gap ID | Follow-up to `docs/change-log/2026-08-17-add-tip-late-charge-guard.md` (Finding 1's wallet/corporate branch, which absorbed unconditionally with no debit attempt) |

## 1. Issue / gap identified

`add_tip`'s wallet/company_allowance branch (shipped earlier the same day) absorbed 100% of a
late tip's cost unconditionally — it never even attempted to collect the money, on the reasoning
that no debit-after-the-fact mechanism existed. That was true at the time, but it meant Spinr
absorbed every dollar of every late tip on those two payment methods, even when the rider's
wallet had funds or the company's allowance had budget available.

## 2. Root cause

`wallet_pay_for_ride` (personal wallet) and `corporate_allowance_apply_delta` /
`corporate_wallet_apply_delta` (corporate) are the RPCs the ORIGINAL ride settlement uses, and
both dedupe on a key that includes the ride's own id — a second call for the same ride with the
same settlement `type` value would silently no-op (return `deduped=true`, apply zero additional
money movement) rather than debit again. There was no *disjoint* type value a late-tip debit
could use to avoid that collision, so a real debit was structurally impossible without a schema
change — hence the earlier fix's decision to absorb unconditionally rather than build a
half-correct mechanism that would risk double-counting or silent no-ops.

## 3. Fix / remediation

**Migration 319** (`backend/migrations/319_late_tip_debit_types.sql`) adds two new,
settlement-disjoint ledger type values:
- `wallet_transactions.type`: `'late_tip_debit'`
- `corporate_wallet_transactions.type`: `'late_tip_debit'` (member allowance side) and
  `'late_tip_adjustment'` (master-wallet fallback side)

and extends `corporate_allowance_apply_delta` (CREATE OR REPLACE, body copied verbatim from
migration 297 plus the new type) so `'late_tip_debit'` gets identical master/used delta math to
`'ride_debit'` and is covered by the same per-member allowance-cap guard.

**Bonus fix bundled into the same migration** (found while writing it, unrelated to late tips):
`corporate_wallet_transactions.type`'s table-level CHECK constraint has never included
`'ride_debit_reversal'`, even though `corporate_allowance_apply_delta` has accepted and inserted
that exact value since migration 248 (`settle_corporate`'s own compensation path, when the
master-wallet fallback fails after the allowance debit succeeded). Every real compensation
reversal has therefore been hitting a Postgres 23514 check-violation since migration 248 shipped.
It's not silent data corruption — `settle_corporate`'s `try/except` around the reversal call
catches it and logs "Allowance compensation failed ... manual ledger fix required" rather than
crashing — but the automatic reversal the code believes it's performing has never actually
happened; every occurrence has required a manual ledger fix instead. Fixed by adding
`'ride_debit_reversal'` to the table CHECK constraint in the same migration (see the migration
file's own header for the full writeup and the precedent — migration 297 itself bundled an
unrelated regression fix into the same DROP+CREATE for the same reason: it was already touching
the exact function/constraint).

**New service functions** (both best-effort, never raise):
- `services/payment_service.py::charge_late_wallet_tip` — debits the rider's personal wallet via
  `wallet_apply_delta(type_="late_tip_debit", clamp_to_floor=True, floor=0)`. Insufficient balance
  means a partial debit, not a failure.
- `services/payment_service.py::charge_late_corporate_tip` — mirrors `settle_corporate`'s
  allowance-then-master-wallet saga (new `corporate_allowance_service.apply_late_tip_debit` /
  `corporate_wallet_service.apply_late_tip_master_debit`), scoped to just the tip amount.
  **Deliberately does NOT reverse a partial success** if the master-wallet fallback then fails
  (unlike `settle_corporate`, which reverses the allowance debit in that case) — see §4.

`add_tip` (`routes/rides/payments.py`) now calls these before falling back to absorbing: the
rider/driver-facing outcome is unchanged either way (driver always credited the full tip, rider
never sees an error), but Spinr now actually collects real money whenever the wallet/allowance
has it, only absorbing the genuine shortfall instead of the whole amount unconditionally.

## 4. Risk & impact on existing functionality

- **Blast radius: single-surface (backend), touching a previously regression-prone shared
  function.** `corporate_allowance_apply_delta` is called by `apply_grant`, `apply_reset`,
  `apply_rollback`, `apply_ride_debit`, `apply_ride_debit_reversal` (all unchanged — new type is
  additive, existing branches untouched) and now the new `apply_late_tip_debit`. Grepped every
  caller; none pass a type this migration doesn't account for.
- **Dedup-collision is the central risk this whole design exists to avoid** — verified explicitly:
  `wallet_apply_delta` dedupes on `(wallet_id, reference_id, type)` (migration 249's function
  body); `corporate_wallet_apply_delta` / `corporate_allowance_apply_delta` dedupe on
  `(wallet_id, ride_id, type, scope/member_id)` (migration 297's function body, read directly from
  the migration file, not inferred from a docstring). The original settlement's rows for a given
  ride use `type='ride_payment'` (wallet, `settle_wallet`) / `type IN ('ride_debit','adjustment')`
  (corporate, `settle_corporate`) — none of which the new late-tip code ever reuses. Confirmed by
  reading `settle_wallet`/`settle_corporate` source directly, not assumed.
- **Deliberate deviation from `settle_corporate`'s all-or-nothing compensation**: if the allowance
  debit succeeds but the master-wallet fallback then fails, `charge_late_corporate_tip` KEEPS the
  allowance portion already collected rather than reversing it (no `late_tip_debit_reversal` type
  was added — see the migration's "NOT DONE HERE" section). This is intentional: the original
  settlement's reversal exists because a ride charge must be all-or-nothing (the rider can be asked
  to retry with a different card/method); a late tip has no such retry path once "done" from the
  rider's perspective, so partial collection + absorbing only the genuine remainder is the correct
  outcome, not a shortcut. Documented in the function's own docstring so this reads as a decision,
  not an oversight, on future review.
- **Allowance-cap enforcement is preserved for the new type** — the migration's cap guard was
  extended to `p_type IN ('ride_debit', 'late_tip_debit')`, not left scoped to `'ride_debit'`
  only (which would have silently let late tips bypass the per-employee spending cap).
- **`ride_payment_sources`, admin/reporting reads of `corporate_wallet_transactions`,
  reconciliation jobs**: grepped for any reader that assumes every `corporate_wallet_transactions`
  row for a given `ride_id` has `type IN ('ride_debit','adjustment')` only — none found that would
  misbehave on a new type value; readers either filter by `ride_id` alone (a late-tip row is
  correctly additional context on the same ride) or don't touch this table.
- **Migration ordering**: same "apply before deploying the paired backend code" requirement
  migration 297 documented for the same function — `charge_late_corporate_tip`'s
  `apply_late_tip_debit` call will fail with "invalid allowance type" against a database that
  hasn't had migration 319 applied yet. Falls into the existing best-effort `try/except` (routes to
  master, then absorbs) rather than crashing, so a deploy-before-migrate ordering mistake degrades
  to "absorb everything" (today's behavior) rather than erroring — not silent, still logged.

## 5. User-experience effect

**None, for riders and drivers.** The entire point of this change is that nothing changes on the
outward-facing side: the driver is credited the full tip either way, the rider never sees any tip
error on either payment method. The only difference is internal — Spinr collects real money more
often instead of absorbing every late tip unconditionally. Not visible mid-ride (only reachable
after a ride is already completed and paid, same reachability as the original Finding 1 fix).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/319_late_tip_debit_types.sql` | New — adds `late_tip_debit`/`late_tip_adjustment` types, redefines `corporate_allowance_apply_delta`, fixes the `ride_debit_reversal` CHECK-constraint gap | Enables a real, dedup-safe debit path; closes a found-along-the-way compensation bug |
| `backend/services/payment_service.py` | Added `charge_late_wallet_tip`, `charge_late_corporate_tip` | Real debit attempts before absorbing |
| `backend/services/corporate_allowance_service.py` | Added `apply_late_tip_debit` | Allowance-side debit with the new dedup-safe type |
| `backend/services/corporate_wallet_service.py` | Added `apply_late_tip_master_debit` | Master-wallet fallback debit with the new dedup-safe type |
| `backend/routes/rides/_deps.py` | Re-export the two new `payment_service` functions | Same pattern as `charge_late_tip` |
| `backend/routes/rides/payments.py` | `add_tip`'s wallet/corporate branch now attempts real collection before absorbing the remainder | Closes this follow-up |
| `backend/tests/test_charge_late_wallet_tip.py` | New — 5 unit tests | Direct coverage of the wallet helper |
| `backend/tests/test_charge_late_corporate_tip.py` | New — 10 unit tests | Direct coverage of the corporate saga, including cap-exceeded/failure branches |
| `backend/tests/test_rides_payments_coverage.py` | Updated wallet/corporate `add_tip` tests for success/partial outcomes | Endpoint-level coverage of the new wiring |

## 7. Before / after

```python
# Before (routes/rides/payments.py::add_tip, abridged)
if payment_method != "card":
    _metric_inc("spinr_payment_late_tip_total", {"outcome": "absorbed"})
    logger.info(f"[TIP] late tip on already-paid {payment_method} ride {ride_id} — "
                "no after-the-fact debit path; absorbing per product decision")
    # tip_amount/driver_earnings persisted regardless — 100% absorbed, always
```

```python
# After (abridged)
if payment_method == "wallet":
    collected = await charge_late_wallet_tip(ride, ride_id, current_user["id"], tip_amount)
elif payment_method == "company_allowance":
    collected = await charge_late_corporate_tip(ride, ride_id, tip_amount)
else:
    collected = Decimal("0")  # unrecognized method, fail safe

outcome = "success" if collected >= tip_amount else "partial" if collected > 0 else "absorbed"
_metric_inc("spinr_payment_late_tip_total", {"outcome": outcome})
if outcome != "success":
    logger.info(f"... collected ${collected} of ${tip_amount} for real, absorbing the rest ...")
# tip_amount/driver_earnings persisted regardless of collected — driver always
# gets the full credit; only Spinr's collected-vs-absorbed split differs now
```

## 8. Rollback plan

**`revert-plus-data-cleanup`** — a step up from the pure `git-revert-safe` of the earlier
same-day fix, because this one includes a migration:
- **Code revert**: `git revert` is safe on its own — reverting the Python code just stops
  attempting real debits and goes back to absorbing unconditionally (today's behavior). No data
  correction needed for that half.
- **Migration**: do NOT roll back the migration unless zero `late_tip_debit`/`late_tip_adjustment`
  rows have been written yet (per the migration's own rollback comment). If any have been
  written, leave the migration applied (the new types are purely additive — nothing reads them
  differently from the pre-existing types unless explicitly coded to) and only revert the Python
  code that writes them.
- The `ride_debit_reversal` CHECK-constraint fix should NOT be rolled back once applied, even if
  everything else is reverted — it's a standalone correctness fix for `settle_corporate`'s
  existing compensation path, unrelated to late tips.

## 9. Verification performed

- [x] Automated tests: `pytest tests/test_charge_late_tip.py tests/test_charge_late_wallet_tip.py tests/test_charge_late_corporate_tip.py tests/test_rides_payments_coverage.py -q --no-cov` → **45 passed**. Broader sweep `pytest tests/ -k "tip or payment or settle_card or settle_wallet or settle_corporate or corporate_allowance or corporate_wallet or webhooks or ledger or wallet" -q --no-cov` → **1170 passed, 1 pre-existing unrelated skip, 0 failed**. Full `-m unit` sweep → **2777 passed, 1 skipped, 1 failed** — the 1 failure (`test_scheduled_rides_coverage.py::test_lock_not_acquired_still_proceeds_to_fetch`) reproduces identically on unmodified `main` (verified via `git stash`), confirmed pre-existing and unrelated.
- [x] `ruff check` on all changed/added Python files → clean.
- [x] Migration validated with the repo's own `scripts/check_migration.py` → all hard checks pass (naming, sequence, RLS n/a, rollback-comment, dangerous-ops).
- [x] A convention violation was caught and fixed during this work: 4 `logger.error(..., exc_info=True)` calls (loguru has no `exc_info` parameter — it's silently swallowed as a format kwarg with no traceback captured) were rewritten to `logger.opt(exception=...).error(...)`, caught by this repo's own `tests/test_loguru_call_conventions.py`.
- [x] Dedup-collision risk verified by reading the actual RPC function bodies from the migration files (not inferred from docstrings) for `wallet_apply_delta` (249), `corporate_wallet_apply_delta`/`corporate_allowance_apply_delta` (297), confirming the exact dedup key columns and that the new types are disjoint from every settlement-time type.
- [x] `ride_debit_reversal` CHECK-constraint gap verified by grepping every migration file that references `corporate_wallet_transactions` for any CHECK/ALTER on the `type` column — only migrations 27 (original definition, 7 values, no `ride_debit_reversal`) and 214 (unrelated `actor_user_id` type widening) touch this table; no migration since has widened `type`.
- [x] Independent review requested from `spinr-migration-reviewer` and `spinr-corporate-billing-reviewer` subagents before opening the PR (findings, if any, addressed in a follow-up commit on the same PR — see the PR thread for the outcome).

## What was NOT verified

- No manual/staging repro against a real Supabase instance with migration 319 actually applied —
  no live environment access in this session. All tests mock the RPC layer.
- Did not verify against real production data how often the wallet/corporate late-tip path
  actually fires, or how much money this recovers vs. the prior unconditional-absorb behavior —
  no production DB access in this session.
- The personal-wallet side (`charge_late_wallet_tip`) has no allowance-cap-style contention
  concern (a single wallet, no per-member ceiling), so its test coverage is proportionately
  lighter than the corporate side's — this matches the actual complexity difference, not an
  oversight, but is worth naming explicitly.
