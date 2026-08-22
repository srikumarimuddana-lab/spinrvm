# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-21 |
| Author | Claude Code (session, vikas@ngitservices.com) |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | (see PR) |
| Related issue or gap ID | #2816 Stage 1, Batch 7 (sub-batch 11) — see `docs/change-log/2026-08-21-admin-color-token-migration-plan.md` |

## 1. Issue / gap identified

Continuing the #2816 hardcoded-Tailwind-color-token migration. This
sub-batch covers `sentry-logs/page.tsx` (3 broken lines), `rides/live/
[id]/page.tsx` (3), `company-signup/page.tsx` (3), `corporate-accounts/
[id]/subscription/page.tsx` (3), and `company-portal/[id]/activity/
page.tsx` (3).

## 2. Root cause / findings

- **`sentry-logs/page.tsx`**: two warning `Card` borders (partial-load
  and not-configured setup panels) had no `dark:` at all, while their
  own text siblings already used `dark:text-yellow-400` — converted the
  borders to the `warning` token, matching the identical fix already
  applied to `monitoring/redis/page.tsx`'s analogous alert cards in an
  earlier sub-batch. An empty-state "all clear" checkmark → `success`.
- **`rides/live/[id]/page.tsx`**: driver (`Car`) and rider (`User`) role
  icons, each already inside a `dark:`-aware avatar-badge container,
  given matching `dark:` pairing — categorical role differentiation
  (like Rider/Driver badges elsewhere), not a state, so tokenized only
  via the missing `dark:` variant, not converted to a semantic token. A
  pickup-location marker dot (emerald) left untouched — this file's
  route markers use `emerald`/`bg-primary` rather than the blue/red
  brand-pin convention seen elsewhere, but remain a decorative route
  indicator either way.
- **`company-signup/page.tsx`** (public signup form): 2 required-field
  asterisk markers → `destructive`; a signup-success confirmation
  checkmark → `success`.
- **`corporate-accounts/[id]/subscription/page.tsx`**: `STATUS_BADGE`
  (active/past_due/cancelled) — all 3 states map cleanly →
  `success`/`warning`/`muted`.
- **`company-portal/[id]/activity/page.tsx`** (corporate-customer-
  facing): `POLICY_RESULT_COLORS` (pass/override/fail) — `pass` →
  `success`, `fail` → `destructive`; `override` (a policy check that
  failed but was manually approved anyway) documented with
  `eslint-disable-next-line` — it must stay visually distinct from both
  a clean pass and an unresolved fail.

## 3. Fix / remediation

10 real semantic-token fixes, 2 dark: pairing additions (driver/rider
role icons), 1 documented suppression (`override` in the corporate-
customer-facing activity log). Remainder deliberately left as
decorative per the established per-line rule.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to these 5 files.** No shared component,
  hook, or utility was touched. Each file's status/result map is
  locally defined.
- All converted lines are plain text, icons inside already-dark-aware
  containers, or tinted (non-solid-fill) badges — none were part of the
  excluded solid-fill white-text button/badge class.
- Repo-wide lint warning count stayed well under the `--max-warnings`
  ratchet (1352 vs. the 1751 ceiling).

## 5. User-experience effect

**Mixed**: `sentry-logs/page.tsx`, `rides/live/[id]/page.tsx`, and
`corporate-accounts/[id]/subscription/page.tsx` are internal admin
only. `company-signup/page.tsx` is the **public** corporate-signup form
(a prospective customer filling it out) — required-field markers and
the success confirmation get a shade shift only, no copy/validation-
logic change. `company-portal/[id]/activity/page.tsx` is corporate-
customer-facing (a business user reviewing their own company's policy-
check activity log) — same shade-shift-only effect. No icon, label, or
layout change anywhere; no change to which subscriptions/activity rows
are shown or filtered.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/sentry-logs/page.tsx` | 2 alert-card borders → warning; empty-state icon → success | #2816 Batch 7 |
| `admin-dashboard/src/app/dashboard/rides/live/[id]/page.tsx` | Driver/rider role icons given dark: pairing | #2816 Batch 7 |
| `admin-dashboard/src/app/company-signup/page.tsx` | 2 required-field markers → destructive; success icon → success | #2816 Batch 7 |
| `admin-dashboard/src/app/dashboard/corporate-accounts/[id]/subscription/page.tsx` | `STATUS_BADGE` (3-state, all converted) → tokens | #2816 Batch 7 |
| `admin-dashboard/src/app/company-portal/[id]/activity/page.tsx` | `POLICY_RESULT_COLORS` (pass/fail converted, override documented) → tokens | #2816 Batch 7 |

## 7. Before / after

```tsx
// corporate-accounts/[id]/subscription/page.tsx — STATUS_BADGE (before)
const STATUS_BADGE: Record<string, string> = {
    active: "bg-emerald-100 text-emerald-800",
    past_due: "bg-amber-100 text-amber-800",
    cancelled: "bg-slate-100 text-slate-700",
};

// after
const STATUS_BADGE: Record<string, string> = {
    active: "bg-success/15 text-success",
    past_due: "bg-warning/15 text-warning",
    cancelled: "bg-muted text-muted-foreground",
};
```

## 8. Rollback plan

`git-revert-safe` — 5 files, all `className` string literals and one
documentation comment. No data/API/schema change, no shared-component
change.

## 9. Verification performed

- [x] `npx eslint` on all 5 touched files — 0 errors, 21 warnings (all
  pre-existing, mostly a `react-hooks/set-state-in-effect` warning).
- [x] `npx tsc --noEmit` — clean, 0 errors.
- [x] `npx vitest run` — 339/339 passed.
- [x] `npm run lint` (the exact CI command) — exit 0, 1352 total
  warnings (under the 1751 ratchet).
- [x] `npm run build` (real production build) — succeeded.
- [ ] Not manually click-tested/screenshotted — same standing sandbox
  limitation as every prior change-log this session; visual-regression
  baseline still not seeded. Notable: `company-signup/page.tsx` is
  fully public and `company-portal/[id]/activity/page.tsx` is
  corporate-customer-facing — both outside internal-admin-only scope.

## 10. Sign-off

- [x] Rollback plan is concrete and testable.
- [x] Blast radius is stated per file, not assumed.
- [x] No silent behavior change — every conversion is a color-only
  visual change on an already-semantically-meaningful element; all
  click handlers, filters, and validation logic are unchanged.
- [x] Public and customer-facing surfaces in this sub-batch are called
  out explicitly in the UX-effect section rather than lumped in with
  internal-only changes.
