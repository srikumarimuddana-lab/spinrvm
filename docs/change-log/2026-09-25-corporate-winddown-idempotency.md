# Change Impact & Risk Log: corporate wind-down idempotency and close CAS

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-25 |
| Author | Claude Code (session_01PPGd1wK6WRzbtxcGj3qNkX) |
| Surface(s) | backend |
| Domain (Sentry tag) | corporate |
| PR / commit link | branch `claude/fix-corporate-winddown-idempotency`: `5acded5`, `88f2c33`, `6175ed1` (not pushed) |
| Related issue or gap ID | ROADMAP N20; clean-sheet findings CORP-001 and CORP-003 (`docs/audit/clean-sheet/02-findings/corporate.md`); risk register RR-50 and RR-52; `session-2-pricing-payments-cra.md` §3 "Now" item 5 |

## 1. Issue / gap identified

- **CORP-001.** Two things combined. First, the company close flow debits the master wallet through `apply_adjustment` → `corporate_wallet_apply_delta` without an idempotency key. Second, the only replay guard is a plain-read `if status == 'closed': 409` followed by an unconditional status UPDATE. So a double-submitted or retried "Close account" could run the wind-down twice and debit the wallet twice against one set of Stripe refunds, on an account that is terminal and can never be reopened to fix it.
- **CORP-003.** `update_corporate_wallet_config` wrote any `patch` dict straight to `corporate_wallets`, including `balance`. Only the callers' own discipline stopped it from bypassing the wallet RPC.

Re-verified against `origin/main` @ `9e27915` before fixing. Both findings were still present exactly as described: `corporate_repo.py` `update_corporate_account_status` had no status filter, `corporate_wallet_winddown_service.py` called `apply_adjustment` with no `client_idempotency_key`, and `update_corporate_wallet_config` had no allow-list. Nothing had already been fixed.

## 2. Root cause

- The close endpoint's replay safety sat at the *status* layer (409 on re-close), not at the *money* layer. The status check was also not atomic: the read and the write were two statements with no compare-and-set. Stripe's own per-top-up idempotency key (`corp-close-refund-{wallet}-{topup}`) protected Stripe's ledger, but not `corporate_wallets.balance` or `corporate_wallet_transactions`.
- `update_corporate_wallet_config` was written as a generic patch helper with no column contract.

## 3. Fix / remediation

1. **Idempotency key on the wind-down debit.** `refund_wallet_balance_on_close` passes `client_idempotency_key = "corp-close-{wallet_id}-" + sha256("|".join(sorted(stripe_refund_ids)))`.
   - The RPC parameter and its dedup short-circuit already exist (migration 376, `p_client_idempotency_key`, short-circuit #3, checked after the wallet row is locked `FOR UPDATE`). There is no schema change.
   - The key comes from the sorted refund ids, not the wallet id alone. A replay that gets the same refunds back from Stripe dedupes. A run that refunded a different set is a different debit and is applied.
   - The refund-id set is hashed so the key has a fixed length, because it sits in a global unique btree index.
   - It is scoped by wallet id because that index is global.
   - A deduped debit sets the new result field `ledger_debit_deduped: true` and logs a warning.
2. **Compare-and-set close transition.** `update_corporate_account_status` gets an optional keyword-only `expected_status`. When it is given, the UPDATE also filters on that status (`.eq("status", expected_status)`).
   - `change_company_status` passes the status it read. If 0 rows match, it re-reads the row. If the row is gone, it returns **404** (as before). If the status moved underneath the request, it returns **409** with a clear message and logs a warning.
   - Both outcomes happen before any side effect: auto-topup freeze, ride cancellation, wind-down, subscription cancel, audit row.
   - This applies to every transition through this endpoint, not only close. A suspend racing a close could otherwise land second and leave a refunded, terminal company in the reversible `suspended` state.
3. **Wallet-config column allow-list.** `update_corporate_wallet_config` raises `ValueError` for any column outside `{auto_topup_enabled, auto_topup_threshold, auto_topup_amount, auto_topup_daily_cap}`.
4. `corporate_close_refunds_wallet_balance` is unchanged: same default (`false`), same read site, same behaviour.

**Alternative considered (gate 10).** Write one ledger row per Stripe refund, each keyed on its own refund id (`corp-close-refund-{refund.id}`).
- That alternative is strictly more robust to *overlapping* refund sets.
- **Chosen instead:** a single sorted-refund-set key plus CAS. It keeps today's ledger shape (one wind-down row per close, which finance and the existing tests and notes expect), needs no new floor semantics per row, and matches the brief.
- The overlapping-set case is only reachable if the wind-down runs twice for one close. The CAS now prevents that, and the terminal-status 409 already blocked it sequentially.

## 4. Risk & impact on existing functionality

**Blast radius (grepped on `origin/main`):**

- `update_corporate_account_status` is called from `routes/corporate_accounts.py::change_company_status`, which now passes `expected_status`, and from `routes/corporate_company_kyb.py:190` (KYB resubmit flip to `pending_verification`). The KYB caller does **not** pass the new argument, so its behaviour is unchanged (covered by `test_status_update_without_expected_status_is_unconditional`). The function is re-exported via `db_supabase.py`, with no other callers.
- `update_corporate_wallet_config` is called from `routes/corporate_wallet.py:353` (patch from `WalletConfigPatch`, whose `extra="forbid"` fields are exactly the four allowed columns) and `routes/corporate_accounts.py` (the `{"auto_topup_enabled": False}` literal). Both fall inside the allow-list. `test_wallet_config_allow_list_matches_route_schema` fails if `WalletConfigPatch` ever gains a field the repo does not allow. `corporate_repo.py:493` writes `low_balance_notified_at` through its own dedicated function, not this helper, so it is unaffected.
- `refund_wallet_balance_on_close` has one caller: `change_company_status`.
- `apply_adjustment` has other callers: `services/payment_service.py:1424` (ride-scoped, `ride_id` dedup), `services/cancellation_service.py:301` (ride-scoped), and `routes/corporate_wallet.py:285` (admin manual adjust, already passes its own `client_idempotency_key`). None of them changed; only the wind-down call site gained a key.
- `corporate_wallet_apply_delta` is called only from `services/corporate_wallet_service.py::_apply`. Its signature is unchanged.
- The admin dashboard is the one caller of `POST /api/admin/corporate-accounts/{id}/status` (`admin-dashboard/src/lib/api/corporate.ts:77`), and it already handles a 409 on this endpoint (closed account).

**What could regress:**

- In a genuinely concurrent pair of status requests, the loser now gets 409 instead of both "succeeding" as last-writer-wins. That is intended.
- A caller of `update_corporate_wallet_config` that relied on writing a non-config column would now raise. None exists today.
- The unique index `corp_wtxn_client_idempotency_key_unique` is global. The key is prefixed `corp-close-{wallet_id}-`, so it cannot collide with the manual-adjust keys (which are client-supplied).

**Background loops.** `corporate_autotopup_loop`, `corporate_low_balance_loop` and `allowance_reset_loop` read company status fresh each tick. They do not write it and do not call these functions, so they are unaffected.

## 5. User-experience effect

- **Internal admin only.** Two admins, or one double-click, submitting conflicting status changes at the same instant: one gets the new 409, "Corporate account status changed while this request was in flight (now '…'). No changes were made — refresh and try again." Before, both requests appeared to succeed and the side effects ran twice. Nobody sees a difference outside a true race.
- Riders, drivers and corporate portal users see no change.
- There is no copy change on any customer surface.
- Visual regression: this is a backend-only change. None of the six seeded admin-dashboard pages changes.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/repositories/corporate_repo.py` | `update_corporate_account_status(..., *, expected_status=None)` CAS filter; `_WALLET_CONFIG_PATCHABLE_COLUMNS` allow-list enforced in `update_corporate_wallet_config` | CORP-001 (atomic close guard), CORP-003 |
| `backend/routes/corporate_accounts.py` | Passes `expected_status`. On 0 rows, re-reads and returns 404 (gone) or 409 (moved). Also one `# noqa: F841` on a pre-existing unused `subscription_cancel_result` assignment | CORP-001. The noqa is only there so the pre-commit ruff gate passes: the F841 exists on `origin/main` too, and behaviour is unchanged |
| `backend/services/corporate_wallet_winddown_service.py` | `_winddown_idempotency_key()`; passes `client_idempotency_key` to `apply_adjustment`; new `ledger_debit_deduped` result field and warning | CORP-001 |
| `backend/tests/test_corporate_repo_guards.py` (new) | CAS filter, CAS losing path, allow-list reject/accept, schema-drift guard | Verification |
| `backend/tests/test_corporate_close_cas.py` (new) | Route CAS: conditional update, 409 losing path with no side effects, concurrent double close, suspend-vs-close race, sequential repeat | Verification |
| `backend/tests/test_corporate_winddown_idempotency.py` (new) | Dry run via the real `apply_adjustment`/`_apply` → `mock_supabase_client.rpc` against an in-memory model of migration 376 and of Stripe idempotency | Gate 4 dry run |
| `backend/tests/test_corporate_accounts_lifecycle.py` | "row disappears" test: the re-read also returns None | The new re-read on 0 rows |
| `backend/tests/test_corporate_wallet_winddown_service.py` | `apply_adjustment` mocks return `{"deduped": False}` like the real RPC row | A bare `AsyncMock()` child `.get()` returns an un-awaited coroutine |

## 7. Before / after

**Concrete scenario (gate 4)**

Company `c1`, wallet `w1`, balance **$250.00**, two $50 Stripe top-ups (`pi_1`, `pi_2`). The admin double-clicks "Close account" with `corporate_close_refunds_wallet_balance = true`.

| | Before | After |
|---|---|---|
| Status reads | Both requests read `active`; both pass the 409 check | Both read `active` |
| Status UPDATE | Both unconditional; both "succeed" | CAS `WHERE status='active'`: one wins, the other matches 0 rows → **409**, no side effects |
| Stripe refunds | 2 per request; Stripe's per-top-up key returns the same 2 refunds | 2, once |
| Ledger debit | −$100 twice (no key) → balance **$50.00** | −$100 once → balance **$150.00** |
| Net | $200 debited for $100 refunded, on a terminal account | Ledger matches Stripe |

Even with the CAS bypassed (for example, a future second entry point), the replay's debit carries the same key and the RPC returns the original row (`deduped=true`). The balance stays at $150.00. This is covered by `test_two_concurrent_winddowns_debit_the_ledger_exactly_once` and `test_repeated_winddown_for_same_refunds_is_deduplicated`.

```python
# Before: routes/corporate_accounts.py
row = await update_corporate_account_status(company_id=normalized_id, status=transition.status.value)
if not row:
    raise HTTPException(status_code=404, detail="Corporate account disappeared mid-transition")
```

```python
# After
expected_status = current.get("status")
row = await update_corporate_account_status(
    company_id=normalized_id, status=transition.status.value, expected_status=expected_status,
)
if not row:
    latest = await get_corporate_account_by_id(validated_id=normalized_id)
    if not latest:
        raise HTTPException(status_code=404, detail="Corporate account disappeared mid-transition")
    logger.warning("Corporate status transition lost a concurrent race: ...")
    raise HTTPException(status_code=409, detail="Corporate account status changed while this request was in flight ...")
```

```python
# Before: services/corporate_wallet_winddown_service.py
await apply_adjustment(wallet_id=wallet_id, amount=-refunded_total, notes=..., actor_user_id=..., floor=Decimal("0"))
```

```python
# After
ledger_row = await apply_adjustment(
    wallet_id=wallet_id, amount=-refunded_total, notes=..., actor_user_id=..., floor=Decimal("0"),
    client_idempotency_key=_winddown_idempotency_key(wallet_id, stripe_refund_ids),
)
if ledger_row.get("deduped") is True:
    result["ledger_debit_deduped"] = True
    logger.warning("... already recorded by an earlier wind-down run; no second debit applied")
```

```python
# Before: repositories/corporate_repo.py
res = supabase.table("corporate_wallets").update(patch).eq("id", wallet_id).execute()
```

```python
# After
disallowed = set(patch) - _WALLET_CONFIG_PATCHABLE_COLUMNS
if disallowed:
    raise ValueError(...)
res = supabase.table("corporate_wallets").update(patch).eq("id", wallet_id).execute()
```

## 8. Rollback plan

- **No live data has moved through this path.** `corporate_close_refunds_wallet_balance` is default `false`, per ROADMAP N20 and `session-2` §3, and this change does not alter that. So there is nothing on Stripe or the ledger to unwind.
- **If only the wind-down key misbehaves:** set `corporate_close_refunds_wallet_balance = false` in `app_settings`. That takes effect without a redeploy (it is its existing kill-switch) and stops every wind-down debit. Closes still succeed, and the balance is reported as unrefunded for finance, exactly as it is today.
- **If the CAS or the allow-list misbehaves:** there is no flag, so a code revert of the relevant commit (`88f2c33` and/or `5acded5`) plus a redeploy is the path. This is acceptable because neither change writes new data. The CAS only *refuses* a write, and the allow-list only *refuses* a patch. Reverting leaves no orphaned state.
- Any `corporate_wallet_transactions.client_idempotency_key` values written by a wind-down before a revert are harmless. The column is nullable and only read by the RPC's dedup lookup.
- **Why no new flag.** The domain convention (`domain-corporate.md`) prefers a default-`true` kill switch for behaviour fixes. It was considered and not added:
  - The CAS only changes an outcome in a true concurrent race, where the old outcome was the bug.
  - The key only changes an outcome on a replay, where the old outcome was a double debit.
  - The allow-list rejects nothing any current caller sends.
  - The money-moving part is already behind `corporate_close_refunds_wallet_balance`.
  - If a reviewer wants an explicit kill switch for the CAS anyway, it is a one-line `settings.get(...)` around `expected_status=`.

## 9. Verification performed

- [x] **New tests.** 5 in `test_corporate_repo_guards.py` (8 cases with parametrisation), 5 in `test_corporate_close_cas.py`, 6 in `test_corporate_winddown_idempotency.py`. All pass.
- [x] **Mutation checks.**
  - With `expected_status=None` forced in the route, 3 of the 5 CAS tests fail: conditional update, concurrent double close, suspend-vs-close.
  - With `client_idempotency_key=None` forced in the service, 3 of the 6 idempotency tests fail: key passed, concurrent wind-down, repeated wind-down.
  - In both cases the file was restored afterwards.
- [x] **Existing corporate suite.** `pytest tests/ --ignore=tests/rls -k corporate` gave **1078 passed, 3 skipped, 0 failed**. The `tests/rls` tier needs a real Postgres (`TEST_DATABASE_URL`) and errors in this environment by design, so it was not run.
- [x] **Coverage on touched files** (same run):

  | File | Coverage |
  |---|---|
  | `routes/corporate_accounts.py` | 96% |
  | `services/corporate_wallet_winddown_service.py` | 92% |
  | `repositories/corporate_repo.py` | 90% |
  | `routes/corporate_wallet.py` | 88% |

  All are at or above the 80% corporate floor.
- [x] **Lint.** `ruff check` and `ruff format --check` are clean on every touched file (backend `ruff.toml`), and the pre-commit hook passed on all commits.
- [x] **Blast-radius grep.** Searched `update_corporate_account_status`, `update_corporate_wallet_config`, `refund_wallet_balance_on_close`, `apply_adjustment(`, `corporate_wallet_apply_delta`, direct `table("corporate_wallets").update`, and the admin-dashboard status caller.
- [x] **Migration SQL.** Read migration 376 to confirm `p_client_idempotency_key` exists, that its dedup runs after `FOR UPDATE`, and that the unique index is global.
- [x] **Self-review.** Checked against `.claude/agents/spinr-corporate-billing-reviewer.md` and `.claude/agents/spinr-money-auditor.md`, read manually. The Agent tool was not available in this session, so the reviewers were not run as sub-agents.
- [ ] Production build: not applicable (backend only).
- [ ] Staging: not run.

## 10. What was NOT verified

- **Real Postgres.** Nothing ran against real Postgres or Supabase. The RPC's dedup-under-lock behaviour is modelled in Python from migration 376's SQL. It was not exercised through `test_money_rpc_races.py`'s real-Postgres harness, and whether migration 376 is applied in production was not checked.
- **Real Stripe.** Nothing ran against real Stripe. Stripe's "same idempotency key → same refund object" behaviour is modelled, and Stripe's 24-hour key expiry is not.
- **PostgREST CAS filter.** That a PostgREST `UPDATE … eq(id) eq(status)` returns an empty `data` list when 0 rows match was assumed from how the ride-acceptance guard and `corporate_suspension_service` already rely on it. It was not re-tested live.
- **Residual risk 1 (pre-existing, not fixed, out of scope).** If the wind-down loop is ever re-entered *after a partial Stripe failure*, it walks the same top-ups again. Stripe returns the earlier refund for any top-up whose refund amount is unchanged, and the loop counts it again. The refund set then differs from the first run's, so the new key (correctly) does not dedupe, and that top-up's amount would be debited twice. This is unreachable today: a closed company 409s on any second close, and the CAS stops the concurrent case. Making it safe by construction needs per-refund ledger rows (the alternative in §3) or skipping top-ups already refunded.
- **Residual risk 2 (pre-existing, not fixed, out of scope).** `POST /{company_id}/kyb-review` (`record_kyb_decision`) flips status to `active`/`suspended` with no guard on the current status. As far as this session could see, an admin approving KYB on a **closed** company would reopen it. That breaks the terminal-close invariant the wind-down relies on. It should become its own finding.
- **Pre-existing lint finding.** The F841 on `subscription_cancel_result` in `change_company_status` means that variable is computed and never used; it was probably meant for the audit-log details. It was noted and `noqa`'d, not fixed.
