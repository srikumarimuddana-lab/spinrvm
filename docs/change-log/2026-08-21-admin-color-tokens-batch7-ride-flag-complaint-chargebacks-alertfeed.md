# Change Impact & Risk Log — #2816 Batch 7 sub-batch 15: ride flag/complaint forms, chargebacks tab, monitoring alert feed

## Issue/gap identified
Four more admin-dashboard files still used raw Tailwind color utilities for single-signal
warning/destructive icons and alert boxes instead of the semantic tokens.

## Root cause
Pre-dates the token migration; written against literal Tailwind palette classes.

## Fix/remediation
- `rides/_components/ride-flag-form.tsx`: dialog-title `AlertTriangle` icon (`text-red-500`) →
  `text-destructive`. Left the solid-fill red "Flag" submit button (`text-white bg-red-600`)
  untouched (contrast-risk exclusion).
- `rides/_components/ride-complaint-form.tsx`: dialog-title `FileWarning` icon (`text-amber-500`) →
  `text-warning`. Left the solid-fill amber "Submit" button untouched (same exclusion).
- `dashboard/disputes/chargebacks-tab.tsx`: 2 identical load/download-error banners
  (`bg-red-50 dark:bg-red-950/30 ... text-red-700 dark:text-red-400`) → `bg-destructive/10
  text-destructive`; the "Submit Evidence to Stripe" confirmation dialog's `Send` icon and
  "cannot be undone" warning box (both amber) → `text-warning` / `bg-warning/10 text-warning`.
- `dashboard/monitoring/alert-feed.tsx`: a `Zap` alert-severity icon (`text-amber-500`) →
  `text-warning`.

Left untouched (verified, no change needed): `rides/_components/create-ride-modal.tsx` — its
pickup (`blue`) and dropoff (`red`) address-input icon/border/ring colors are the established
fixed pickup/dropoff UI convention used elsewhere in this migration (map-pin dot colors), not a
success/warning/destructive signal, so no edits were made to that file.

## Risk & impact on existing functionality
Color-only class swaps; no logic, props, or data flow changed. Each converted element is local to
its own file/component — `ride-flag-form.tsx` and `ride-complaint-form.tsx` are separate modal
components (grepped: no shared color constants between them), and the chargebacks error-banner
pattern, while duplicated twice in the same file (load error / download error), was converted
identically in both places for consistency.

## User experience effect
All four files are internal-admin-only screens (ride moderation forms, dispute chargeback
handling, live monitoring alert feed). Purely cosmetic icon/banner color change — the underlying
error/warning text and logic are unchanged, and not visible mid-session differently than before.

## Files modified
| File | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/rides/_components/ride-flag-form.tsx` | Dialog warning icon → destructive token | #2816 |
| `admin-dashboard/src/app/dashboard/rides/_components/ride-complaint-form.tsx` | Dialog warning icon → warning token | #2816 |
| `admin-dashboard/src/app/dashboard/disputes/chargebacks-tab.tsx` | 2 error banners + Stripe-submit warning dialog → destructive/warning tokens | #2816 |
| `admin-dashboard/src/app/dashboard/monitoring/alert-feed.tsx` | Severity icon → warning token | #2816 |

## Before/after snippet
```tsx
// chargebacks-tab.tsx — before (both error banners, identical)
className="flex items-center justify-between gap-3 border-b bg-red-50 dark:bg-red-950/30 px-4 py-3 text-sm text-red-700 dark:text-red-400"
// after
className="flex items-center justify-between gap-3 border-b bg-destructive/10 px-4 py-3 text-sm text-destructive"
```

## Rollback plan
Pure CSS class revert — `git revert` this commit; no data migration, flag, or config change.

## Verification performed
- `npx eslint` on all 4 changed files: 0 errors, 9 warnings (pre-existing unrelated
  `react-hooks/set-state-in-effect` and `jsx-a11y/label-has-associated-control` advisories, plus
  the 2 deliberately-left raw-color warnings on the solid-fill submit buttons).
- `npx tsc --noEmit`: clean.
- `npx vitest run`: 35 files / 339 tests passed.
- `npm run build`: **production build completed successfully**.

## What was NOT verified
No visual-regression tooling exists in this repo (standing gap). Colors were reasoned about
against the existing token definitions, not screenshotted. Not tested against a live
Supabase-backed admin session.
