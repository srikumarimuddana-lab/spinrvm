# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-31 |
| Author | Claude Code (session_011im4qixzWk7mNQfPLPhChK) |
| Surface(s) | backend, admin-dashboard |
| Domain (Sentry tag) | corporate, admin |
| PR / commit link | PR #4791 |
| Related issue or gap ID | #4602 (findings 2, 3), #4605 (finding 2) |

This log covers three independent fixes bundled in one PR (each in its own commit, no shared files): two on the corporate wallet/billing money path, one on the admin-dashboard nav.

## 1. Issue / gap identified

- **#4602 finding 2**: ad-hoc corporate wallet adjustments (`POST /company/{id}/wallet/adjust`) had no idempotency protection — a dashboard timeout-retry or double-click could double-apply a real dollar amount.
- **#4602 finding 3**: closing a corporate account never cancelled its Stripe SaaS subscription — required a separate, easy-to-forget admin step, risking a terminated customer being billed with zero platform access.
- **#4605 finding 2**: the admin sidebar's Audit Logs link only checked the `audit` module, but its backend endpoints require `audit` AND `dashboard` (an undocumented AND at the router-mount level) — a role granted `audit` alone saw the link and got a 403 on every click.

## 2. Root cause

- **4602/2**: `corporate_wallet_apply_delta`'s dedup logic only covers `stripe_payment_intent_id` (top-ups) and `ride_id` (internal ride-settlement debits) — the ad-hoc admin-adjustment call shape has neither, so it was never a dedup target.
- **4602/3**: `change_company_status`'s `CLOSED` transition disables auto-topup, cancels pre-pickup rides, and winds down the wallet, but never called `corporate_subscription_service.cancel_subscription` — that function's only caller was the separate, unlinked `routes/corporate_subscriptions.py` admin action.
- **4605/2**: `routes/admin/compliance.py`'s audit-log handlers require `require_module("audit")` per-handler; the router mounts under `require_module("dashboard")` in `routes/admin/__init__.py`. FastAPI ANDs these. The frontend `sidebar.tsx` nav item only checked `userModules.includes("audit")`.

## 3. Fix / remediation

- **4602/2**: migration 376 adds `corporate_wallet_transactions.client_idempotency_key` (nullable) + a partial unique index + a third RPC dedup short-circuit, purely additive (existing callers unaffected — new param defaults NULL). `AdjustRequest` accepts an optional key from the client, falling back to a 1-minute time-bucket keyed on wallet+amount+sha256(notes), mirroring `TopUpRequest`'s existing pattern.
- **4602/3**: `change_company_status` now calls `cancel_subscription(at_period_end=False)` on the `CLOSED` transition only (not `SUSPENDED`, which is reversible). `no_active_subscription` is treated as a no-op, not an error; any other failure is caught and logged, never crashes the close transition.
- **4605/2**: added `NavItem.requiresAllModules` (an AND-list) to `sidebar.tsx`, following the existing `superAdminOnly` precedent in the same file. Only the Audit Logs item uses it. Backend gate is unchanged.

Two unrelated, pre-existing CI-red items were also fixed in this PR since they were caught while driving this PR to green (both verified failing identically on a clean `origin/main` checkout, unrelated to this PR's own diff):
- `pages.smoke.test.tsx`'s lucide-react mock was missing the `Circle` export (gap introduced by PR #4785's `MigrationChecklist.tsx`, never this PR's own change).
- 9 backend tests (`test_stale_intent_reconciler.py` ×6, `test_admin_drivers_coverage.py`, `test_admin_drivers_expiring.py`, `test_admin_extended.py`) had the same `get_rows_batched_in` test-mocking gap already fixed for `test_admin_approval_queue.py` in #4783 — this exact fix had been pushed to this branch earlier in the session but was dropped when an earlier PR on the same branch (#4782) was auto-merged mid-push; recovered via `git cherry-pick` from local history and re-applied here.

## 4. Risk & impact on existing functionality

- **4602/2**: `corporate_wallet_apply_delta` is called by `apply_topup`, `apply_adjustment`, `apply_late_tip_master_debit` (grepped all three call sites in `corporate_wallet_service.py`). Only `apply_adjustment` now passes a non-None `client_idempotency_key`; the other two are unaffected (they pass `stripe_payment_intent_id`/`ride_id`, and the new short-circuit only engages when `p_stripe_pi IS NULL AND p_ride_id IS NULL AND p_client_idempotency_key IS NOT NULL`). Blast radius: isolated to the ad-hoc adjustment call path.
- **4602/3**: `cancel_subscription` (in `corporate_subscription_service.py`) is also called directly from `routes/corporate_subscriptions.py`'s admin cancel endpoint — that call site is untouched; this PR only adds a second caller. Blast radius: isolated to the `CLOSED` transition branch of `change_company_status`; `SUSPENDED` and all other transitions are untouched.
- **4605/2**: `requiresAllModules` is a new, opt-in field on `NavItem` — every other nav item (grepped the full `NAV_GROUPS` array) omits it and keeps its existing single-`module` behavior unchanged. Blast radius: isolated to the Audit Logs link's visibility; no backend/access change.
- No interaction with background loops, the ride state machine, or any other wallet-delta caller beyond what's named above.

## 5. User-experience effect

- **Corporate admin (billing)**: a retried/double-clicked wallet adjustment no longer double-applies — silently safer, no new UI.
- **Corporate admin (self-service close)**: closing an account now also stops Stripe billing immediately — a real behavior change, but the intended one (previously required a separate manual step that could be forgotten).
- **Internal admin (custom role holding only `audit`)**: no longer sees a nav link that always 403s — a link disappears for anyone who never actually had access to what it pointed to.
- None of these are visible mid-session to a rider or driver; all are internal/admin-facing only.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/376_corporate_wallet_adjust_idempotency.sql` | New column + unique index + widened RPC | #4602/2 |
| `backend/services/corporate_wallet_service.py` | Thread `client_idempotency_key` through `_apply`/`apply_adjustment` | #4602/2 |
| `backend/routes/corporate_wallet.py` | `AdjustRequest` gains the field; fallback-key generation | #4602/2 |
| `backend/tests/services/test_corporate_wallet_service.py`, `backend/tests/test_corporate_wallet_routes.py` | New tests | #4602/2 |
| `backend/routes/corporate_accounts.py` | Cascade `cancel_subscription` on `CLOSED` | #4602/3 |
| `backend/tests/test_corporate_status.py` | New tests | #4602/3 |
| `admin-dashboard/src/components/sidebar.tsx` | `requiresAllModules` field + filter check | #4605/2 |
| `admin-dashboard/src/__tests__/dashboard/pages.smoke.test.tsx` | Add missing `Circle` icon mock | pre-existing, unrelated to this PR's own diff |
| `backend/tests/test_stale_intent_reconciler.py`, `test_admin_drivers_coverage.py`, `test_admin_drivers_expiring.py`, `test_admin_extended.py` | Same `get_rows_batched_in` mocking fix as #4783 | pre-existing, unrelated to this PR's own diff |

## 7. Before / after

**#4602 finding 3** (`backend/routes/corporate_accounts.py`, inside `change_company_status`):

```python
# Before: no subscription action on close at all — winddown_result block
# was the last thing in the CLOSED branch.

# After:
subscription_cancel_result = None
if transition.status == CompanyStatus.CLOSED:
    try:
        subscription_cancel_result = await cancel_subscription(
            company_id=normalized_id,
            admin_id=str(current_admin.get("id") or ""),
            at_period_end=False,
        )
    except CorporateSubscriptionError as exc:
        if str(exc) != "no_active_subscription":
            logger.error(...)
            subscription_cancel_result = {"skipped_reason": str(exc)}
    except Exception:
        logger.error(...)
        subscription_cancel_result = {"skipped_reason": "unhandled_exception"}
```

**#4605 finding 2** (`admin-dashboard/src/components/sidebar.tsx`):

```tsx
// Before
{ href: "/dashboard/audit-logs", label: "Audit Logs", icon: Shield, module: "audit" },
// filter: return isSuperAdmin || userModules.includes(item.module);

// After
{
    href: "/dashboard/audit-logs", label: "Audit Logs", icon: Shield, module: "audit",
    requiresAllModules: ["audit", "dashboard"],
},
// filter: if (item.requiresAllModules) return isSuperAdmin || item.requiresAllModules.every(m => userModules.includes(m));
```

## 8. Rollback plan

- **4602/2**: `git-revert-safe` for the app code. The migration's own header comment documents the exact rollback SQL (drop the unique index, drop the column, restore the RPC to migration 297's body) — safe only if no `client_idempotency_key` values are relied on yet, per the migration's own note. No data already written is mutated by this migration.
- **4602/3**: `git-revert-safe` — no data-level remediation needed. A reverted deploy simply stops calling `cancel_subscription` on close; any subscription already cancelled by this code stays cancelled (no auto-uncancel either way, so no double-charge risk from reverting).
- **4605/2**: `git-revert-safe`.

## 9. Verification performed

- [x] Automated tests run (unit): `pytest backend/tests/services/test_corporate_wallet_service.py backend/tests/test_corporate_wallet_routes.py backend/tests/test_corporate_status.py backend/tests/test_corporate_accounts_lifecycle.py backend/tests/test_corporate_subscriptions_route.py backend/tests/test_corporate_rpc_ride_idempotency.py` — 106 passed. Plus the 5 recovered/pre-existing test files (`test_admin_approval_queue.py`, `test_stale_intent_reconciler.py`, `test_admin_drivers_coverage.py`, `test_admin_drivers_expiring.py`, `test_admin_extended.py`) — 228 passed.
- [ ] Manual repro steps followed in staging — **not done**, verified against mocked Supabase/Stripe fixtures only.
- [x] Blast-radius grep performed: all 3 call sites of `corporate_wallet_apply_delta` checked; the one other caller of `cancel_subscription` checked; the full `NAV_GROUPS` array checked for other `module`/`requiresAllModules` usages.
- [x] Reviewed against relevant CLAUDE.md conventions: Stripe idempotency (4602/2 adds it where missing), money-function safety (`SECURITY DEFINER` + `search_path` preserved in the migration), migration append-only/reversible-on-paper conventions.
- [ ] Feature-flagged — not applicable; these are correctness/security-posture fixes to existing behavior, not new product surfaces.
- **Full admin-dashboard production build (`npm run build`)**: **not run** this pass — verified via `tsc --noEmit` (clean) and targeted `vitest run` on the one changed test file, consistent with this session's established verification depth for admin-dashboard config/nav-only changes.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (all three are plain code/schema reverts, no live-data remediation required).
- [x] Blast radius is stated, not assumed (see section 4, with the actual grep results named per fix).
- [x] No silent behavior change to an already-shipped flow without the UX field filled in — #4602/3 is a real behavior change and section 5 states it explicitly; #4602/2 and #4605/2 are correctness/safety fixes with no functional change to the shipped happy path.
