# Change Impact & Risk Log — admin money-action caps, dispute refund routing and flag (N23 / ADMIN-OPS-001)

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-25 |
| Author | Claude Code |
| Surface(s) | backend, admin-dashboard (disputes page notice only) |
| Domain (Sentry tag) | admin (money path: payments) |
| PR / commit link | branch `claude/fix-admin-money-action-caps` (local, not pushed). Round 1: `8d6e79e`, `00b3771`, `be63745`. Round 2 (reviewer fixes + owner decisions A/B): `415cecf`, `effeb82`, `b12e6d6`, `0c20940`, `29f1725`, `a35e65e`, `4bd8825`, `3894ca7`. Round 3 (prod schema + refund truth + dashboard notice): `e76679b`, `b518786`, `94a1091`. Round 4 (money-auditor should-fixes): `f39fe9d`, `a192777` |
| Related issue or gap ID | ROADMAP N23, finding ADMIN-OPS-001; cap/threshold values are founder decision E-F7; owner decisions (A) real dispute refunds behind a flag that ships off, (B) super_admin-only cap settings |

## 1. Issue / gap identified

- Admins could credit or debit any rider/driver wallet, and issue dispute refunds, with no cumulative limit and no alert.
- Separately, the dashboard's dispute "approve refund" never refunded anyone. A duplicate `PUT /api/admin/disputes/{id}/resolve` in `routes/admin/support.py` shadowed the real handler in `routes/disputes.py`.

## 1b. Missing `disputes` columns (found round 2, fixed round 3)

- **Finding.** Production `public.disputes` has only the migration 10 and 126 columns (read-only `information_schema` check, 2026-09-25). `routes/disputes.py` writes four columns that don't exist:
  - `create_dispute`: `requested_amount`, `original_fare`;
  - `admin_resolve_dispute`: `resolution`, `refund_result`.
- **Impact.**
  - PostgREST rejects unknown columns (PGRST204), so **both handlers would have returned 500 in production**. Rider- and driver-filed disputes could never have been created, which fits the 0 disputes to date.
  - Once the routing fix made the resolve handler live, every resolve would also have returned 500.
  - With the refund flag on, that 500 would come **after** the Stripe refund.
- **Fix.** Migration `477_disputes_resolution_columns.sql` (additive, all nullable, no default):
  - `requested_amount NUMERIC(10,2)`
  - `original_fare NUMERIC(10,2)`
  - `resolution TEXT`
  - `refund_result JSONB`
- **Guard.** `backend/tests/test_disputes_column_drift.py` captures the real `insert_one`/`update_one` payload keys on every create and resolve path (Stripe refund, `manual_required`, flag off, rejected). It fails if a key is neither in the production column snapshot nor declared by a migration's `ADD COLUMN`. All 6 tests fail with 477 removed.

## 2. Root cause

- **No control existed.** The corporate `/adjust` endpoint already had a per-admin daily cap, but it was never extended to the consumer wallet or dispute paths.
- **Two handlers were registered on the same path.**
  - `support_router` is included at `routes/admin/__init__.py:309`, before `disputes_admin_router` at `:339`, and the first registration wins.
  - The `support.py` handler only accepted `{status, notes}`, wrote `resolution_status`, and silently ignored the dashboard's `{resolution, refund_amount, admin_note}`.
  - Production has 0 rows in `disputes` and no dispute audit rows (the orchestrator's read-only check), so no rider was affected.

## 3. Fix / remediation

1. **Migration 475** adds three columns to `public.settings`:
   - `admin_money_daily_cap_per_admin NUMERIC(12,2) NULL`
   - `admin_money_alert_threshold NUMERIC(12,2) NULL`
   - `admin_dispute_refunds_enabled BOOLEAN NOT NULL DEFAULT FALSE`

   The migration isn't applied anywhere yet, so it was edited in place. All three fields are on `SettingsUpdateRequest`.
2. **`services/admin_money_caps.enforce_admin_money_action_cap`** runs immediately before money moves:
   - **Cap.** It sums the admin's own `audit_logs` rows for today (UTC): `wallet_credit` and `wallet_debit` `details.amount`, plus `dispute_resolved` `details.refund_amount` **only when `details.refund_issued` is True**. If this action pushes the admin over the cap, it returns 403 and nothing moves.
   - **Fail closed.** If the check itself fails, it returns 503 and logs with `logger.error`.
   - **Alert.** An action at or above the threshold is still allowed, but it raises a warning log, an audit row and a Sentry event tagged `domain=admin`.
   - **Excluded rows.** The alert and blocked audit rows are deliberately not counted.
   - **Both settings NULL** means no database query for the daily sum; the settings themselves are still read from the 60s cache.
3. **Routing fix (A).** The duplicate `admin_resolve_dispute` and its `DisputeResolveRequest` model are removed from `support.py`, so `routes/disputes.py` now serves the dashboard.
   - `disputes.py` now also writes `resolved_by`, which only the removed handler did (F-32).
4. **Refund flag (A)** in `routes/disputes.py`. An approved or partial resolution with a refund amount reads `admin_dispute_refunds_enabled` through `settings_loader`:
   - **Flag off (shipped):**
     - no Stripe call and no cap check;
     - the dispute is still marked `resolved`;
     - `refund_amount` is stored as 0 and the approved amount goes into `refund_result.approved_amount`;
     - the response carries `refund_issued: false` plus a "no refund was issued, refund manually" `message`;
     - the audit row has `refund_issued: false`;
     - the rider push says "approved" with no refund claim.
   - **Flag on:** the N23 cap is enforced, then the existing Stripe path runs. `refund_issued` is true only when `Refund.create` returns.
   - **Flag read fails:** 503, no refund, dispute stays open.
   - **Rejected:** the flag is never read, so behaviour is identical on or off.
5. **Super-admin gate (B).** `admin_update_settings` returns 403 when a non-super_admin **changes** any of the three fields.
   - The comparison is by value (Decimal/bool) against the stored row.
   - I did not reject on mere presence in the payload, as the brief asked. The dashboard sends the whole settings object back on every save (`admin-dashboard/src/app/dashboard/settings/page.tsx:188`). The NOT NULL flag is therefore always in that payload, so a presence check would 403 every non-super-admin settings save.
   - This follows the existing `_SUPER_ADMIN_ONLY_FIELDS` pattern, which compares stored values for the same reason.
6. **Reviewer should-fix.** `AdminDebitRequest.amount` changed from `float` to `Decimal`, matching credit.

**Scope decisions:**
- Super-admins are **not** exempt from the cap.
- The cap returns 403, not the corporate cap's 429.

**Alternatives rejected:**
- Summing the cap from `wallet_transactions.metadata.admin_id`: dispute refunds never write there, and the query layer can't filter on JSON metadata.
- Fixing the route by moving `disputes_admin_router` ahead of `support_router`: that would leave a dead duplicate handler that could silently win again if someone reorders the includes.
- A presence-based super-admin check: it breaks unrelated saves (see item 5).

**Round 3 changes:**
- **Refund truth.** With the flag on, a refund counts as issued only when Stripe's refund status is `succeeded` or `pending`. `failed`, `canceled` or anything else means:
  - `refund_issued: false`, plus the "refund manually" message;
  - the audit row has `refund_issued=false`, so the cap doesn't count it;
  - no "refund issued" rider push;
  - `refund_amount` stored as 0;
  - a `logger.error` for manual follow-up.
- **Dashboard notice.** `disputes/page.tsx::handleResolve` now reads the response. When `refund_issued === false` for approved or partial_refund, it shows the backend `message` in a destructive toast, using the page's existing `useToast` pattern, instead of closing silently.

**Round 4 changes** (spinr-money-auditor SHOULD-FIX; round 2 was rated SAFE TO MERGE):

- **Compare-and-set on the dispute resolve (`f39fe9d`).**
  - **Problem.** Two concurrent resolves both passed the read-then-check "already resolved" guard. Both then wrote a `dispute_resolved` audit row, so the N23 cap double-counted.
  - **Fix.** The final status write is now `update_one({"id": id, "status": {"$nin": ["resolved", "rejected"]}}, ...)`. If 0 rows match, it returns **409 "Dispute already resolved"** with no audit row and no rider push. This is the same pattern as ride acceptance's `{'status': 'searching'}` filter.
  - **Chosen approach: finalize-time CAS, not claim-first.** With the flag on, a race loser may already have called Stripe. It uses the same deterministic idempotency key (`refund-dispute-{id}`), so Stripe either replays the winner's refund (same amount) or rejects a parameter mismatch with a 400, which surfaces as a 502. **No second refund is possible either way.**
  - **Rejected alternative: claim-first.** This would CAS the status to `resolving`, call Stripe, then finalize, reverting on Stripe failure. It adds a new status value, which `disputes.status` has no CHECK constraint against (migration 10 is plain TEXT). But it also adds three problems:
    1. a stuck `resolving` row if the process dies between claim and finalize, which would need a sweeper;
    2. a revert path that can itself fail;
    3. a new value the dashboard's status tabs and `admin_dispute_stats_rollup` don't know about.

    It would buy nothing money-wise, because the idempotency key already prevents a duplicate refund. The finalize-time CAS also keeps the existing retry property: if the DB write fails after Stripe succeeds, the dispute stays open and a retry replays Stripe, then finalizes.
  - **Test.** `test_concurrent_resolve_loser_gets_409_and_no_second_audit_row` runs two resolves against one simulated row. Both read it as open. The first finalizes; the second gets 409, with exactly 1 audit row and 1 push. Both Stripe calls carry the same idempotency key and amount, so the second is a replay. The test fails with the CAS reverted.
  - **Edge case.** A dispute whose `status` is SQL NULL would never match `NOT IN` and would always get 409. `create_dispute` sets `open`, admin create sets `pending`, and the column defaults to `pending`, so NULL isn't reachable today.
- **"Approve Full Refund" pre-fill (`a192777`).** This fixes the round 3 gap.
  - Opening the resolve dialog pre-fills a visible, editable "Refund Amount ($)" input with the dispute's `original_fare`, for both `approved` and `partial_refund`.
  - The same `getPartialRefundError` validation applies: the amount must be more than 0 and no more than the original fare.
  - The amount is sent as `refund_amount`. `rejected` sends no amount.
  - Nothing is defaulted server-side, and the `refund_issued: false` toast is kept.
  - If `original_fare` is missing or 0 (old rows from before migration 477), the input starts empty and validation blocks submit until the admin enters an amount.

## 4. Risk & impact on existing functionality

**Blast radius: backend admin money endpoints plus one admin route's handler and permission. Riders and drivers are unaffected while the flag is off.**

- **Callers of the resolve route.** The only one is `admin-dashboard/src/lib/api/analytics-payouts.ts::resolveDispute` (used by `app/dashboard/disputes/page.tsx`). No backend, AI-tool or e2e caller exists. The support tab's dispute panel uses `PUT /disputes/{id}` (`updateDispute`), which is unchanged.
- **support.py callers and tests changed:**
  - `backend/tests/test_admin_support_routes.py::test_resolve_dispute` asserted the removed `resolution_status` write. It was replaced by:
    - `test_resolve_route_is_served_by_disputes_py`: the real app, the dashboard payload over HTTP, and an assertion that the Stripe refund and `resolution` write happen;
    - `test_resolve_route_flag_off_records_without_refund`;
    - `test_resolve_route_now_requires_disputes_module`, covering both the "support-only → 403" and "disputes → 200" cases.
  - `backend/tests/test_disputes_admin_coverage.py`: its stale "admin_router is never mounted" docstring was corrected. The three refund-path tests now set the flag on.
  - Nothing else referenced `DisputeResolveRequest`, the `support.py` `admin_resolve_dispute`, or `resolution_status` writes. `resolution_status`/`resolution_notes` columns stay in the table and are no longer written.
- **Permission change (recorded explicitly).** `PUT /api/admin/disputes/{id}/resolve` used to require `require_module("support")` and now requires `require_module("disputes")`. `staff.py` `ROLE_PRESETS`:
  - `super_admin` has all modules and is unaffected.
  - `support` holds **both** `support` and `disputes`, so it is unaffected.
  - `operations` and `finance` hold neither, so they are unaffected.
  - Only a **custom** role with `support` but not `disputes` loses access, and one with `disputes` but not `support` gains it.
  - The dashboard page itself is still gated client-side by `useRequireModule("support")`, so a disputes-only custom admin can't reach the page but could call the API.
- **Other `/disputes` routes stay in `support.py`.** `GET /disputes`, `GET /disputes/stats` and `PUT /disputes/{id}` are unchanged and still shadow `disputes.py`'s `GET ""`, which is out of scope.
- **Behaviour change for admin-created disputes.** Disputes created via `POST /admin/disputes` have status `pending`, no `original_fare`, and may have no ride or user. Resolving one now returns:
  - 409 "requires manual review" if it has no ride or claimant;
  - 400 if `refund_amount > original_fare` (0).

  Previously the resolve always "succeeded", although it wrote nothing meaningful. Production has 0 dispute rows.
- **Rider push copy.** "A refund of $X has been issued" is now only sent when a Stripe refund was actually created. The `manual_required` (no card payment) path no longer claims a refund was issued.
- **`audit_logs`:**
  - New reads: one query per capped action.
  - New action names: `admin_money_cap_blocked`, `admin_money_threshold_alert`.
  - `dispute_resolved` details gain `refund_issued`.
- **Admin money paths not covered by the cap:** `wallet_import`, corporate `/adjust` (has its own cap), ride waivers and pay-links, auto-payouts.
- There is no interaction with the ride state machine, background loops, or wallet RPC locking.

## 5. User-experience effect

- **Internal admins, dispute resolve.** Until a super_admin turns `admin_dispute_refunds_enabled` on:
  - approving a refund marks the dispute resolved and issues no refund;
  - the API response says "Dispute resolved, but no refund was issued. Issue the refund manually in Stripe…";
  - the dashboard now shows that message as a toast titled "Dispute resolved, no refund issued" when the dialog closes (round 3). The same toast appears if Stripe returns a `failed` or `canceled` refund with the flag on.
- **"Approve Full Refund" (fixed in round 4).**
  - **Before:** it sent no amount, so no refund was ever made.
  - **Now:** the amount field is pre-filled with the original fare, is editable, and is sent. The admin sees and can change the exact amount before submitting.
  - **Concurrent resolves:** a second admin resolving the same dispute at the same moment gets "Dispute already resolved", shown inline in the dialog through the existing `resolveError` path.
- **Internal admins, cap.** Once E-F7 values are set, an over-cap credit, debit or refund fails with "Daily admin money-action cap of $X reached … Nothing was moved…".
- **Internal admins, settings.** A non-super-admin changing any of the three settings gets 403 "Only super admins can change <field>". Unchanged round-trips still save.
- **Riders.** A dispute approved while the flag is off gets "Your dispute has been approved." with no refund claim. With the flag on, they get the same push as before plus a real refund.
- **Visibility.** Changes are visible to already-logged-in admins within the 60s settings cache. The dashboard change is a toast on `/dashboard/disputes`. That page is not one of the 6 visual-regression baselines in `e2e/visual-regression.spec.ts` (login, dashboard-home, rides, drivers, monitoring, settings), so there is no baseline to update; the toast was reasoned about, not screenshotted.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/475_admin_money_action_caps.sql` | cap, threshold, `admin_dispute_refunds_enabled` columns + comments + rollback | Settings storage, all off by default |
| `backend/routes/admin/settings.py` | 3 model fields; `_SUPER_ADMIN_ONLY_MONEY_FIELDS` + value-compare gate | Settable; super_admin-only to change (B) |
| `backend/tests/test_admin_settings_write_allowlist_drift.py` | Snapshot gains 3 columns | Drift guard rule |
| `backend/services/admin_money_caps.py` | Cap/alert service; counts only `refund_issued` dispute rows | N23 control |
| `backend/routes/admin/wallet.py` | Cap call before `wallet_apply_delta`; debit `amount` is Decimal | Enforce before money moves; no float money |
| `backend/routes/disputes.py` | Flag gate, cap call, `refund_issued`, response message, `resolved_by`, refund_amount 0 when not issued | (A) + cap |
| `backend/routes/admin/support.py` | Removed duplicate resolve handler + `DisputeResolveRequest` | Route collision (A) |
| `backend/tests/test_admin_money_caps.py` | 10 unit tests (mock_supabase_client) | Cap, scope, NULL, alert, 503, issued-only counting |
| `backend/tests/test_admin_money_caps_routes.py` | 15 endpoint tests | Wallet 403/503; dispute flag on/off, over cap, flag-read 503, rejected parity |
| `backend/tests/test_admin_settings_money_fields_super_admin.py` | 9 tests | (B) allowed + denied paths |
| `backend/tests/test_admin_support_routes.py` | Old resolve test replaced by 4 HTTP route/permission tests | Routing fix proof |
| `backend/tests/test_dispute_refund_cents.py`, `test_routes_disputes_coverage.py`, `test_disputes_admin_coverage.py` | Refund-path tests set the flag on; docstring correction. `test_routes_disputes_coverage.py` also got a pre-existing unformatted block ruff-formatted, because the hook requires it | Keep existing coverage valid |

## 7. Before / after

**Routing:**
```python
# Before — routes/admin/support.py (registered first, so it served the dashboard)
@router.put("/disputes/{dispute_id}/resolve")
async def admin_resolve_dispute(dispute_id, resolution: DisputeResolveRequest, admin=...):
    await db_supabase.update_one("disputes", {"id": dispute_id},
        {"resolution_status": resolution.status, "resolution_notes": ..., "resolved_by": admin["id"], ...})
    return {"message": "Dispute resolved"}   # dashboard's refund_amount silently ignored
```
```python
# After — handler removed; routes/disputes.py admin_resolve_dispute serves the route
```

**Flag** (`routes/disputes.py`):
```python
# Before
if req.resolution in ("approved", "partial_refund") and req.refund_amount:
    ... stripe.Refund.create(...)
```
```python
# After
wants_refund = req.resolution in ("approved", "partial_refund") and bool(req.refund_amount)
if wants_refund:
    settings = await get_app_settings()          # failure -> 503, nothing refunded
    refunds_enabled = settings.get("admin_dispute_refunds_enabled") is True
if wants_refund and not refunds_enabled:
    refund_result = {"status": "not_issued", ..., "approved_amount": str(req.refund_amount)}
elif wants_refund:
    await enforce_admin_money_action_cap(...)    # 403 before any Stripe call
    ... stripe.Refund.create(...); refund_issued = True
```

**Dry run** (cap $100, admin A):
- **Flag off:** A credits $40, debits $25, then approves a $30 dispute refund. The refund is recorded with no Stripe call and not counted, so the total stays at $65. A following $35 credit is allowed, bringing the total to exactly $100.
- **Flag on:** the same $30 refund calls Stripe and counts, bringing the total to $95, so the next $10 credit gets a 403 and `wallet_apply_delta` is never called.

## Deploy order (required)

1. Apply **475** and **477** before deploying this backend. Without 475, saving admin settings fails (PGRST204) on the new fields, because the dashboard round-trips the settings object. Without 477, dispute create and resolve fail.
2. Deploy the backend and dashboard.
3. Only then may a super_admin turn `admin_dispute_refunds_enabled` on. **477 must be applied before the flag is turned on**, or the resolve write fails after the Stripe refund.

## 8. Rollback plan

- **Flag.** Leave it off, or turn it back off. This needs no deploy and takes effect within the 60s cache: `UPDATE public.settings SET admin_dispute_refunds_enabled = FALSE WHERE id = 'app_settings';`. A Stripe refund already issued while the flag was on is not reversed by this. It is a real refund and needs a manual Stripe-side decision.
- **Cap/threshold:** `UPDATE public.settings SET admin_money_daily_cap_per_admin = NULL, admin_money_alert_threshold = NULL WHERE id = 'app_settings';`. This has to be SQL, because the API drops None values.
- **Routing change:** revert commit `29f1725`, which restores the `support.py` handler and its shadowing. No data migration is involved. Rows resolved in the meantime keep `resolution`/`resolved_by`.
- **Dashboard toast:** revert `94a1091`. It is display-only.
- **Round 4:**
  - Revert `a192777` (the dashboard pre-fill). Full approvals then send no amount again.
  - Revert `f39fe9d` (the CAS). It only narrows which writes land, and there is no data to remediate.
- **Migration 477:** leave it in place; its columns are additive and harmless. Only drop them after reverting the code that writes them (rollback SQL is in its header).
- **Schema** (only after reverting the code): `ALTER TABLE public.settings DROP COLUMN IF EXISTS admin_dispute_refunds_enabled, DROP COLUMN IF EXISTS admin_money_alert_threshold, DROP COLUMN IF EXISTS admin_money_daily_cap_per_admin;`

## 9. Verification performed

- [x] **Targeted suites, round 2:**
  - 299 passed across 19 files: the wallet, dispute, support and new test files plus `test_admin_rbac`, `test_admin_routes_auth` and `test_loguru_call_conventions`.
  - 470 passed across 37 further settings, dispute and support files: every `*settings*`, `*dispute*` and `*support*` test file.
- [x] **Round 1 checks:**
  - The 4 blocking or fail-closed wallet and dispute cap tests fail with the wiring reverted.
  - Full `pytest -m "not slow"` gave 16352 passed. The only failures were 6 order-dependent ones in `test_verify_otp_login_flow.py`, which pass in isolation, plus the `tests/rls` errors that need real Postgres.
- [x] **Round-2 full `pytest -m "not slow"`** (with `tests/rls` ignored): 16369 passed, 6 failed. The 6 failures are the same order-dependent `test_verify_otp_login_flow.py` tests as round 1 (auth code, untouched here), and there are no other failures. The run started before commit `3894ca7`, which is a small `refund_amount` change whose affected files were re-run separately (99 passed).
- [x] **Round 3:**
  - Backend: `test_disputes_column_drift.py` 6 passed (6 fail without 477); refund-status tests 3 new, and `test_admin_money_caps*` 26 passed; dispute, support and drift files 105 passed; `test_migration*` plus drift 251 passed.
  - Dashboard: `npm ci`, then **`npm run build` (Next.js production build) exited 0** with "Compiled successfully".
  - Dashboard tests: `npx vitest run src/app/dashboard/disputes/ src/__tests__/dashboard/pages.smoke.test.tsx src/app/dashboard/support` gave 38 passed across 3 files. The new `page.resolve.test.tsx` fails with the page change reverted.
  - `eslint` on the changed files: 0 errors, 3 pre-existing `set-state-in-effect` warnings.
- [x] **Round 4:**
  - Backend: 395 passed across 25 files (dispute, cap, support, settings, column guard, wallet, `test_admin_rbac`, `test_loguru_call_conventions`). The new race test fails without the CAS.
  - Dashboard: `npx vitest run src/app/dashboard/disputes/ src/__tests__/dashboard/pages.smoke.test.tsx src/app/dashboard/support` gave 41 passed across 3 files (3 new pre-fill tests).
  - **`npm run build` exited 0** ("Compiled successfully").
  - `eslint`: 0 errors, 3 pre-existing warnings.
- [x] `ruff check` and `ruff format --check` are clean, and the pre-commit hook ran on every commit (no `--no-verify`).
- [x] **Blast-radius grep:**
  - every `/disputes/{id}/resolve` registration and dashboard caller;
  - `DisputeResolveRequest` and `resolution_status` readers and writers;
  - `ROLE_PRESETS`;
  - `total_refunded` (the `admin_dispute_stats_rollup` sum);
  - the dashboard's settings save payload and its 403 handling.
- [x] **Conventions:** Decimal only, the dual import pattern, fail closed with `logger.error`, and PIPEDA (IDs and amounts only in logs and Sentry).
- [x] **Flagged:** refunds are off, the cap and threshold are NULL, and all three are super_admin-only.

## What was NOT verified

- Migration 477 has not been applied anywhere. The drift test proves only that a migration declares every written key, not that production has run it.
- The dashboard toast was not screenshotted or exercised in a real browser. It was covered by vitest with a mocked `useToast`, plus the production build.
- Nothing ran against live Supabase or Stripe. The migration wasn't applied, Sentry was mocked, and there was no staging run.
- **Known follow-up: `pending` refunds.** A refund Stripe returns as `pending` counts as issued: `refund_issued=true`, it's counted by the cap, and the rider push says a refund was issued. If Stripe later fails it, nothing reconciles `refund_result`, the audit row or the cap total, because no `charge.refund.updated` handling updates disputes. The cap error only goes one way: it can only make the cap **stricter** (an unrefunded amount stays counted), never looser. The rider push would be wrong in that case, and the dispute would need manual follow-up. This is tracked as a follow-up, not fixed here.
- **Round 4.**
  - The CAS was tested against a simulated row, not a real Postgres `NOT IN` via PostgREST. `_apply_filters` compiles `$nin` to `.not_.in_`.
  - The dashboard pre-fill was covered by vitest and the production build, not a real browser.
- **Accepted gaps in the interim control:**
  - It's check-then-act, not a lock.
  - Wallet audit rows are written after the money moves, and `log_admin_action` swallows failures, which undercounts.
  - A deduped wallet replay writes a second audit row, which overcounts.
  - The threshold alert row remains even if the action then fails.
- **Reviewers.** `spinr-money-auditor` and `spinr-admin-rbac-reviewer` reviewed round 1 (orchestrator). Round 2 has not been re-reviewed.
