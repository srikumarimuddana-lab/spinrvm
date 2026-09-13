# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-13 |
| Author | Claude Code (spinr-competitive-positioning-15juag session) |
| Surface(s) | backend, admin-dashboard |
| Domain (Sentry tag) | corporate |
| PR / commit link | (branch `claude/spinr-competitive-positioning-15juag`, commits `6b78312`, `00eab74`, `b36948e`) |
| Related issue or gap ID | ACTION_ITEMS.md G7 (corporate B2B GTM not yet actioned) |

## 1. Issue / gap identified

Corporate SaaS subscription billing (migration 281, `routes/corporate_subscriptions.py`) is
gated by a single global setting, `corporate_subscription_billing_enabled`. That flag is
all-or-nothing: the moment it is turned on, an admin can start a real recurring Stripe
subscription for **any** corporate account — there was no way to verify the flow against one
chosen company (e.g. Spinr's own internal account, for a mocked-Stripe dry run) without
exposing it to every corporate account in the system at the same time.

## 2. Root cause

The original design (corporate + admin portal review round 2) only needed a ship-dark/ship-lit
switch for "has this feature been verified in staging yet," and staging does not exist yet
(`docs/runbooks/staging-environment.md` is scaffolding only). No per-company scoping was ever
added because there was no pilot company to scope to at the time.

## 3. Fix / remediation

Added a second, narrower gate on top of the existing global setting: a per-company boolean
column (`corporate_accounts.subscription_billing_pilot_enabled`, migration 419, default
`false`). `assign_subscription` now requires **both** the global setting and this column to be
true before it will call Stripe. A new admin-only endpoint,
`POST /admin/corporate-accounts/{id}/subscription-pilot`, toggles the column per company, and
the existing `GET .../subscription` response now returns `pilot_enabled` so the admin-dashboard
company subscription page can show and toggle it. Cancellation remains ungated by either flag,
unchanged from before this change.

No real Stripe call was made anywhere in this change — every code path was verified with
`mock_supabase_client`-style mocks and a mocked Stripe client, per the user's explicit choice of
verification method (see below). The global setting is still `false` today; turning either flag
on for a real company is a separate, explicit decision not made in this change.

## 4. Risk & impact on existing functionality

- **Blast radius: single-surface (corporate billing only), isolated within it.** Grepped for
  every reader/writer of `corporate_accounts` (`repositories/corporate_repo.py` is the only
  place `.table("corporate_accounts")` is called) and every caller of `assign_subscription`
  (only `routes/corporate_subscriptions.py`'s `assign_company_subscription`) and of
  `get_company_subscription`'s response shape (only the one admin-dashboard page, which now
  reads the new `pilot_enabled` field it added). No other route, background loop, or table
  reads or writes the new column.
- **Existing behavior unchanged for every path except the one new gate.** Because the global
  setting is still `false` in every environment, `assign_subscription` was unreachable before
  this change and remains unreachable now — this change adds a second closed door behind an
  already-closed door. If the global flag is ever turned on in the future, the new column
  becomes the difference between "billing works for everyone" and "billing works only for
  companies an admin explicitly opted in" — strictly narrower than before, never wider.
- **No interaction with ride state machine, dispatch, or wallet/allowance deltas.** Subscription
  billing is a flat SaaS fee, deliberately separate from ride fares and from the
  `corporate_wallet_apply_delta` money path (per migration 281's own header).
- **No interaction with the 42 background loops** in `core/lifespan.py` — nothing here is
  polled or reconciled by a loop; Stripe webhook mirroring (unaffected by this change) is a
  separate route (`routes/webhooks.py`).

## 5. User-experience effect

- **Internal admin only.** No rider, driver, or corporate-admin-facing surface changed. The
  only visible change is a new toggle on an internal admin-dashboard page
  (`/dashboard/corporate-accounts/[id]/subscription`) that only an admin with the
  `corporate_accounts` module grant can reach.
- **Not visible mid-session to any rider/driver/corporate-portal user** — this surface has no
  end-user-facing counterpart.
- No copy/notification change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/419_corporate_subscription_billing_pilot.sql` | New column `corporate_accounts.subscription_billing_pilot_enabled BOOLEAN NOT NULL DEFAULT false` | Per-company half of the two-gate design |
| `backend/services/corporate_subscription_service.py` | `assign_subscription` now raises `CorporateSubscriptionError("company_not_in_pilot")` if the company's pilot column is falsy | Enforces the new gate at the one place `company` is already fetched |
| `backend/routes/corporate_subscriptions.py` | Added `company_not_in_pilot` → 403 mapping; new `POST .../subscription-pilot` endpoint (admin-only, audit-logged); `GET .../subscription` now also returns `pilot_enabled` | HTTP surface for the new gate and its toggle |
| `backend/tests/test_corporate_subscription_service.py` | `_company()` fixture now defaults `subscription_billing_pilot_enabled: True`; added `test_pilot_not_enabled_rejected` and `test_pilot_flag_missing_defaults_to_rejected` | Cover both the pass and fail-closed paths of the new gate |
| `backend/tests/test_corporate_subscriptions_route.py` | Added 403-mapping test, full `TestSubscriptionPilotToggle` class, and pilot-field assertions on the existing GET test | HTTP-level coverage for the new endpoint and response field |
| `admin-dashboard/src/lib/api/corporate.ts` | New `setCompanySubscriptionPilot()` call, `CompanySubscriptionPilotResponse` type, `pilot_enabled` added to `CompanySubscriptionResponse` | Frontend API surface |
| `admin-dashboard/src/lib/api.ts` | Re-exported the new function/type through the barrel file | Existing convention for this module |
| `admin-dashboard/src/app/dashboard/corporate-accounts/[id]/subscription/page.tsx` | New pilot toggle card; "Assign plan" disabled with an inline hint until the company is in the pilot | Lets an admin see and change pilot status per company |

## 7. Before / after

```
# Before (services/corporate_subscription_service.py::assign_subscription)
company = await db_supabase.get_corporate_account_by_id(company_id)
if not company:
    raise CorporateSubscriptionError("company_not_found")

plan = await db_supabase.get_corporate_subscription_plan(plan_id)
```

```
# After
company = await db_supabase.get_corporate_account_by_id(company_id)
if not company:
    raise CorporateSubscriptionError("company_not_found")
if not company.get("subscription_billing_pilot_enabled"):
    raise CorporateSubscriptionError("company_not_in_pilot")

plan = await db_supabase.get_corporate_subscription_plan(plan_id)
```

## 8. Rollback plan

- **No data has been touched.** Both the global setting and the new per-company column remain
  `false` everywhere; no Stripe subscription has been created by this change.
- **Code rollback**: a `git revert` of these three commits is a complete, safe rollback — there
  is no live data dependent on the new column or endpoint yet.
- **Schema rollback** (only needed if reverting past the migration too):
  `ALTER TABLE public.corporate_accounts DROP COLUMN IF EXISTS subscription_billing_pilot_enabled;`
  (stated in the migration's own header, per `backend/migrations/CLAUDE.md`).
- **If a real pilot company is opted in later and needs to be pulled out**: toggle the new
  `subscription-pilot` endpoint to `false` for that company — this does not cancel an
  already-created Stripe subscription (existing `cancel_subscription` flow handles that
  separately, unchanged by this PR).

## 9. Verification performed

- [x] Automated tests run: `pytest backend/tests/test_corporate_subscription_service.py
  backend/tests/test_corporate_subscriptions_route.py` — 30 passed. `ruff check` + `ruff format
  --check` clean on all touched backend files.
- [x] Frontend: `npx tsc --noEmit` clean repo-wide; **`npm run build` (real production build,
  not just the dev server) run and passed**, exit 0, `/dashboard/corporate-accounts/[id]/subscription`
  present in the route manifest; `eslint` on the three touched files shows zero errors (one
  pre-existing warning on an unrelated, unmodified line).
- [ ] Manual repro steps followed in staging — **not done; no staging environment exists yet**
  (`docs/runbooks/staging-environment.md`). Verification was mocked-only, by the user's explicit
  choice (see "What was NOT verified" below).
- [x] Blast-radius grep performed: every reader/writer of `corporate_accounts` and every caller
  of `assign_subscription` / `get_company_subscription` (see §4).
- [x] Reviewed against relevant CLAUDE.md conventions: migration conventions (passed
  `spinr-migration-reviewer`, verdict "SAFE TO APPLY", no blockers), dual-import pattern, RLS
  (verified migration 416's row-level policy already covers the new column; no new policy
  needed), audit-logging convention (`log_admin_action` on the new toggle endpoint, matching
  `routes/corporate_accounts.py`'s exact call shape).
- [x] Feature-flagged: yes — this entire change *is* a feature flag (a narrower one layered on
  the existing global flag). Both default to off.

## What was NOT verified

- **No real Stripe API call was made anywhere in this change** — `stripe.Subscription.create`
  is mocked in every test. The actual Stripe customer/subscription/webhook-mirroring pipeline
  for a real company remains unverified end-to-end (this was true before this change too; this
  change does not add or remove that gap).
- **No staging environment exists**, so nothing here was exercised against a real Stripe
  test-mode account or a database that isn't the mocked unit-test fixture set. The user
  explicitly chose "mocked dry run only" over standing up staging or self-charging Spinr's own
  account for this reason.
- **No visual-regression coverage**: `/dashboard/corporate-accounts/[id]/subscription` is not
  one of admin-dashboard's 6 CI-seeded visual-regression baseline pages (login, dashboard-home,
  dashboard-drivers, dashboard-monitoring, dashboard-settings, dashboard-rides), so the new
  toggle's visual placement was reasoned about, not screenshotted.
- **No admin-dashboard e2e (Playwright) test was added.** The only existing spec whose name
  suggested overlap (`e2e/subscriptions.spec.ts`) turned out to cover a different, unrelated
  page (`/dashboard/subscriptions`, Spinr Pass driver plans) — there was no existing scaffold
  for `/dashboard/corporate-accounts/[id]/subscription` to extend, and writing a new Playwright
  spec from scratch was judged out of proportion for this change; backend coverage is thorough
  instead.
- **Naming an actual pilot company and turning either flag on for it is a separate, later
  decision** — not made or actioned in this change.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (no live data touched; plain `git revert` suffices)
- [x] Blast radius is stated, not assumed (single-surface, isolated — see §4)
- [x] No silent behavior change to an already-shipped flow — the only reachable path
  (`assign_subscription`) was already fully blocked before this change (global flag off) and
  stays fully blocked after it
