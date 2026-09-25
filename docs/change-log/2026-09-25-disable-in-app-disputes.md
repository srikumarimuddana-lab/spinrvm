# Change Impact & Risk Log — Disable in-app disputes

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-25 |
| Author | Claude Code (AI-assisted), owner-approved scope |
| Surface(s) | backend / driver-app / admin-dashboard / docs (legal drafts) |
| Domain (Sentry tag) | payments / admin |
| PR / commit link | branch `claude/disable-in-app-disputes` (local commits only, not pushed) |
| Related issue or gap ID | Owner decision 2026-09-25: "no in-app dispute option". Approach: disable now, full code cleanup later |

## 1. Issue / gap identified

Spinr shipped an in-app dispute flow: drivers could file one from trip detail (`POST /disputes`), and admins could resolve disputes with a Stripe refund, edit them, or hard-delete them. The owner has decided Spinr has no in-app dispute option. A user who disagrees with a charge emails support@spinr.ca, which becomes a Zoho Desk ticket, or contacts their card issuer, which becomes a bank chargeback. Production has never had a row in `disputes`.

## 2. Root cause

This is a product decision, not a bug. The in-app flow ran alongside the two channels the owner wants to keep: bank chargebacks (Stripe `charge.dispute.*` webhooks into `stripe_disputes`) and support email. The flow also carried two liabilities of its own:
- a second refund money path (`PUT /admin/disputes/{id}/resolve`, which calls Stripe `Refund.create`);
- a hard `DELETE` of a financial record, which breaks the 7-year retention rule.

## 3. Fix / remediation

The flow is disabled, not deleted. The follow-up cleanup PR removes the code.

- **Backend, user side:** `POST /api/v1/disputes` returns **410** before any DB read, Zoho ticket or push. The detail is structured: `{"code": "IN_APP_DISPUTES_DISABLED", "message": "In-app disputes are not available. For questions about a charge, email support@spinr.ca or contact your card issuer."}`.
  - Why structured: a plain-string 4xx detail passes through `utils.pii.redact_error_detail` in `http_exception_handler`, and its email regex would have turned the support address into `[redacted]`. The first test run caught this.
  - `shared/api/client.ts` `extractError` already reads the `{code, message}` shape, the same one `OUTSIDE_SERVICE_AREA` uses.
  - `GET /disputes` and `GET /disputes/{id}` are unchanged (read-only), so old clients don't break.
- **Backend, admin side:**
  - These return 410, with nothing written, no Stripe call, no push, no Zoho ticket and no audit row:
    - `PUT /api/admin/disputes/{id}/resolve` (`routes/disputes.py`, still behind `require_module("disputes")`);
    - `POST /api/admin/disputes` (`routes/admin/support.py`);
    - `PUT /api/admin/disputes/{id}` (`routes/admin/support.py`).
  - **`DELETE /api/admin/disputes/{id}` is removed entirely.** It now returns 405, because GET and PUT still match the path.
  - The admin read endpoints stay as a historical read-only view: GET list, GET `/disputes/stats` and GET `/disputes/{id}`.
  - Decision beyond the brief: the brief named POST and resolve. `PUT /admin/disputes/{id}` (a generic field edit, including `status` and `refund_amount`) was also turned into a 410 because it is a write to the same records. Leaving it open would contradict "keep admin GET list/stats/detail read-only".
  - The original handler functions stay in place but are no longer routed (no decorator). Their existing direct-call unit tests (`test_routes_disputes_coverage.py`, `test_disputes_admin_coverage.py`, `test_admin_money_caps_routes.py`, `test_dispute_refund_cents.py`, `test_disputes_column_drift.py`) still pass unchanged. The cleanup PR deletes the functions and those tests together.
- **Admin dashboard:**
  - `/dashboard/disputes`, which the Support page's `?tab=disputes` embeds, now renders only `ChargebacksTab`. The "Rider Disputes" tab, with its list, stats and resolve/refund dialog, is gone.
  - The sidebar entry "Disputes & Refunds", the command-palette entry and the Support tab label are all renamed to "Chargebacks". URLs, the `?tab=disputes` slug and the `next.config.ts` redirect are unchanged.
  - The `resolveDispute` and `deleteDispute` API helpers are removed.
- **Driver app:** in `app/driver/ride-detail.tsx`, the "Report an earnings issue" button (a confirm dialog, then `api.post('/disputes')`) becomes a secondary link, "Questions about this trip's earnings? Get help". It opens `/driver/help` (`SupportScreen`, which shows the `company_email` mailto chip). The `api.post` call and the now-unused `Alert` import are removed.
  - No i18n keys were added: this screen's copy is hard-coded English throughout (it has no `useTranslation`), so no `driver-app/i18n/*.json` file was touched.
- **Legal drafts:** in `docs/legal/terms-of-service.md` (§5, §8) and `docs/legal/cancellation-fee-policy.md` (preamble, fee-dispute paragraph, pre-publication note 3), the references to "Support through the app", the "receipt/dispute flow" and `create_dispute()` are replaced with "Questions about a charge? Email support@spinr.ca, or contact your card issuer." The 60-day window and all other text are unchanged.
  - In ToS §8, general ride complaints still say "contact Support first". Only "through the app" was dropped, and the charge sentence was added after it.

### What stays, on purpose (bank chargeback path, untouched)

The following files and data are not modified by this diff:
- **Stripe webhooks:** the `charge.dispute.*` handlers in `backend/routes/webhooks.py` and the `stripe_disputes` table.
- **Evidence tooling:** the evidence reminder loop (`core/lifespan.py` plus `utils/dispute_evidence_reminder.py`), `utils/dispute_evidence_pack.py` and `utils/dispute_evidence_pdf.py`.
- **Admin evidence routes:** `routes/admin/dispute_evidence_submission.py` (`POST /admin/disputes/{id}/submit-evidence`) and `routes/admin/dispute_pack_download.py`.
- **Chargebacks list:** `GET /admin/disputes/chargebacks`. It is still registered before `/disputes/{dispute_id}`, and a new test pins that ordering.
- **Dashboard chargebacks UI:** `ChargebacksTab` and its test (`chargebacks-tab.test.tsx`), unchanged.
- **Ride payment state:** `rides.payment_status` `disputed` / `dispute_lost` and the ledger entries.

Why keep them: a bank chargeback is the owner's chosen channel for a user who disagrees with a charge. Stripe opens it whether or not Spinr has an in-app flow, and Spinr still has to see its evidence deadline and respond.

Also kept, per the brief:
- the `disputes` table, its migrations and RLS (no migrations in this PR);
- the `admin_dispute_refunds_enabled` column (stays false);
- the `disputes` admin permission module;
- the Zoho `_LINKED_TABLES` entry for `disputes`;
- `services/admin_money_caps.py`, which is still used by the admin wallet credit/debit caps.

## 4. Risk & impact on existing functionality

Blast radius is cross-surface (backend, driver-app, admin-dashboard) but narrow. Each disabled endpoint had one real caller, and production has 0 `disputes` rows.

- **`POST /disputes` callers:** grep across `rider-app/`, `driver-app/`, `shared/` and `frontend/` finds only `driver-app/app/driver/ride-detail.tsx`. rider-app never called it.
- **`PUT /admin/disputes/{id}/resolve` callers:** only the admin page's resolve dialog, removed here.
- **`POST` / `PUT` / `DELETE /admin/disputes[/{id}]` callers:** the `createDispute`, `updateDispute` and `deleteDispute` helpers had no UI caller. `deleteDispute` is removed. `createDispute` and `updateDispute` remain as unused exports for the cleanup PR, and now get 410.
- **Readers of `disputes` rows:**
  - admin GET list/stats/detail: unchanged;
  - `admin_dispute_stats_rollup` RPC: unchanged;
  - Zoho status sync via `_LINKED_TABLES`: no new rows, so no effect;
  - `admin_money_caps`: counts `dispute_resolved` audit rows, and no new ones can be written. The wallet cap path is unaffected.
- **Chargeback routing:** `/disputes/chargebacks` vs `/disputes/{dispute_id}`. Covered by the existing `test_registered_before_dispute_id_path_param` plus the new `test_chargebacks_path_not_captured_by_dispute_id_write_routes`.
- **Other systems:** no background loop, ride-state-machine, wallet-delta or Stripe-charge code is touched. The only money path touched is removed: the admin-issued Stripe refund via dispute resolve. Refunds can still be issued in the Stripe Dashboard.
- **Removed e2e tests:** `ride-management.spec.ts` lost its two dispute-refund tests. That flow no longer exists, so there is nothing left to cover.

## 5. User-experience effect

- **Drivers on a new build:** trip detail shows "Questions about this trip's earnings? Get help", which opens the Help screen. That screen already shows a support-email chip (`company_email` = support@spinr.ca in prod, from `GET /company-info`). Visible on the next app build only; no mid-session change.
- **Drivers on an old build:** "Report an earnings issue" then "Send" now gets a 410. Verified by reading the code:
  - the button's `api.post('/disputes', …).then(onOk, onErr)` passes `() => Alert.alert('Could not send', 'Try again from Help.')` as the rejection handler;
  - `shared/api/client.ts` rejects on any non-2xx through `handleApiError`. A 410 matches neither the 401 refresh path, the 503 retry path nor the 426 force-upgrade check (`checkForceUpgrade` only fires on 426), so it falls through to the generic throw;
  - the old build therefore shows "Could not send — Try again from Help.", which sends the driver to the same Help screen. No crash, no false success.
- **Riders:** no in-app change. No rider-app screen ever called `POST /disputes`.
- **Internal admins:**
  - the sidebar and Support tab now say "Chargebacks" instead of "Disputes & Refunds" / "Disputes";
  - `/dashboard/disputes` shows only the chargebacks table;
  - the "Rider Disputes" tab and the resolve/refund dialog are gone.
  - This takes effect on the next dashboard deploy and is visible to an admin who has the page open after a reload.
- **Legal copy** (repo drafts only; see ESCALATION below): charge questions now point to support@spinr.ca or the card issuer.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| backend/routes/disputes.py | `POST /disputes` and admin `PUT /disputes/{id}/resolve` now 410 stubs; original handlers kept, not routed | Disable the in-app create and the refund-issuing resolve |
| backend/routes/admin/support.py | `POST /disputes` and `PUT /disputes/{id}` now 410; `DELETE /disputes/{id}` removed | Make dispute records read-only; hard delete broke 7-year retention |
| backend/tests/test_disputes_disabled.py | New: 410 plus no side effects (DB/Zoho/push/settings), module gate, GET still served | Pin the disabled behaviour |
| backend/tests/test_admin_support_routes.py | Create/update/resolve now assert 410; delete asserts 405 and no delete | Tests asserted the old write behaviour |
| backend/tests/test_admin_chargebacks_route.py | New test: chargebacks path still reaches `stripe_disputes`; PUT/DELETE on it never hit a dispute handler | Chargebacks routing must survive the change |
| backend/tests/test_p3_addresses_favorites_safety_disputes.py | 5 old create tests replaced by one parametrized 410/no-DB test | Tests asserted the old create flow |
| driver-app/app/driver/ride-detail.tsx | "Report an earnings issue" (POST /disputes) replaced by a "Get help" link to `/driver/help`; `Alert` import removed | Only client caller of `POST /disputes` |
| driver-app/__tests__/screens/ride-detail-route.test.tsx | New contract test: no `/disputes`, no `api.post`, Help route wired | Regression pin |
| admin-dashboard/src/app/dashboard/disputes/page.tsx | Renders only `ChargebacksTab` | Hide the Rider Disputes tab and resolve dialog |
| admin-dashboard/src/app/dashboard/disputes/page.resolve.test.tsx | Deleted | Asserted the removed resolve dialog |
| admin-dashboard/src/app/dashboard/disputes/page.test.tsx | New: chargebacks render, no tabs, no Resolve, no dispute API calls | Pin the new page |
| admin-dashboard/src/components/sidebar.tsx | Label "Disputes & Refunds" renamed to "Chargebacks" (href unchanged) | Label matches content |
| admin-dashboard/src/lib/command-palette-routes.ts | Label renamed to "Support & Issues → Chargebacks" (href unchanged) | Same |
| admin-dashboard/src/app/dashboard/support/page.tsx | Tab label "Disputes" renamed to "Chargebacks" (slug unchanged) | Same |
| admin-dashboard/src/lib/api.ts | Drop `resolveDispute` and `deleteDispute` re-exports | Helpers removed |
| admin-dashboard/src/lib/api/analytics-payouts.ts | `resolveDispute` removed | Endpoint is 410; no caller |
| admin-dashboard/src/lib/api/rides.ts | `deleteDispute` removed; header comment updated | Endpoint removed; no caller |
| admin-dashboard/src/lib/api/safety-disputes.ts | Header comment only | Stale pointer to `deleteDispute` |
| admin-dashboard/e2e/disputes.spec.ts | Rewritten for the chargebacks-only page | Old spec drove the removed dialog |
| admin-dashboard/e2e/ride-management.spec.ts | Two dispute-refund tests removed | Flow removed |
| admin-dashboard/e2e/support.spec.ts | Tab name "Disputes" renamed to "Chargebacks" | Label rename |
| admin-dashboard/e2e/crawl-audit.spec.ts | `/dashboard/disputes` mock now seeds chargeback rows | Page reads the chargebacks endpoint now |
| docs/legal/terms-of-service.md | §5 and §8 charge-question sentence | Owner decision |
| docs/legal/cancellation-fee-policy.md | Preamble, fee paragraph, note 3 | Owner decision |
| docs/change-log/2026-09-25-disable-in-app-disputes.md | This file | CLAUDE.md gate |

## 7. Before / after

```python
# Before (backend/routes/disputes.py)
@api_router.post("")
async def create_dispute(req: CreateDisputeRequest, current_user: dict = Depends(get_current_user)):
    ride = await db_supabase.get_ride(req.ride_id)
    ...  # insert row, push, Zoho ticket

# After
@api_router.post("")
async def create_dispute_disabled():
    raise HTTPException(status_code=410, detail={"code": "IN_APP_DISPUTES_DISABLED", "message": IN_APP_DISPUTES_DISABLED_DETAIL})

async def create_dispute(...):  # not routed; removed in the cleanup PR
```

```python
# Before (backend/routes/admin/support.py)
@router.delete("/disputes/{dispute_id}")
async def admin_delete_dispute(dispute_id: str, admin: dict = Depends(get_admin_user)):
    await db_supabase.delete_many("disputes", {"id": dispute_id})

# After: route removed (DELETE -> 405)
```

```tsx
// Before (driver-app/app/driver/ride-detail.tsx)
onPress={() => Alert.alert('Report an earnings issue', ..., [{ text: 'Send', onPress: () => api.post('/disputes', {...}) }])}

// After
onPress={() => router.push('/driver/help' as any)}
```

Concrete scenario (dry-run of the removed money path): an admin opens an open dispute, picks "Approve Full Refund" and submits.
- **Before:** `Refund.create` ran on the ride's PaymentIntent when `admin_dispute_refunds_enabled` was on.
- **After:** the dialog no longer exists. A direct API call gets 410 with no DB read and no Stripe call. `test_admin_resolve_is_410_and_moves_no_money` pins this with mocked `get_rows`, `get_ride`, `update_one`, `get_app_settings` and push.

## 8. Rollback plan

- **No data rollback is needed.** This diff applies nothing to live data: no migration, no Stripe action, no row changes, and production has 0 `disputes` rows.
- **No feature flag.** A flag would keep a refund-issuing path deployable, which is exactly what the owner decided against. The flow has never been used in production (0 rows), so there is no live usage to protect with a staged rollout.
- **Code rollback:** revert the branch's commits and redeploy backend, dashboard and a driver build. The original backend handlers are still in the modules, so re-enabling the backend is just restoring the route decorators.
- **Mobile:** a driver build already shipped with the Help link cannot be reverted without a new build. It would simply keep sending drivers to Help, which is safe.

## 9. Verification performed

- **Backend:** `cd backend && python -m pytest -q -p no:cacheprovider --no-cov --ignore=tests/rls -k "dispute or chargeback or support or webhook or evidence or money_cap"` gives **795 passed, 1 skipped, 0 failed**.
  - The first run had 5 failures in `test_p3_addresses_favorites_safety_disputes.py`, whose HTTP tests asserted the old create flow. They were fixed in their own commit, then the suite was re-run.
  - Route-inventory and auth tests (`test_admin_routes_auth.py`, `test_admin_auth_coverage_gap.py`, `test_appcheck_*_exempt.py`, `test_documents.py`, `test_worker_app.py`, `test_admin_staff_mfa_reset.py`) also passed: 156 passed.
  - `ruff check` and `ruff format --check` pass on every changed `.py` file.
- **Admin dashboard:**
  - `npm ci`, then `npx tsc --noEmit`: clean.
  - `npx vitest run` (full suite): 84 files / 757 tests passed.
  - eslint on the changed files: 0 errors, and 5 warnings, all pre-existing on lines this diff doesn't touch (`chargebacks-tab.tsx`, `lib/api/client.ts`, and `sidebar.tsx` lines 278/325).
  - **`npm run build` (real Next.js production build): passed.** `/dashboard/disputes` and `/dashboard/support` both compiled. The first attempt died with ENOSPC because the shared container disk filled; it passed after the npm cache was freed and the build re-run.
- **Driver app:** `yarn install --frozen-lockfile`, then `npx tsc --noEmit`: clean. `npx jest __tests__/screens/ride-detail-route.test.tsx`: 5/5 passed. eslint on `ride-detail.tsx`: 0 errors, and 45 warnings, the same count as `origin/main` (all pre-existing hard-coded padding warnings).
- **Blast-radius greps:**
  - `'/disputes` / `"/disputes` / `` `/disputes `` in `rider-app/`, `driver-app/`, `shared/` and `frontend/`;
  - `deleteDispute|resolveDispute|createDispute|updateDispute|getDisputes|getDisputeStats|disputeResolutionSchema` in `admin-dashboard/src` and `admin-dashboard/e2e`;
  - `"disputes"` across `backend/` (non-test);
  - `Disputes & Refunds|Rider Disputes` across `admin-dashboard`.
- **Visual baselines:** `/dashboard/disputes` and `/dashboard/support` are **not** among the 6 seeded pages in `admin-dashboard/e2e/visual-regression.spec.ts` (login, dashboard-home, dashboard-rides, dashboard-drivers, dashboard-monitoring, dashboard-settings). The sidebar is on seeded pages, but the "Chargebacks" child item lives under the collapsed "Support & Issues" group; see the NOT-verified list.

## 10. What was NOT verified

- **Playwright e2e was not run** (`disputes.spec.ts`, `ride-management.spec.ts`, `support.spec.ts`, `crawl-audit.spec.ts`): no Playwright browsers in this container. Those specs are type-checked only.
  - The crawl's a11y baseline for `/dashboard/disputes` is 0 violations. It previously crawled the rider-disputes table and now crawls `ChargebacksTab`, whose a11y was not measured here.
- **Sidebar on seeded visual baselines:** if the seeded screenshots render the "Support & Issues" group expanded, the label change "Disputes & Refunds" → "Chargebacks" would show as a visual diff. The group is expected to be collapsed on those pages, but that was reasoned, not checked. If `visual-regression-test` fails on it, a human must re-capture baselines via `update-visual-baselines.yml`.
- **driver-app and rider-app have no visual regression tooling.** The Help link was reasoned about from source, not screenshotted.
- **Not tested against live Supabase, a staging backend or a real device.** The old-build 410 behaviour was verified by reading the code, not on an installed old build.
- **ESCALATION: live legal text.** Only the repo drafts in `docs/legal/` changed. The live `legal_documents` DB rows need a **new version published by a human**. Whether this change is "material" and **requires re-consent** is a **legal decision**, not made here.
  - Also out of sync: `docs/legal/legal-text-publication-checklist.md`'s cancellation-fee row still cites `routes/disputes.py`'s missing time cutoff. It was not edited here (outside the two files in scope).
- **Out of scope (owner not asked):** the in-app Contact form (`/tickets`, `SupportScreen`) does **not** create Zoho Desk tickets. Only emails to support@spinr.ca do.
- **Cleanup deferred to the follow-up PR:**
  - the unrouted handlers `create_dispute`, `admin_resolve_dispute`, `admin_create_dispute` and `admin_update_dispute`, plus their direct-call tests;
  - the unused `createDispute` / `updateDispute` exports and `lib/disputeResolutionSchema.ts` (plus its test).
- No `spinr-*` reviewer agent was run: this session had no Agent tool.

## 11. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed
- [x] No silent behavior change to an already-shipped flow without the UX field filled in
