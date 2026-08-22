# Change Impact & Risk Log — Admin color-token migration, batch 7 / sub-batch 52

Part of the #2816 hardcoded-Tailwind-color-token migration (Stage 1). See
`docs/change-log/2026-08-21-admin-color-token-migration-plan.md` for the overall plan.

## Issue/gap identified
Three more `components/analytics/*` panels used hardcoded Tailwind color utilities:
fixed `text-red-600 dark:text-red-400` error text in `driver-offers-panel.tsx`,
`supply-panel.tsx`, and `marketplace-overview-panel.tsx`; and a fixed amber warning
banner (attribution-honesty note) in `marketplace-overview-panel.tsx`.

## Root cause
Same as prior sub-batches in this migration: these panels predate the shared
`--success`/`--warning`/`--destructive` semantic tokens in `globals.css`.

## Fix/remediation
- `driver-offers-panel.tsx`, `supply-panel.tsx`, `marketplace-overview-panel.tsx`:
  error message `text-red-600 dark:text-red-400` → `text-destructive`.
- `marketplace-overview-panel.tsx`: the "attribution honesty" warning banner (shown when
  some cancellations predate structured attribution columns and were classified via
  free-text matching) converted from
  `border-amber-300 bg-amber-50 text-amber-800 dark:border-amber-700 dark:bg-amber-950 dark:text-amber-200`
  to `border-warning/40 bg-warning/10 text-warning` — this is a genuine warning signal
  (data-quality caveat to the operator), not decorative, so token conversion is correct.

## Risk & impact on existing functionality
All edits are local, single-purpose class-string swaps inside leaf presentational
components — none of the three files are shared/imported elsewhere in a way that would
propagate a class-string change (each renders its own error/warning UI independently).
No props, state shape, or exported symbols changed. The `--warning`/`--destructive`
tokens are pre-existing and already contrast-verified in earlier sub-batches of this
same migration.

## User experience effect
Purely a color-token substitution — the error text and warning banner resolve to
visually equivalent (already-approved) tokens under both light and dark themes. No
layout, copy, or behavior change. Admin-portal-facing only (Analytics tab).

## Files modified
| File | What changed | Why |
|---|---|---|
| `src/components/analytics/driver-offers-panel.tsx` | `text-red-600 dark:text-red-400` → `text-destructive` on the error message | #2816 token migration |
| `src/components/analytics/supply-panel.tsx` | `text-red-600 dark:text-red-400` → `text-destructive` on the error message | #2816 token migration |
| `src/components/analytics/marketplace-overview-panel.tsx` | error message → `text-destructive`; attribution-honesty warning banner → `border-warning/40 bg-warning/10 text-warning` | #2816 token migration |

## Before/after snippet
```tsx
// error text — before (all three files)
<p className="text-sm text-red-600 dark:text-red-400">{error}</p>
// after
<p className="text-sm text-destructive">{error}</p>
```
```tsx
// marketplace-overview-panel.tsx warning banner — before
<div className="flex items-start gap-2 rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:border-amber-700 dark:bg-amber-950 dark:text-amber-200">
// after
<div className="flex items-start gap-2 rounded-md border border-warning/40 bg-warning/10 px-3 py-2 text-xs text-warning">
```

## Rollback plan
Pure CSS class-string revert — `git revert` this commit restores the prior hardcoded
classes with no data migration, feature flag, or config involved.

## Verification performed
- `npx eslint` on the three edited files: 0 errors. 6 remaining warnings are all
  pre-existing, unrelated `react-hooks` warnings (`exhaustive-deps` /
  `set-state-in-effect`) already present on these files before this change — no new
  `no-restricted-syntax` warnings.
- `npx vitest run`: 339/339 tests passing across all 35 test files.
- `npm run build` (Turbopack) not re-run — the pre-existing, diff-unrelated
  `@spinr/shared` "Unknown module type" Turbopack failure was already root-caused
  against unmodified `origin/main` in sub-batch 31/PR #4371; this sub-batch is a plain
  Tailwind class-string edit with no import/module changes.

## What was NOT verified
- No visual regression tooling exists in this repo for the admin dashboard (standing
  gap, `ACTION_ITEMS.md`) — reasoned about against previously contrast-verified token
  values, not screenshotted.
- Not tested against a live Supabase/staging deployment — only against the existing
  mocked `vitest` fixtures for these panels.
