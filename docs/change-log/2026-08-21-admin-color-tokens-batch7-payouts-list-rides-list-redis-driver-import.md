# Change Impact & Risk Log — #2816 Batch 7 sub-batch 29: payouts list, rides list, Redis monitoring, driver import

## Issue/gap identified
Four more admin-dashboard files still used raw Tailwind color utilities for single-signal
success/destructive notices instead of the semantic tokens.

## Root cause
Pre-dates the token migration; written against literal Tailwind palette classes.

## Fix/remediation
- `dashboard/earnings/payouts/page.tsx`: `STATUS_CONFIG`'s `processing` entry (completed/pending/
  failed/cancelled were already on tokens) documented with an `eslint-disable-next-line` — the
  same "in progress, no clean token fit" treatment used for the identical `processing` state
  elsewhere in this migration; the toast success banner → `bg-success/10 border-success
  text-success`; the "failed payouts" alert banner → `bg-destructive/10 border-destructive
  text-destructive`.
- `dashboard/rides/page.tsx`: the "failed to load rides" banner and its "Retry" link →
  `border-destructive bg-destructive/10 text-destructive`.
- `dashboard/monitoring/redis/page.tsx`: a per-instance error/warning line → `text-destructive`.
- `dashboard/drivers/import/page.tsx`: the commit-success summary card's border (`border-emerald-300
  dark:border-emerald-800`) → `border-success`.

Verified, no change needed: `dashboard/quests/page.tsx` — its `STATUS_COLORS`
(active/completed/claimed/expired) was already fully documented with individual
`eslint-disable-next-line` comments in an earlier pass; the Trophy icon and reward-amount amber
theme are a consistent decorative "quest reward" color used throughout the page, not signals; no
edits made.

## Risk & impact on existing functionality
Color-only class swaps (plus one added lint-suppression comment) — no logic, props, or data flow
changed. Each converted element is local to its own file's own banner/card rendering.

## User experience effect
All four files are internal-admin-only screens (driver payouts list, rides list, Redis monitoring,
driver bulk import). Purely cosmetic — the underlying payout-status classification, load-error
detection, Redis health reporting, and import-commit logic are unchanged.

## Files modified
| File | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/earnings/payouts/page.tsx` | `processing` documented as an intentional exclusion; toast + failed-alert banners → success/destructive tokens | #2816 |
| `admin-dashboard/src/app/dashboard/rides/page.tsx` | Load-error banner → destructive token | #2816 |
| `admin-dashboard/src/app/dashboard/monitoring/redis/page.tsx` | Per-instance error/warning line → destructive token | #2816 |
| `admin-dashboard/src/app/dashboard/drivers/import/page.tsx` | Commit-success card border → success token | #2816 |

## Before/after snippet
```tsx
// rides/page.tsx — before
<div className="rounded-md border border-red-200 dark:border-red-800 bg-red-50 dark:bg-red-900/10 px-4 py-3 text-sm text-red-700 dark:text-red-300 flex items-center justify-between">
// after
<div className="rounded-md border border-destructive bg-destructive/10 px-4 py-3 text-sm text-destructive flex items-center justify-between">
```

## Rollback plan
Pure CSS class revert (plus one added lint-suppression comment) — `git revert` this commit; no
data migration, flag, or config change.

## Verification performed
- `npx eslint` on all 4 changed files: 0 errors, 8 warnings (pre-existing unrelated advisories
  only).
- `npx tsc --noEmit`: clean.
- `npx vitest run`: 35 files / 339 tests passed.
- `npm run build`: **production build completed successfully**.

## What was NOT verified
No visual-regression tooling exists in this repo (standing gap). Colors were reasoned about
against the existing token definitions, not screenshotted. Not tested against a live
Supabase-backed admin session.
