# Change Impact & Risk Log — #2816 Batch 7 sub-batch 21: ride list, SGI removal-queue notice, live monitoring, payout detail

## Issue/gap identified
Four more admin-dashboard files still used raw Tailwind color utilities for single-signal
success/warning/destructive indicators instead of the semantic tokens.

## Root cause
Pre-dates the token migration; written against literal Tailwind palette classes.

## Fix/remediation
- `rides/_components/ride-list.tsx`: the "+tip" earned-tip amount and the "Time" (actual trip
  duration) label+value — both single positive-signal figures standing apart from the uncolored
  Plan/Pickup/Trip distance rows — → `text-success`. Left the rider (blue) / driver (emerald)
  avatar-badge icons untouched — the established role-differentiation convention already applied
  to `rides/live/[id]/page.tsx` in an earlier sub-batch.
- `dashboard/data-transfer/SgiFormsTab.tsx`: the entire "drivers left but still filed with SGI"
  removal-queue notice (card border/background, warning icon, heading, body text, table borders,
  "no linked account" tag, and the unresolvable-count footnote — all amber) → `--warning` tokens
  throughout. This is a single warning-severity notice, not a categorical map, so every amber
  shade in the block converts uniformly.
- `dashboard/monitoring/page.tsx`: the "Live data paused" stale-feed banner (yellow) → `--warning`
  tokens. Left the "LIVE" pulsing-dot header indicator (red) untouched — the established fixed
  decorative convention — and the ride-lifecycle status pills (searching/driver_assigned/
  driver_arrived) untouched — already individually documented with
  `eslint-disable-next-line` comments from an earlier pass.
- `dashboard/earnings/payouts/[id]/page.tsx`: `STATUS_CONFIG`'s `processing` entry (the only
  remaining raw color in an otherwise fully-token map: completed/paid/pending/failed/cancelled
  were already converted) → `bg-warning/15 text-warning`, matching the "in-progress/under-review →
  warning" precedent used for other intermediate states in this migration; the inline failure-reason
  text and its detail box → `--destructive` tokens.

Verified, no change needed: `company-portal/[id]/bookings/page.tsx` — its `STATUS_STYLES` (8-state
ride lifecycle map) was already documented with a block `eslint-disable`/`eslint-enable` comment
from an earlier pass; no edits made.

## Risk & impact on existing functionality
Color-only class swaps; no logic, props, or data flow changed.
- `ride-list.tsx`'s tip/time colors are local to that file's row-rendering.
- `SgiFormsTab.tsx`'s removal-queue notice is local to that tab.
- `monitoring/page.tsx`'s stale-feed banner is local to the live-monitoring page; the WebSocket
  status logic that decides *when* to show it is untouched.
- `earnings/payouts/[id]/page.tsx`'s `STATUS_CONFIG` is local to the payout detail page.

## User experience effect
All four files are internal-admin-only screens (ride list, SGI regulatory-forms tab, live
monitoring dashboard, payout detail). Purely cosmetic color change — the underlying tip/time
figures, removal-queue data, live-feed status, and payout status logic are unchanged.

## Files modified
| File | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/rides/_components/ride-list.tsx` | Tip amount + actual-time label/value → success token | #2816 |
| `admin-dashboard/src/app/dashboard/data-transfer/SgiFormsTab.tsx` | Entire removal-queue warning notice → warning token | #2816 |
| `admin-dashboard/src/app/dashboard/monitoring/page.tsx` | "Live data paused" banner → warning token | #2816 |
| `admin-dashboard/src/app/dashboard/earnings/payouts/[id]/page.tsx` | `processing` status + failure-reason text/box → warning/destructive tokens | #2816 |

## Before/after snippet
```tsx
// SgiFormsTab.tsx — before
<div className="rounded-lg border border-amber-300 bg-amber-50 dark:border-amber-800 dark:bg-amber-900/20 p-3 space-y-2">
// after
<div className="rounded-lg border border-warning bg-warning/10 p-3 space-y-2">
```

## Rollback plan
Pure CSS class revert — `git revert` this commit; no data migration, flag, or config change.

## Verification performed
- `npx eslint` on all 4 changed files: 0 errors, 24 warnings (pre-existing unrelated advisories,
  plus the already-documented ride-status-pill exclusions and the untouched role-differentiation
  icons).
- `npx tsc --noEmit`: clean.
- `npx vitest run`: 35 files / 339 tests passed.
- `npm run build`: **production build completed successfully**.

## What was NOT verified
No visual-regression tooling exists in this repo (standing gap). Colors were reasoned about
against the existing token definitions, not screenshotted. Not tested against a live
Supabase-backed admin session or a live WebSocket disconnect scenario.
