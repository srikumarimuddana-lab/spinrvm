# Change Impact & Risk Log — Admin color-token migration, batch 7 / sub-batch 66

Part of the #2816 hardcoded-Tailwind-color-token migration (Stage 1). See
`docs/change-log/2026-08-21-admin-color-token-migration-plan.md` for the overall plan.

`earnings/page.tsx` (1,889 lines, 55 raw-color matches before this sub-batch) — this
sub-batch converts the genuine good/bad signals: the period-over-period `DeltaChip`
growth/decline indicator, the "Operational health" and "Payouts" metric-card accents
that the file's own code comments already document as intentional non-positive/
positive signals (e.g. "Amber accent because high outstanding is operational debt to
drivers, not a positive metric"), the ride-funnel cancellation-outcome cards, the
cancellation-mix legend/bar, the stuck-rides/blocked-drivers alert cards, the T4A
$30k-threshold mandatory-registration row, a payout retry error message, and the
subscription-transaction status badge. 26 matches remain, all confirmed decorative or
established exclusions (see below) — no further work needed on this file for Stage 1.

## Issue/gap identified
Multiple genuine signal groups on this earnings/financial page used fixed Tailwind
shades instead of the shared `--success`/`--warning`/`--destructive` tokens: growth/
decline deltas, operational-health metric accents (several already documented in code
comments as intentional non-positive/positive signals), the ride-funnel's cancellation
outcomes, the rider/driver cancellation-mix bar, the stuck/blocked alert cards, the
T4A mandatory-registration row, a payout-retry error message, and the subscription
status badge (active/expired/other).

## Root cause
Same as prior sub-batches: this page predates the shared semantic tokens. The
signal intent was already documented in prose comments in several places (e.g. the
"Outstanding to drivers" accent, the "Health-summary row" comment) but never wired to
the tokens those comments describe.

## Fix/remediation
- `DeltaChip`: up→`text-success bg-success/10`, down→`text-destructive bg-destructive/10`.
- Operational-health accents: Refunds/Promo Spend→`text-destructive`, Surge Revenue→
  `text-warning`.
- Ride funnel: Travelled→`text-success`, Rider Cancelled→`text-warning`, Driver
  Cancelled/Cancelled After Start→`text-destructive`.
- Cancellation-mix legend dots and bar segments: rider→`bg-warning`, driver→
  `bg-destructive` (system stays `bg-muted-foreground/60`, already neutral).
- Payouts metrics: Outstanding to drivers→`text-warning`, Paid out/Success rate→
  `text-success`, Failed→`text-destructive`.
- Stuck->48h alert card→`border-warning/40`/`text-warning`; Blocked-by-Stripe alert
  card→`border-destructive/40`/`text-destructive`.
- At-risk-drivers failure-count cell→`text-destructive`.
- T4A ≥$30k mandatory-registration row→`bg-warning/10`/`text-warning`.
- Subscription-transaction status badge (active/expired/other)→`bg-success/15
  text-success` / `bg-warning/15 text-warning` / `bg-destructive/15 text-destructive`.
- Payout-retry error message→`text-destructive`.

Left untouched (confirmed decorative/established exclusions — 26 remaining matches):
Net Revenue/Spinr Pass MRR metric accents and the driver/admin-earnings/tips triads
(money-category-differentiation, distinct arbitrary hues for unrelated revenue
categories in a KPI row); the violet "Spinr Pass" subscription-comparison cards,
comparison-mode toggle button, and section icons (consistent brand accent for the
named product tier, same reasoning as sub-batch 62's Spinr Pass card); the solid
`bg-emerald-600` "Approve" button (established dark-mode contrast-risk exclusion); and
a single decorative "active" count inline in a summary sentence (not paired with a
negative counterpart).

## Risk & impact on existing functionality
All edits are within `earnings/page.tsx`, not imported elsewhere — no shared-component
blast radius. No props, state shape, or exported symbols changed.

## User experience effect
Purely color-token substitutions to visually equivalent (already-approved,
contrast-verified) tokens. No layout, copy, or behavior change. Admin-portal-facing
only (Finance/Earnings section).

## Files modified
| File | What changed | Why |
|---|---|---|
| `src/app/dashboard/earnings/page.tsx` | `DeltaChip`, operational-health/payouts metric accents, ride-funnel, cancellation-mix, stuck/blocked alert cards, T4A row, subscription status badge, payout-retry error → `--success`/`--warning`/`--destructive` | #2816 token migration |

## Before/after snippet
```tsx
// DeltaChip — before
const color = up
    ? "text-emerald-600 dark:text-emerald-400 bg-emerald-50 dark:bg-emerald-900/20"
    : "text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-900/20";
// after
const color = up
    ? "text-success bg-success/10"
    : "text-destructive bg-destructive/10";
```
```tsx
// subscription status badge — before
t.status === "active" ? "bg-emerald-500/15 text-emerald-700 dark:text-emerald-400" :
t.status === "expired" ? "bg-amber-500/15 text-amber-700 dark:text-amber-400" :
"bg-red-500/15 text-red-700 dark:text-red-400"
// after
t.status === "active" ? "bg-success/15 text-success" :
t.status === "expired" ? "bg-warning/15 text-warning" :
"bg-destructive/15 text-destructive"
```

## Rollback plan
Pure CSS class-string revert — `git revert` this commit restores the prior hardcoded
classes with no data migration, feature flag, or config involved.

## Verification performed
- `npx eslint` on the file: 0 errors. 37 remaining warnings are the confirmed
  decorative/established exclusions listed above plus unrelated pre-existing
  `react-hooks` warnings — no new warnings introduced by this change.
- `npx vitest run`: 339/339 tests passing across all 35 test files.
- `npm run build` (Turbopack) not re-run — the pre-existing, diff-unrelated
  `@spinr/shared` "Unknown module type" Turbopack failure was already root-caused
  against unmodified `origin/main` in sub-batch 31/PR #4371; this sub-batch is plain
  Tailwind class-string edits with no import/module changes.

## What was NOT verified
- No visual regression tooling exists in this repo for the admin dashboard (standing
  gap, `ACTION_ITEMS.md`) — token substitutions were reasoned about against previously
  contrast-verified token values, not screenshotted.
- Not tested against a live Supabase/staging deployment — only against the existing
  mocked `vitest` fixtures.
