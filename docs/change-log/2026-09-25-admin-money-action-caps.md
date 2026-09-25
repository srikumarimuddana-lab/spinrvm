# Change Impact & Risk Log — admin money-action caps (N23 / ADMIN-OPS-001)

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-25 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | admin (money path: payments) |
| PR / commit link | branch `claude/fix-admin-money-action-caps` (local, not pushed) — commits `8d6e79e`, `00b3771`, `be63745` |
| Related issue or gap ID | ROADMAP N23, finding ADMIN-OPS-001; cap/threshold values are founder decision E-F7 |

## 1. Issue / gap identified

An admin could credit or debit any rider/driver wallet (`POST /api/admin/wallet/credit|debit`) and issue dispute refunds (`routes/disputes.py::admin_resolve_dispute`) with no cumulative limit and no alert. The only bound was the $10,000 per-call Pydantic limit, so one compromised or careless admin session could move an unbounded amount in minutes.

## 2. Root cause

No control existed. The corporate wallet `/adjust` endpoint already had a per-admin daily cap (`routes/corporate_wallet.py::_check_daily_adjust_cap`, `corporate_wallet_admin_adjust_daily_cap`), but it was never extended to the consumer wallet or dispute refund paths.

## 3. Fix / remediation

- **Migration 475** adds two nullable `NUMERIC(12,2)` columns to `public.settings`: `admin_money_daily_cap_per_admin` and `admin_money_alert_threshold`. NULL means disabled, and both ship NULL. They are exposed on `SettingsUpdateRequest` (`gt=0`, 2 dp, 12 digits), next to the corporate cap.
- **`services/admin_money_caps.enforce_admin_money_action_cap`** is called immediately before the money moves:
  - **Cap.** Sums the admin's own `audit_logs` rows for the current UTC day (`actor_id` = admin, `created_at >= 00:00 UTC`). It counts `wallet_credit`/`wallet_debit` rows (`details.amount`) and `dispute_resolved` rows (`details.refund_amount`, only when `details.resolution` is `approved`/`partial_refund`). If `abs(amount) + today > cap`, it returns **403** and nothing moves. An `admin_money_cap_blocked` audit row is written.
  - **Fail closed.** If the settings read or the sum query fails, it returns **503** and logs with `logger.error`, including `DatabaseError.details["original"]`.
  - **Alert.** If one action's `abs(amount) >= threshold`, the action is still allowed (subject to the cap). It raises a `logger.warning`, an `admin_money_threshold_alert` audit row (via `log_admin_action`, the codebase's admin-action audit path) and a Sentry `capture_message` tagged `domain=admin`, `surface=backend`, `spinr_alert=admin_money_threshold`. No shared Sentry helper takes a domain tag (`ledger_service.escalate` hard-codes `payments`), so this uses the same inline pattern. It logs IDs and amounts only.
  - If both settings are NULL, the service makes no audit query and does nothing.
- **Wiring:**
  - Wallet credit: runs after the idempotency replay short-circuit and wallet checks, before `wallet_apply_delta`.
  - Wallet debit: runs after the advisory balance check, before `wallet_apply_delta`.
  - Dispute: runs at the top of the refund branch, before both the Stripe `Refund.create` path and the `manual_required` path.
- **Scope decision: super-admins are NOT exempt.** Nothing in the existing code justifies an exemption. The corporate cap doesn't exempt them either, and a super-admin session is the most valuable one to compromise.
- **Status code:** 403 as specified. The corporate cap uses 429. The dashboard's `request()` only special-cases 401 (logout) and 429, so a 403 surfaces as a plain error with the backend `detail`.

**Alternative considered and rejected:** reuse the wallet ledger (`wallet_transactions.metadata.admin_id`) as the source of truth. Rejected because dispute refunds never touch `wallet_transactions`, so it couldn't sum across all three action types. It would also need a JSON-path filter on `metadata`, which the query layer doesn't support. `audit_logs` is already written by all three endpoints with a first-class, indexed `actor_id`, and it's the source the existing corporate cap already uses.

## 4. Risk & impact on existing functionality

**Blast radius: single-surface (backend admin money endpoints). NULL settings mean no behaviour change.**

- **New callers:** `routes/admin/wallet.py` (credit, debit) and `routes/disputes.py::admin_resolve_dispute`. No other code imports the service.
- **Callers of the touched endpoints:** admin-dashboard `lib/api/users-wallet.ts` (credit/debit) and `lib/api/analytics-payouts.ts::resolveDispute`. There are no backend or AI-tool callers. `dependencies/__init__.py:865` is a docstring example.
- **Important pre-existing finding (not fixed here):** `PUT /api/admin/disputes/{id}/resolve` is registered twice:
  - `routes/admin/support.py` (included at `routes/admin/__init__.py:309`), which only writes `resolution_status` and moves no money.
  - `routes/disputes.py` (included at `:339`), which does the Stripe refund.

  The first registration wins. `test_admin_support_routes.py::test_resolve_dispute` shows that path is served by `support.py`, and `test_disputes_admin_coverage.py`'s docstring notes the `disputes.py` handler is only exercised as a function. So the dashboard's "approve refund" currently issues **no Stripe refund**; it ignores `resolution`/`refund_amount`. The cap in `disputes.py` takes effect whenever that routing is fixed. This needs its own ticket.
- **`audit_logs`:**
  - New reads: one indexed query per capped action (`actor_id` index, `created_at` index).
  - New writes: `admin_money_cap_blocked` and `admin_money_threshold_alert` rows. These names are not in the counted set, so they never inflate the sum.
  - `support.py`'s `dispute_resolved` rows have no `resolution` key, so they count as 0.
  - Other readers of `audit_logs`, such as the admin audit views and retention purge, see the extra action names as ordinary rows.
- **Admin money paths NOT covered:** super-admin bulk `wallet_import`, corporate `/adjust` (has its own cap), admin ride waivers and pay-links, and auto-payouts. They are out of scope for N23.
- **Settings:** `SettingsUpdateRequest` gains two fields. `test_admin_settings_write_allowlist_drift.py`'s column snapshot was updated. Saving either field before migration 475 is applied would PGRST204, so apply the migration first.
- There is no interaction with the ride state machine, background loops, or the wallet RPC's own locking or idempotency.

## 5. User-experience effect

- **Internal admins only, and only once E-F7 values are set.** Over the cap, a credit, debit or refund fails with: "Daily admin money-action cap of $X reached: $Y already moved today (UTC) and this action is $Z. Nothing was moved. Ask another admin to process it, or wait until 00:00 UTC."
- If the check can't run, admins get a 503 retry message.
- Riders and drivers see no change; a blocked action simply never happens, and no push is sent.
- Changes take effect for admins who are already logged in within the 60s settings cache.
- There is no visual change, and no dashboard code changed.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/475_admin_money_action_caps.sql` | 2 nullable settings columns + comments + rollback header | Store cap/threshold; NULL = off |
| `backend/routes/admin/settings.py` | 2 `Optional[Decimal]` fields on `SettingsUpdateRequest` | Settable from admin settings without SQL |
| `backend/tests/test_admin_settings_write_allowlist_drift.py` | Snapshot gains the 2 columns | Drift guard maintenance rule |
| `backend/services/admin_money_caps.py` | New cap/alert service | Shared by wallet + dispute paths |
| `backend/tests/test_admin_money_caps.py` | 10 unit tests (mock_supabase_client) | Cap, sum scope, NULL, alert, 503 |
| `backend/routes/admin/wallet.py` | Cap call before `wallet_apply_delta` (credit + debit) | Enforce before money moves |
| `backend/routes/disputes.py` | Cap call at top of refund branch | Enforce before Stripe refund |
| `backend/tests/test_admin_money_caps_routes.py` | 8 endpoint tests | 403 with no wallet/Stripe call; 503 path |

## 7. Before / after

```python
# Before (routes/admin/wallet.py, credit)
    credit = _q(req.amount)
    txn = await db_supabase.wallet_apply_delta(...)
```

```python
# After
    credit = _q(req.amount)
    await enforce_admin_money_action_cap(
        admin, credit, action="wallet_credit", resource="user", resource_id=req.user_id
    )  # 403 over cap / 503 if unverifiable -- raises before any money moves
    txn = await db_supabase.wallet_apply_delta(...)
```

**Dry-run scenario** (cap $100, threshold $50, admin A):
1. Credit $40: allowed.
2. Debit $25: allowed.
3. Refund $30: allowed (total today $95).
4. Credit $10: **403**, `wallet_apply_delta` is never called and the balance is unchanged.

Admin B is unaffected by A's total. A single $60 credit by B is allowed but alerts.

## 8. Rollback plan

- **Operational, with no deploy.** The 60s settings cache applies:
  `UPDATE public.settings SET admin_money_daily_cap_per_admin = NULL, admin_money_alert_threshold = NULL WHERE id = 'app_settings';`
  The API can't clear the values because `exclude_none` drops them, so use SQL.
- **Schema** (only after reverting the code): `ALTER TABLE public.settings DROP COLUMN IF EXISTS admin_money_alert_threshold, DROP COLUMN IF EXISTS admin_money_daily_cap_per_admin;`
- A block moves no money, so there is no live data to remediate.

## 9. Verification performed

- [x] **New tests:** `test_admin_money_caps.py` (10) and `test_admin_money_caps_routes.py` (8), all passing. The 4 blocking or fail-closed route tests were confirmed to **fail** with the wiring reverted.
- [x] **Existing suites.** Wallet and dispute suites plus new tests: 268 passed across 17 files (`test_admin_wallet_*`, `test_admin_business_logic`, `test_admin_rbac`, `test_referral_payout_credit`, `test_push_target_app_declared`, `test_n10_admin_push_target_app`, `test_no_raw_exception_in_4xx_detail`, `test_dispute_refund_cents`, `test_routes_disputes_coverage`, `test_disputes_admin_coverage`, `test_p3_addresses_favorites_safety_disputes`, `test_admin_support_routes`, `test_loguru_call_conventions`). Admin-settings suites: 292 passed (19 `*settings*` files plus the flag/settings files).
- [x] Full `pytest -m "not slow"` (no coverage): 16352 passed, 6 failed, 411 errors. The 411 errors are all `tests/rls/`, which needs a real Postgres and must be run in isolation per CLAUDE.md. The 6 failures are all in `test_verify_otp_login_flow.py` and are order-dependent: the file passes alone (21/21) and also passes when run after every new or changed test file here. The failing code is auth, not code touched here. It was not confirmed whether these 6 also fail on `origin/main` in full-suite order.
- [x] `ruff check` and `ruff format --check` are clean, and the pre-commit hook ran on every commit (no `--no-verify`).
- [x] **Blast-radius grep:**
  - The two endpoints' callers: backend, AI tools and admin-dashboard.
  - Every `Refund.create`, `wallet_apply_delta` and `admin_credit`/`admin_debit` writer.
  - `audit_logs` indexes.
  - Every `/disputes/{id}/resolve` registration.
  - The dashboard's handling of 403.
- [x] **Conventions:**
  - Decimal only (`_d`/`_round`; there's no float output, so no `_f`).
  - Dual import.
  - Fail closed with `logger.error`.
  - PIPEDA: IDs and amounts only.
- [x] **Flagged:** both settings are NULL and dark until E-F7.

## What was NOT verified

- Nothing was run against live Supabase. The migration wasn't applied anywhere, and the `audit_logs` `actor_id`/`created_at` filter was checked only through the mocked PostgREST chain.
- Sentry delivery was mocked, not observed.
- No staging run.
- The dispute cap is not reachable over HTTP today because of the route shadowing above, so it was tested only as a direct function call.
- **Accepted gaps in the interim control:**
  - It's check-then-act, not a lock, so two concurrent actions by one admin can both pass.
  - Wallet audit rows are written after the money moves, and `log_admin_action` swallows failures, so a failed audit write undercounts.
  - A deduped wallet replay that reaches the RPC writes a second audit row, which overcounts (the safe direction).
  - The threshold alert row is written before the action and remains even if the action then fails, for example at the debit floor.
- CLAUDE.md gate 10's post-implementation `spinr-*` reviewer was not run, because no Agent tool was available in this session. `spinr-money-auditor` and `spinr-security-auditor` should run before merge.
