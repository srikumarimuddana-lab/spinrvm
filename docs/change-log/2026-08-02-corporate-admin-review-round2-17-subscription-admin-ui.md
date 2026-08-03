# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-02 |
| Author | Claude Code |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin, corporate |
| PR / commit link | branch `claude/spinrvm-schedule-ride-review-2jsank` |
| Related issue or gap ID | Corporate + admin portal review, round 2 — "no pricing/fee mechanism exists for the corporate product" (business decision: flat SaaS subscription, full Stripe automation) — admin UI slice (final) |

## 1. Issue / gap identified

Final slice of the corporate subscription-billing build: the round2-15
route exists but no admin dashboard screen can drive it — an admin would
have to call the API directly.

## 2. Root cause

Never built — see round2-12 for full background.

## 3. Fix / remediation

- New `admin-dashboard/src/app/dashboard/corporate-accounts/[id]/subscription/page.tsx`,
  following the exact structural template of the sibling `[id]/policy/page.tsx`
  sub-page (own route, back-link to the company detail page, inline
  loading/error state — not a new pattern). Shows the company's current
  subscription (plan, price, status badge, period end, cancel-at-period-end
  notice) with a cancel control when one exists, or a plan picker + assign
  button when it doesn't; a history table below lists past subscriptions.
  A failed assign surfaces a hint that billing may not be enabled yet
  (the round2-15 flag), rather than a bare generic error.
- New "Subscription" button on the company detail page
  (`[id]/page.tsx`), placed next to the existing Members/Policy buttons —
  same `Link` + `Button variant="outline"` pattern, new `CreditCard` icon
  import (only new import added to that file).
- New API client surface in `lib/api/corporate.ts`: `CorporateSubscriptionPlan`,
  `CorporateSubscription`, `CompanySubscriptionResponse` types + `getSubscriptionPlans`/
  `getCompanySubscription`/`assignCompanySubscription`/`cancelCompanySubscription`,
  re-exported from `lib/api.ts` alongside (not replacing) the existing wallet
  exports.

## 4. Risk & impact on existing functionality

- **Blast radius: one new page file, four new named exports appended to
  `corporate.ts`/`api.ts`, and a two-line addition (one import, one
  `Link`/`Button` block) to the existing company detail page.** No
  existing function, type, route, or JSX block in either touched file was
  modified — confirmed by diff: the only change to `[id]/page.tsx` is the
  new `CreditCard` import and the new `Link` block inserted between the
  existing Policy and Suspend buttons.
- Grepped `corporate.ts`/`api.ts` for the four new export names: no
  collision with any existing export.
- Bracket-balance check (no TS/JS toolchain run, per this round's
  instruction) on all four touched files — clean.
- The page depends entirely on the round2-15 route, which ships dark
  (assign blocked until the billing flag is turned on) — so this UI is
  inert (list/cancel work, assign surfaces the "not enabled yet" message)
  until that flag is flipped in staging, consistent with the rest of this
  feature's staged rollout.

## 5. User-experience effect

**Internal admin-facing only.** An admin viewing a corporate account now
sees a "Subscription" button leading to a dedicated screen. Until the
`corporate_subscription_billing_enabled` flag is turned on, attempting to
assign a plan surfaces a clear "not enabled yet" message rather than a
silent failure or a broken button. No rider, driver, or corporate-admin
(company-side) UI is touched — this screen is Spinr-internal-admin only.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/corporate-accounts/[id]/subscription/page.tsx` | New page: view/assign/cancel a company's subscription | Admin UI for the round2-15 route |
| `admin-dashboard/src/app/dashboard/corporate-accounts/[id]/page.tsx` | New "Subscription" button + `CreditCard` import | Link to the new sub-page |
| `admin-dashboard/src/lib/api/corporate.ts` | New types + 4 API client functions | Typed client for the round2-15 route |
| `admin-dashboard/src/lib/api.ts` | Re-exported the 4 new functions + 4 new types | Match the barrel-export convention already used for every other corporate API surface |

## 7. Rollback plan

`git revert` the commit. Purely additive UI — no data written, no
existing screen's behavior changed. The underlying route/flag (round2-15)
remain the actual kill switch regardless of whether this UI is reverted.

## 8. Verification performed

- [x] Bracket-balance check on all four touched files (no TS/JS toolchain
      run, per this round's instruction) — all balanced.
- [x] Confirmed every UI primitive used (`Table`, `Switch`, `Label`,
      `Select`, `Badge`, `Card`) is already imported and used elsewhere in
      this same directory (`policy/page.tsx`, `faqs.tsx`) — no new
      component dependency introduced.
- [x] Confirmed the four new API client functions' request shapes
      (method, body field names `plan_id`/`at_period_end`) match the
      round2-15 route's Pydantic request models exactly.
- [x] Manually traced that `[id]/page.tsx`'s only diff is the new import
      + new `Link` block — no existing line altered.
- [x] Did **not** run `npm run build`, `tsc --noEmit`, `eslint`, or start
      the dev server — per this round's explicit "don't run tests/CI
      until everything is developed" instruction. This is the single
      highest-risk unverified item in this entire feature build for the
      admin-dashboard surface specifically (see CLAUDE.md: "a passing dev
      server or `tsc --noEmit` alone is not equivalent" to a real
      production build) — deferred to the end-of-round pass, where a real
      `npm run build` should be run for `admin-dashboard`, not just a
      syntax check.

## 9. Sign-off

- [x] Rollback plan is concrete — `git revert`, no data involved
- [x] Blast radius is stated, not assumed — confirmed via diff inspection
- [x] No silent behavior change to a working flow — the company detail
      page's existing buttons/handlers are byte-for-byte unchanged; only
      new JSX was inserted

## What was NOT verified

Did not run `npm run build` (or any TypeScript/lint toolchain) for
`admin-dashboard` — per this round's explicit instruction, this is
deferred to the single end-of-round pass, and per CLAUDE.md's own
pre-merge gate, a real production build (not just `tsc --noEmit`) is
required for any `admin-dashboard` change before merge; that has not
happened yet for this file. Did not run the dev server or click through
this screen in a browser — no visual/functional confirmation exists yet
that the plan picker, cancel toggle, or history table render correctly
against real API responses. This closes out the corporate subscription-
billing feature build (round2-12 through round2-17, 6 commits); the
end-of-round verification pass should treat `admin-dashboard`'s
production build as the first thing to check given this file was never
compiled in this session.
