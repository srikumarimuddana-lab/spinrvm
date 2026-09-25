# Change Impact & Risk Log — Remove the disabled in-app dispute code

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-25 |
| Author | Claude Code (AI-assisted), follow-up scoped by PR #5811 |
| Surface(s) | backend / admin-dashboard |
| Domain (Sentry tag) | payments / admin |
| PR / commit link | branch `claude/remove-disabled-dispute-code` |
| Related issue or gap ID | PR #5811 "Follow-ups" (cleanup PR + duplicate `GET /admin/disputes`); `docs/change-log/2026-09-25-disable-in-app-disputes.md` |

## 1. Issue / gap identified

PR #5811 disabled in-app disputes but, by design, left the old handlers in the code (unrouted), along with their schemas, a Zoho helper, unused dashboard helpers and the tests that called the dead handlers directly. It also recorded a pre-existing duplicate: two handlers were registered for `GET /admin/disputes`.

## 2. Root cause

- **Dead code:** #5811 was a disable-first change, with deletion deferred so the disable could ship small and be reverted by restoring decorators. That deferral is now done.
- **Duplicate GET:** `routes/admin/support.py` and `routes/disputes.py` (`admin_router`) both registered `GET /disputes` on the admin router. `routes/admin/__init__.py` mounts `support_router` (line ~309) before `disputes_admin_router` (line ~339), so FastAPI always matched support.py's handler. The disputes.py copy (which added `ride_status` / `ride_fare` and a `"Unknown"` name default) was never reached. `scripts/check_route_shadowing.py` does not walk the `routes.admin` package, so nothing flagged it.

## 3. Fix / remediation

Deleted, nothing else changed in behaviour except the two unused user GETs below.

- **`backend/routes/disputes.py`** (472 → 57 lines): deleted `create_dispute`, `admin_resolve_dispute`, the shadowed `admin_get_disputes`, the user `get_user_disputes` / `get_dispute` GETs, `CreateDisputeRequest`, `ResolveDisputeRequest`, and every import only they used. What is left: the two 410 stubs (`POST /disputes`, `PUT /admin/disputes/{id}/resolve`, still under `require_module("disputes")`) and their detail strings, byte-for-byte unchanged.
- **`backend/routes/admin/support.py`**: deleted the unrouted `admin_create_dispute` / `admin_update_dispute`, `DisputeCreateRequest`, `DisputeUpdateRequest` and the `create_ticket_for_dispute` import. Kept: the `POST` / `PUT /disputes[/{id}]` 410 stubs, `GET /disputes`, `GET /disputes/stats`, `GET /disputes/{id}` (read-only historical view) and `GET /disputes/chargebacks`.
- **Duplicate `GET /admin/disputes`:** resolved by deleting the never-reached disputes.py copy. support.py's handler, the one production has always served, is now the only one, so the response shape is unchanged (dispute row + `user_name` joined at read time). New tests pin it (see §9).
- **`backend/services/zoho_desk_integration.py`**: deleted `create_ticket_for_dispute`. Its only callers were the two deleted handlers. `_LINKED_TABLES`' `disputes` entry (reverse status sync) is kept.
- **User `GET /api/v1/disputes` and `GET /api/v1/disputes/{id}` deleted.** Proof no client uses them: grep of `rider-app/`, `driver-app/`, `shared/` finds no `/disputes` call; `git log -S"disputes"` / `-G` over the same paths shows the only client call ever was driver-app's `api.post('/disputes')` (removed in #5811). No build ever called the GETs. `GET /api/v1/disputes` is now 405 (the POST stub keeps the path); `GET /api/v1/disputes/{id}` is 404.
- **Admin dashboard:** deleted `getDisputes`, `getDisputeStats`, `getDisputeDetails`, `createDispute`, `updateDispute` (from `lib/api/safety-disputes.ts` and the `lib/api.ts` barrel) and `lib/disputeResolutionSchema.ts` + its test. None had an importer after #5811 (grep of `admin-dashboard/src` and `admin-dashboard/e2e`). Everything `ChargebacksTab` uses (`getChargebacks`, `Chargeback`, `downloadDisputeEvidencePack`, `submitDisputeEvidence`) is untouched.
- **Tests:** deleted the four files that only called deleted handlers directly (`test_routes_disputes_coverage.py`, `test_disputes_admin_coverage.py`, `test_dispute_refund_cents.py`, `test_disputes_column_drift.py`), the dispute-refund half of `test_admin_money_caps_routes.py`, and the `create_ticket_for_dispute` tests in `test_zoho_desk.py` / `test_zoho_desk_integration_coverage.py`. Strengthened `test_disputes_disabled.py` (see §9).

### Deliberately kept

- `disputes` table, its migrations and RLS (7-year retention); no migration in this PR.
- `admin_dispute_refunds_enabled` setting and its admin settings UI (not touched).
- The `disputes` admin permission module (still gates the resolve 410 stub).
- Admin GET list / stats / detail (read-only view of historical rows, as #5811 decided).
- All bank-chargeback code: `charge.dispute.*` webhooks, `stripe_disputes`, evidence reminder loop, evidence pack / PDF, `POST /admin/disputes/{id}/submit-evidence`, dispute-pack download, `GET /admin/disputes/chargebacks`, `ChargebacksTab`.
- `services/admin_money_caps.py`: its daily-sum still counts historical `dispute_resolved` audit rows (none in production). It is shared with the live admin wallet credit/debit path, so it is out of scope; its docstring now has a stale pointer to `routes/disputes.py` (follow-up).

## 4. Risk & impact on existing functionality

Blast radius: backend + admin-dashboard, dead code only, plus two user GETs with no caller.

- **Callers of deleted Python symbols:** grep of `backend/` (incl. tests) for `create_dispute`, `admin_resolve_dispute`, `admin_get_disputes`, `get_user_disputes`, `get_dispute`, `CreateDisputeRequest`, `ResolveDisputeRequest`, `DisputeCreateRequest`, `DisputeUpdateRequest`, `admin_create_dispute`, `admin_update_dispute`, `create_ticket_for_dispute`: only the deleted handlers and the deleted/retargeted tests. No AI tool, background loop or other route imported them.
- **Readers of the `disputes` table after this PR:** support.py GET list/stats/detail, the `admin_dispute_stats_rollup` RPC, Zoho `close_linked_records` via `_LINKED_TABLES`, and `purge`/retention SQL (unchanged). Nothing writes it (as since #5811).
- **`GET /admin/disputes` shape:** unchanged, because the handler that serves it is unchanged. No dashboard page calls it any more (the tab was removed in #5811); it stays for API-level historical access.
- **Stripe / money:** no live money path changes. The deleted `admin_resolve_dispute` was unreachable (410 stub routed in its place since #5811). Chargeback webhooks and evidence code are untouched.
- **Old app builds:** driver builds from before #5811 only call `POST /disputes`, which still returns the same 410 body. No build calls the deleted GETs.
- **Old admin dashboard builds** (cached tab before the #5811 deploy): would still call `GET /admin/disputes`, `/stats` (kept) and the 410 write stubs (kept). Unchanged.
- **Ride state machine, dispatch, background loops, wallet deltas:** not touched.

## 5. User-experience effect

None for riders, drivers or admins. No screen changes. The only externally visible API change is `GET /api/v1/disputes` (405) and `GET /api/v1/disputes/{id}` (404), which no client has ever called.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| backend/routes/disputes.py | Only the two 410 stubs remain | Delete dead handlers, schemas, shadowed admin GET, unused user GETs |
| backend/routes/admin/support.py | Deleted unrouted create/update handlers + their schemas + Zoho import; comments | Dead code; now sole `GET /admin/disputes` handler |
| backend/services/zoho_desk_integration.py | Deleted `create_ticket_for_dispute` | No caller left |
| backend/tests/test_disputes_disabled.py | Side-effect mocks moved to source modules; added module-inventory, removed-GETs, single-handler route-table and response-shape tests | Pin the cleanup and the duplicate fix |
| backend/tests/test_admin_support_routes.py | Drop the Zoho-dispute stub; assert the helper is gone | Helper deleted |
| backend/tests/test_p3_addresses_favorites_safety_disputes.py | Disputes section reduced to the 410 test | User GETs deleted; no db mock target any more |
| backend/tests/test_admin_money_caps_routes.py | Dispute-refund tests removed (wallet tests kept) | Handler deleted |
| backend/tests/test_zoho_desk.py, test_zoho_desk_integration_coverage.py | `create_ticket_for_dispute` tests removed | Helper deleted |
| backend/tests/test_routes_disputes_coverage.py, test_disputes_admin_coverage.py, test_dispute_refund_cents.py, test_disputes_column_drift.py | Deleted | Only exercised deleted handlers |
| admin-dashboard/src/lib/api/safety-disputes.ts | Deleted 5 in-app dispute helpers; header comment | No importer |
| admin-dashboard/src/lib/api.ts | Dropped the 5 re-exports | Same |
| admin-dashboard/src/lib/api/rides.ts | Header comment only | Stale pointer to `createDispute` |
| admin-dashboard/src/lib/disputeResolutionSchema.ts (+ test) | Deleted | Only served the resolve dialog removed in #5811 |
| admin-dashboard/src/app/dashboard/disputes/page.test.tsx | Mocks trimmed; asserts the helpers no longer exist | Helpers deleted |
| docs/change-log/2026-09-25-remove-disabled-dispute-code.md | This file | CLAUDE.md gate |

## 7. Before / after

```python
# Before (backend/routes/disputes.py) — two GET /admin/disputes handlers;
# support.py's was mounted first, so this one never ran.
@admin_router.get("")
async def admin_get_disputes(status=None, limit=50, offset=0, current_admin=Depends(get_current_admin)):
    ...  # adds ride_status / ride_fare, "Unknown" name default

# After: deleted. routes/admin/support.py's admin_get_disputes is the only handler.
```

```python
# Before (backend/routes/disputes.py)
@api_router.get("")
async def get_user_disputes(current_user: dict = Depends(get_current_user)): ...
@api_router.get("/{dispute_id}")
async def get_dispute(dispute_id: str, current_user: dict = Depends(get_current_user)): ...

# After: deleted (no client ever called them). GET /disputes -> 405, GET /disputes/{id} -> 404.
```

Money dry run (CLAUDE.md gate 4): an admin sends `PUT /api/admin/disputes/d1/resolve {"resolution":"approved","refund_amount":10}`. Before this PR and after it: 410, no DB read, no `stripe.Refund.create`, no push, no Zoho ticket. `test_admin_resolve_is_410_and_moves_no_money` now patches those at their source modules (`db_supabase`, `stripe.Refund.create`, `features.send_push_notification`, the Zoho client), since `routes/disputes.py` no longer imports any of them.

## 8. Rollback plan

- No data, migration, setting or Stripe object is touched; nothing to roll back in live data.
- Code: `git revert` of this PR restores the dead handlers exactly as #5811 left them (still unrouted). The live behaviour (410 stubs, admin GETs, chargebacks) is identical before and after, so a revert is only needed if a hidden importer turns up.
- If some unknown client did depend on `GET /api/v1/disputes[/{id}]`, reverting restores both.

## 9. Verification performed

- **Backend:**
  - `pytest -q -p no:cacheprovider -o addopts="" --ignore=tests/rls --ignore=tests/direct_pool -k "dispute or chargeback or support or webhook or evidence or zoho or route or money_cap or p3"`: **2380 passed, 1 skipped, 1 xfailed, 0 failed**.
  - `tests/test_disputes_disabled.py`: 15 passed. Negative check: temporarily re-adding a `GET ""` handler on `routes/disputes.py`'s `admin_router` made 3 of them fail (module inventory + single-handler for both `/api/admin` and `/api/v1/admin`), then passed again after removal. The route-table walk recurses through FastAPI 0.141's lazy `_IncludedRouter` (same approach as `test_documents.py`) and a guard test proves it reaches `routes/disputes.py`'s admin router.
  - `ruff check` and `ruff format --check` clean on every changed `.py` file. `services/zoho_desk_integration.py` and `tests/test_zoho_desk.py` were already unformatted on `origin/main`; the pre-commit hook blocks any staged file that fails `ruff format --check`, so `ruff format` rewrapped 3 pre-existing lines there (whitespace only, no logic).
- **Admin dashboard:** `npx vitest run src/app/dashboard/disputes src/__tests__/dashboard/pages.smoke.test.tsx` (3 files, 39 tests passed); `npx tsc --noEmit` clean; `npx eslint` on the 4 changed files: clean; **real `npm run build` (Next.js production build, after `npm ci`): passed**, `/dashboard/disputes` and `/dashboard/support` compiled. Full `npx vitest run`: **83 files / 748 tests passed** (was 84 / 757 before this PR; the difference is exactly the deleted `disputeResolutionSchema.test.ts`, 9 tests).
- **Blast-radius greps:** listed in §4.
- **Visual baselines:** no page component changed (only API helper modules, a deleted unused schema and a test). None of the 6 seeded pages (login, dashboard-home, dashboard-rides, dashboard-drivers, dashboard-monitoring, dashboard-settings) is affected.

## 10. What was NOT verified

- Not run against live Supabase, staging or a real device.
- Playwright e2e not run (no browsers in the container). No e2e spec referenced the deleted helpers (grep of `admin-dashboard/e2e`).
- The "no client ever called the user GETs" claim covers this repo's source and git history. A third-party or hand-written client outside the repo can't be ruled out; such a caller would now get 405/404.
- `docs/audit/**` and `ACTION_ITEMS.md` (B39 cites `lib/disputeResolutionSchema.ts`) still mention deleted files. Out of this workstream's file ownership; left as a follow-up.
- `services/admin_money_caps.py` and `routes/admin/settings.py` comments still point at `routes/disputes.py` for the dispute-refund path, and `admin_dispute_refunds_enabled` no longer has a code reader. Out of scope (shared money file / other workstream; the brief keeps the setting); follow-up.
- Pre-existing, unchanged: Zoho reverse-close (`close_linked_records` via `_LINKED_TABLES`) can still set an old dispute row with a linked Zoho ticket to `resolved`. Production has 0 `disputes` rows, so this is only a mismatch with the "read-only" wording; follow-up if rows ever appear.
- Review: a `/code-review` (medium) pass on the diff found no bugs; it raised only the two stale-comment/reverse-close notes above.
