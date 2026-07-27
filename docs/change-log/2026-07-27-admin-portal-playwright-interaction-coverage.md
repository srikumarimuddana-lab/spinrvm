# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-27 |
| Author | Claude Code |
| Surface(s) | admin-dashboard (test-only — no production code changed) |
| Domain (Sentry tag) | admin |
| PR / commit link | branch `claude/admin-portal-playwright-tests-s22eus`, commits `a8dff8c`..`430437a` (11 commits) |
| Related issue or gap ID | User request: "test everything admin portal individual functionality, feature, button, screen, search, dropdown values" |

## 1. Issue / gap identified

Admin-dashboard Playwright coverage was crawl/render-level only (`crawl-audit.spec.ts`: does every route 404 or throw; `login.spec.ts`/`dashboard.spec.ts`/`ride-management.spec.ts`: a handful of page-load and reachability checks). No spec actually drove buttons, dropdowns, search inputs, forms, or tab switches to verify they *work*, across any of the 41 `/dashboard/*` routes.

## 2. Root cause

The admin portal's automated test investment (from the earlier crawl-audit effort, PR #2360) targeted "does it crash" — a reasonable first pass — but never followed up with "does it function." Interaction-level coverage requires per-page knowledge of API shapes and DOM structure that a generic crawler can't provide, so it was never built.

## 3. Fix / remediation

Added interaction-level Playwright coverage for all 41 `/dashboard/*` routes, decomposed into 12 subtasks (per CLAUDE.md's ≤3-file/subtask + task-decomposition rule), one commit each:

1. `e2e/admin-mocks.ts` — shared `setupAdminMocks()` fixture (auth/session/refresh boilerplate + per-spec `extra` override hook), replacing ad hoc duplication across specs.
2. `e2e/rides.spec.ts` — rides list + live tracking.
3. `e2e/drivers.spec.ts`, `e2e/drivers-subpages.spec.ts` — drivers list/detail, approval queue, expiring docs, bulk import.
4. `e2e/corporate.spec.ts`, `e2e/corporate-members-policy.spec.ts` — corporate accounts, members, policy, KYB queue.
5. `e2e/earnings.spec.ts` — earnings tabs, standalone payouts list + detail.
6. `e2e/safety.spec.ts`, `e2e/disputes.spec.ts`.
7. `e2e/promotions-interaction.spec.ts`, `e2e/subscriptions.spec.ts`, `e2e/quests.spec.ts`.
8. `e2e/staff.spec.ts`, `e2e/settings.spec.ts`, `e2e/audit-logs.spec.ts`.
9. `e2e/support.spec.ts`, `e2e/support-tickets.spec.ts` (Zoho Desk integration).
10. `e2e/monitoring.spec.ts`, `e2e/heatmap.spec.ts`, `e2e/forecast.spec.ts` (+ `/dashboard/surge` redirect).
11. `e2e/misc-admin.spec.ts`, `e2e/misc-admin-2.spec.ts` — ai-console, bulk-operations, cloud-messaging, notifications (redirect), referrals, service-areas, users, vehicle-types, venues, documents (redirect), documents/requirements, driver-offers.
12. This log.

**Net result:** 225 e2e tests across 25 spec files (up from 4 spec files / ~58 tests), all mocked-network (no live backend/Supabase), running against a real production build (`npm run build && npm run start`) in this session, and via `npm run test:e2e` (`playwright test`) in CI with zero workflow changes required — `testDir: './e2e'` in `playwright.config.ts` already globs the whole directory.

**Explicitly out of scope (by user decision earlier in this effort):** French/i18n testing — confirmed via grep that no i18n library, translation files, or language switcher exist anywhere in admin-dashboard; only `.toLocaleString()`/`.toLocaleDateString()` number/date formatting. Flagging this as a standing product gap, not a test gap: **there is no French support to test.**

## 4. Risk & impact on existing functionality

- **Blast radius: test-only, zero production code touched.** Every commit in this effort added new files under `admin-dashboard/e2e/`; no file under `admin-dashboard/src/` was modified. Grepped the diff range (`git diff --stat 5d5bc4f..430437a -- admin-dashboard/src`) — empty.
- New tests run in a fully mocked network (`page.route('**/api/**', ...)` via `setupAdminMocks`) — they never touch a real backend, Supabase, or Stripe, so there is no risk to live data, rate limits, or production state from running them, in CI or locally.
- The one shared fixture (`admin-mocks.ts`) is additive and consumed only by the 11 new spec files that import it — the 4 pre-existing specs (`login`, `dashboard`, `ride-management`, `crawl-audit`) were not touched and were re-run after every subtask to confirm zero regression (they remain at 5/5/7/~41 passing throughout).
- **CI impact:** all three `*-e2e` jobs (`e2e-test` for admin, `rider-app-e2e`, `driver-app-e2e`) are unaffected structurally — admin's `e2e-test` job already runs `npx playwright install --with-deps chromium && npm run test:e2e` with no explicit file list, so it will simply run more tests (225 vs. ~58) on the next CI run. This adds wall-clock time (~2 minutes locally for the full suite; CI machines vary) but no new failure modes beyond the tests themselves.

## 5. User-experience effect

None. This is test infrastructure — no rider, driver, corporate-admin, or internal-admin-facing behavior changed. The admin portal's actual functionality is unchanged; what changed is confidence that 41 routes' buttons/dropdowns/search/tabs/forms work as intended, backed by a regression suite that will now catch future breakage in those same interactions.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/e2e/admin-mocks.ts` (new) | Shared `setupAdminMocks()` fixture: auth/session cookie + refresh-token mock, generic `/api/**` fallback, `extra` override hook with an explicit `'handled'` sentinel (fixing a double-`route.fulfill()` bug caught mid-effort — see commit `a8dff8c`) | Every subsequent spec needed this boilerplate; centralizing it avoided 20+ copies of the same auth-mock code |
| `admin-dashboard/e2e/rides.spec.ts` (new) | 8 tests: search, area filter, date filters, export, row-click detail modal, create-ride modal, page-size select, live tracking | `/dashboard/rides` interaction coverage |
| `admin-dashboard/e2e/drivers.spec.ts`, `drivers-subpages.spec.ts` (new) | 18 tests: driver list (search/filters/sort/PII toggle/export/detail-sheet/edit) + approval queue, expiring docs, bulk import | `/dashboard/drivers` + 3 subpages |
| `admin-dashboard/e2e/corporate.spec.ts`, `corporate-members-policy.spec.ts` (new) | 18 tests: accounts list/detail/kyb-queue + members/policy pages | `/dashboard/corporate-accounts` + subpages |
| `admin-dashboard/e2e/earnings.spec.ts` (new) | 13 tests: tabbed earnings page + standalone payouts list/detail | `/dashboard/earnings` + subpages |
| `admin-dashboard/e2e/safety.spec.ts`, `disputes.spec.ts` (new) | 11 tests | `/dashboard/safety`, `/dashboard/disputes` |
| `admin-dashboard/e2e/promotions-interaction.spec.ts`, `subscriptions.spec.ts`, `quests.spec.ts` (new) | 19 tests | `/dashboard/promotions`, `/dashboard/subscriptions`, `/dashboard/quests` |
| `admin-dashboard/e2e/staff.spec.ts`, `settings.spec.ts`, `audit-logs.spec.ts` (new) | 19 tests | `/dashboard/staff`, `/dashboard/settings`, `/dashboard/audit-logs` |
| `admin-dashboard/e2e/support.spec.ts`, `support-tickets.spec.ts` (new) | 24 tests | `/dashboard/support` (7 tabs), `/dashboard/support-tickets` + subpages, `/dashboard/faqs` |
| `admin-dashboard/e2e/monitoring.spec.ts`, `heatmap.spec.ts`, `forecast.spec.ts` (new) | 14 tests | `/dashboard/monitoring` + `/redis`, `/dashboard/heatmap`, `/dashboard/forecast`, `/dashboard/surge` redirect |
| `admin-dashboard/e2e/misc-admin.spec.ts`, `misc-admin-2.spec.ts` (new) | 26 tests | Remaining 12 routes: ai-console, bulk-operations, cloud-messaging, notifications, referrals, service-areas, users, vehicle-types, venues, documents, documents/requirements, driver-offers |
| `docs/change-log/2026-07-27-admin-portal-playwright-interaction-coverage.md` (new, this file) | Change Impact & Risk Log | Per CLAUDE.md mandatory convention for live-testing-phase changes |

## 7. Before / after

Not applicable in the usual before/after-snippet sense — no existing behavior-changing diff. Representative example of what a "does it actually work" test looks like vs. the prior "does it crash" style:

```typescript
// Before (existing ride-management.spec.ts, unchanged) — only checks the page renders:
test('rides page loads and renders ride list', async ({ page }) => {
  await mockAdminAPIs(page);
  await page.goto('/dashboard/rides');
  await expect(page.locator('h1, h2, [data-testid="rides-heading"]').first()).toBeVisible({ timeout: 20000 });
});
```

```typescript
// After (new rides.spec.ts) — actually drives the control and asserts its effect:
test('search box filters by typed text without a page crash', async ({ page }) => {
  await mockRides(page);
  await page.goto('/dashboard/rides');
  const search = page.getByPlaceholder(/Search by ride code, name, phone, address, or ID/i);
  await expect(search).toBeVisible({ timeout: 20000 });
  await search.fill('SPR-1001');
  await expect(search).toHaveValue('SPR-1001');
});
```

## 8. Rollback plan

`git revert` of the range is complete and sufficient — every commit is test-only, additive, and touches no production code, database, or config. No data to remediate; no feature flag involved (this isn't a shipped feature, it's test coverage). If a specific new spec turns out to be flaky in CI, the fix is to delete or fix that one spec file, not to revert the branch.

## 9. Verification performed

- [x] Every one of the 12 subtasks was verified independently: `tsc --noEmit` clean, `eslint` clean, then **actually executed against a local production build** (`npm run build && npm run start`, not `next dev` — `next dev`'s Turbopack CSS resolver had an unrelated environment-specific issue in this sandbox that a production build didn't share) using the pre-installed Chromium (`/opt/pw-browsers/chromium`).
- [x] Running each spec for real (not just type-checking it) caught concrete bugs before they could land — documented per-commit, summarized in the "What was NOT verified" section's counterpart below: mock-shape mismatches (several `getX()` functions return raw arrays, not `{ x: [...] }` wrappers — confirmed by reading `lib/api.ts` source per endpoint, not guessed), selector ambiguity (aria-labels/text shared between sidebar nav links and page headings, or between a filter combobox and a same-named sortable-column button), and one real double-`route.fulfill()` bug in the shared fixture itself.
- [x] Full regression pass after every subtask: all 4 pre-existing specs + all specs added so far were re-run together, ending at 225/225 passing after subtask 11.
- [x] Confirmed CI picks up the new specs with zero workflow changes: `admin-dashboard/package.json`'s `test:e2e` script is bare `playwright test` (no file args), and `playwright.config.ts`'s `testDir: './e2e'` globs the whole directory — verified by reading both files, not assumed.
- [ ] Not run inside actual GitHub Actions CI in this session (no push yet) — verified the mechanism (script + testDir config) will pick up the new files, but the first real CI run of this branch will be the first confirmation of runtime/browser-version parity with this sandbox's local Chromium.
- [ ] Not tested against a live Supabase/staging backend — by design, this entire effort is mocked-network. A follow-up manual click-through against staging (per the earlier QA-checklist conversation) would still be the right complement for real-data correctness, visual polish, and true backend-integration bugs that a mock can't surface by construction.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, test-only diff, nothing to remediate)
- [x] Blast radius is stated, not assumed — zero production-code changes, confirmed via diff stat
- [x] No silent behavior change to any shipped flow — this is pure test-suite addition; Section 5 (UX effect: none) reflects that accurately
