# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-02 |
| Author | Claude Code |
| Surface(s) | backend, admin-dashboard |
| Domain (Sentry tag) | corporate, admin |
| PR / commit link | branch `claude/spinrvm-schedule-ride-review-2jsank` |
| Related issue or gap ID | Corporate + admin portal review, round 2 — end-of-round verification pass (items #51-67, all previously committed without running tests per the round's explicit instruction) |

## 1. Issue / gap identified

The round's governing instruction was to develop all 17 remaining findings
(#51-67) sequentially without running tests/CI, then run the full
backend `pytest` suite and `admin-dashboard` production build once
everything was committed. That verification pass (this commit) found 6
real, previously-uncaught defects across code committed earlier in the
round — all invisible to `ast.parse`/bracket-balance checks, all only
detectable by actually importing/running the code.

## 2. Root cause

Six independent defects, three of them the same recurring pattern flagged
earlier in this round (an auto-formatter silently drops a name from a
dual-import's `except ImportError:` branch when only the `try:` branch
appears to use it at save time):

1. **`backend/utils/audit_logger.py`** (item #52) — `except ImportError:`
   branch imported `from log_context import get_request_id` instead of
   `from utils.log_context import get_request_id`. Blocked `conftest.py`
   collection entirely (`ModuleNotFoundError`), failing all 7455 tests
   before a single one ran.
2. **`backend/server.py`** (item #63e) — `corporate_subscriptions_router`
   was mounted at two include-points but never imported. `NameError` at
   module load, same collection-blocking effect as #1.
3. **`backend/server.py`** (items #56, #63e) — `corporate_wallet_router`
   (`GET /wallet-portfolio`, item #56) and `corporate_subscriptions_router`
   (`GET /subscription-plans`, item #63e) are single-static-segment paths
   under the same `/admin/corporate-accounts` prefix as
   `corporate_accounts_router`'s catch-all `GET /{account_id}`.
   `corporate_accounts_router` was registered first at both the `/api/v1`
   and legacy `/api` mount points, so FastAPI's registration-order
   matching silently routed both static paths into `/{account_id}`,
   producing a 404 "Corporate account not found" instead of the intended
   handler. This is the identical hazard already documented and guarded
   against for `GET /kyb-reverification-due` (round2-32) — but that fix
   only protected routes *inside* `corporate_accounts.py` itself, not
   routes in *other* router files mounted after it.
4. **`backend/db_supabase.py`** (item #63b) — `update_corporate_subscription`
   present in the `try:` re-export block, silently dropped from the
   `except ImportError:` block. Not collection-blocking (only breaks when
   the non-relative import path is exercised, e.g. `patch("db_supabase.X")`
   in tests), so it slipped past every earlier `ast.parse` check.
5. **`backend/routes/corporate_wallet.py`** (item #51) — the daily
   admin-adjustment-cap check (`_check_daily_adjust_cap`) calls
   `datetime.now(timezone.utc)` but the file never imports `datetime` or
   `timezone`. `NameError` on every call to `POST .../wallet/adjust` —
   this endpoint was completely broken since item #51 was committed
   earlier in the round.
6. **`admin-dashboard/src/lib/api.ts`** (item #63g) — the new corporate
   `getSubscriptionPlans` (driver Spinr Pass subscriptions, pre-existing)
   and the round-2 corporate `getSubscriptionPlans`
   (`api/corporate.ts`, item #63e) both barrel-export under the same
   name, a genuine naming collision Turbopack refuses to build
   (`the name 'getSubscriptionPlans' is exported multiple times`).

All six existed in already-committed work from earlier in this round and
were invisible to the syntax-only/bracket-balance verification used
per-commit — exactly the category of defect the end-of-round `pytest` +
`npm run build` pass exists to catch.

## 3. Fix / remediation

1. `audit_logger.py`: corrected the `except` branch to
   `from utils.log_context import get_request_id`.
2. `server.py`: added the missing
   `from routes.corporate_subscriptions import router as corporate_subscriptions_router`.
3. `server.py`: reordered both include blocks (`v1_api_router` and the
   legacy `/api` `app.include_router` block) so `corporate_wallet_router`
   and `corporate_subscriptions_router` are included **before**
   `corporate_accounts_router`, with an inline comment explaining why the
   order is load-bearing (mirrors the static-before-dynamic convention
   already used inside `corporate_accounts.py` for
   `kyb-reverification-due`, generalized here across router files).
4. `db_supabase.py`: added `update_corporate_subscription` to the
   `except ImportError:` re-export block.
5. `corporate_wallet.py`: added
   `from datetime import datetime, timezone` to the top-level imports.
6. `admin-dashboard`: renamed the round-2 corporate function to
   `getCorporateSubscriptionPlans` in `lib/api/corporate.ts`, the
   `lib/api.ts` barrel re-export, and its one UI consumer
   (`dashboard/corporate-accounts/[id]/subscription/page.tsx`). The
   pre-existing driver-subscription `getSubscriptionPlans` (used by
   `dashboard/subscriptions/page.tsx` and `dashboard/service-areas/page.tsx`)
   is untouched.

## 4. Risk & impact on existing functionality

- **Fix 1** only affects the `except` (non-relative-import) path, which
  is exercised by `python -m backend.server`'s bare-import mode and by
  `pytest` collection. The `try` (relative-import) path used by normal
  package imports was already correct and unaffected. No other consumer
  of `utils.audit_logger` changed.
- **Fix 2** is strictly additive (a missing import). No other import in
  `server.py` was touched.
- **Fix 3 is the highest-blast-radius fix in this commit.** Reordering
  router registration changes which handler wins for any path that
  could ambiguously match more than one router. Audited every route in
  `corporate_wallet_router` and `corporate_subscriptions_router` against
  every route in `corporate_accounts_router`:
  - `corporate_wallet_router`: `/wallet-portfolio` (1 segment, was
    colliding), `/{company_id}/wallet`, `/{company_id}/wallet/topup`,
    `/{company_id}/wallet/adjust`, `/{company_id}/wallet/config` (all 2-3
    segments, never collided regardless of order).
  - `corporate_subscriptions_router`: `/subscription-plans` (1 segment,
    was colliding), `/{company_id}/subscription`,
    `/{company_id}/subscription/cancel` (2-3 segments, never collided).
  - `corporate_accounts_router`'s only single-segment dynamic route is
    `GET /{account_id}` (plus the static `GET ""` list route, already
    unaffected since it has zero path segments). No other route in that
    file has a 2-3 segment shape that could newly collide with the
    reordered routers.
  - Net effect: the two previously-swallowed static routes now resolve
    correctly; every other route (all 2-3+ segment paths) is
    unreachable by the reordering because FastAPI matches by exact
    segment-count-and-literal structure, not prefix — confirmed by
    reading the route list, not assumed.
- **Fix 4** only affects code paths taken via the non-relative
  (`db_supabase.update_corporate_subscription`) spelling — the webhook
  handler in `routes/webhooks.py` (item #63d) is the only caller, and it
  was completely broken (`AttributeError` on every corporate Stripe
  subscription webhook event) until this fix. No other caller of
  `update_corporate_subscription` exists (grepped).
- **Fix 5**: `POST /admin/corporate-accounts/{id}/wallet/adjust` (item
  #51, the daily-cap-guarded manual wallet adjustment endpoint) was
  unconditionally throwing `NameError` on every call since that item was
  committed earlier this round — this fix makes a previously
  100%-broken endpoint work for the first time. No other caller of
  `_check_daily_adjust_cap` exists (it is a private, single-use helper).
- **Fix 6**: grepped every reference to `getSubscriptionPlans` across
  `admin-dashboard/src`; only the two files above (driver subscriptions)
  still use the old name, and both were already using the driver-side
  function from `api/staff-subscriptions.ts` — unaffected by the rename
  applied only to the corporate-side function.

## 5. User-experience effect

**Internal admin-facing only**, and in every case the effect is "a
previously-broken feature now works" rather than a behavior change to a
working one:
- Admin corporate-accounts list page: previously failed to load entirely
  (backend test suite couldn't even collect, meaning the app itself would
  not have started in that state — this was caught before any deploy).
- `GET /admin/corporate-accounts/wallet-portfolio` (item #56's risk
  portfolio card) and `GET /admin/corporate-accounts/subscription-plans`
  (item #63g's plan picker): previously 404'd; now return data.
- `POST /admin/corporate-accounts/{id}/wallet/adjust`: previously
  errored on every call; now applies the adjustment and enforces the
  daily cap as item #51 intended.
- Corporate Stripe subscription webhooks (`customer.subscription.deleted`,
  `customer.subscription.updated`, `invoice.paid`,
  `invoice.payment_failed`): previously threw on every event, meaning
  `corporate_subscriptions` rows never reflected real Stripe state; now
  sync correctly.
- `admin-dashboard` production build: previously failed to build at all
  (`npm run build` exit 1); now succeeds.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/audit_logger.py` | Fixed `except` branch import path | Dual-import formatter bug blocked test collection |
| `backend/server.py` | Added missing router import; reordered 4 router-include calls | Missing import + static-route-swallowed-by-dynamic-catch-all |
| `backend/db_supabase.py` | Added missing re-export to `except` branch | Same dual-import formatter bug pattern |
| `backend/routes/corporate_wallet.py` | Added `datetime`/`timezone` import | Daily-cap check used `datetime.now()` without importing it |
| `admin-dashboard/src/lib/api/corporate.ts` | Renamed `getSubscriptionPlans` → `getCorporateSubscriptionPlans` | Name collided with the pre-existing driver-subscription export |
| `admin-dashboard/src/lib/api.ts` | Updated barrel re-export to match | Same rename |
| `admin-dashboard/src/app/dashboard/corporate-accounts/[id]/subscription/page.tsx` | Updated import + 1 call site | Same rename |
| `CLAUDE.md` | Updated stale 2026-07-28 corporate coverage numbers to current 2026-08-02 measured values | Item #62/#95 — numbers were superseded by this round's test additions |

## 7. Rollback plan

`git revert` the commit. All six fixes are corrections to already-broken
code (missing imports, wrong route order, name collision) — there is no
new behavior to disable via a flag; the "rollback" for a fix that makes a
broken endpoint work again would be to restore the broken state, which is
never the right rollback target. If any individual fix is later found to
have an unintended side effect, revert just that hunk — each of the 5
backend fixes is independently reversible (they touch disjoint lines) and
none of them depends on any other.

## 8. Verification performed

- [x] Full backend `pytest` suite: **7447 passed, 8 skipped, 1 xfailed, 0
      failed** (`43906` statements, `81%` aggregate line coverage,
      `478.04s`). This is the first time the full suite has been run
      against this round's work — per the round's explicit instruction to
      defer testing until "nothing left to be developed."
  - Before these fixes: run 1 failed at collection (`ModuleNotFoundError:
    log_context`, fix #1). Run 2 failed at collection (`NameError:
    corporate_subscriptions_router`, fix #2). Run 3 collected
    successfully (7455 items) but had 18 failures, all traced to fixes
    #3/#4/#5 above. Run 4 (this one, after fixes #3-5) is fully green.
- [x] `admin-dashboard` production build (`npm run build`, Next.js 16.2.12
      Turbopack): failed on the first attempt with the export-collision
      error (fix #6); **succeeds cleanly** after the rename — full route
      manifest generated, `EXIT_CODE=0`.
- [x] Re-grepped every reference to each renamed/re-routed symbol across
      the whole repo (not just the files touched) before and after each
      fix, per this round's established discipline for catching blast
      radius.
- [x] Coverage numbers for every `routes/corporate_*.py` and
      `services/corporate_*.py` file read directly from this run's
      `pytest --cov` output (not estimated) before updating CLAUDE.md.

## 9. Sign-off

- [x] Rollback plan is concrete — plain `git revert`, no data involved,
      no flag to flip
- [x] Blast radius is stated, not assumed — every route, every caller of
      every renamed/re-exported symbol was grepped and enumerated above,
      not just claimed clean
- [x] No silent behavior change to a *working* flow — every fix in this
      commit repairs something that was already fully broken
      (collection failure, 404, `NameError`, `AttributeError`, or build
      failure); nothing that previously worked changed behavior

## What was NOT verified

- No live Supabase/Stripe integration test — the full suite runs against
  `mock_supabase_client` and mocked Stripe calls, per this repo's
  existing test-tier conventions. The corporate subscription webhook
  fix (#4) is verified against the unit-test mocks in
  `test_webhooks_corporate_subscription.py`, not a real Stripe test-mode
  event.
- No manual click-through of the `admin-dashboard` in a browser — the
  production build compiling cleanly confirms there is no build-time
  TypeScript/bundling defect, but does not confirm runtime behavior
  (e.g., that the wallet-portfolio card renders correctly with real
  data). This closes out the automated verification this round's
  governing instruction called for; a manual smoke test in
  staging remains recommended before this branch is promoted past
  the PR stage.
- `pytest --cov`'s 60% global gate passed (80.68% total), and the
  corporate-specific 80% target now reads ~92% aggregate by hand
  computation from the per-file table in this run's output — there is
  still no dedicated `--cov-fail-under` gate scoped to
  `routes/corporate_*.py`/`services/corporate_*.py` specifically, so a
  future regression in one corporate file could hide inside the
  overall-passing 80% global gate. Tracked as a standing gap, not fixed
  in this commit.
