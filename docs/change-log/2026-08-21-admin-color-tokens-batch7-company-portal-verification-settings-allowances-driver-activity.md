# Change Impact & Risk Log — #2816 Batch 7 sub-batch 25: company-portal verification/settings/allowances, driver activity, document upload

## Issue/gap identified
Five more admin-dashboard files still used raw Tailwind color utilities for single-signal
success/warning confirmation text instead of the semantic tokens.

## Root cause
Pre-dates the token migration; written against literal Tailwind palette classes.

## Fix/remediation
- `company-portal/[id]/verification/page.tsx` (**corporate-customer-facing**): a success notice
  banner (`bg-emerald-50 dark:bg-emerald-900/20 text-emerald-800 dark:text-emerald-300`) →
  `bg-success/15 text-success`.
- `company-portal/[id]/settings/page.tsx` (**corporate-customer-facing**): an identical
  save-feedback pattern → `bg-success/15 text-success`.
- `company-portal/[id]/allowances/page.tsx` (**corporate-customer-facing**): the same
  save-feedback pattern → `bg-success/15 text-success`.
- `dashboard/drivers/_components/driver-activity.tsx`: an "empty before" per-trip stat annotation
  (amber) → `text-warning`.
- `dashboard/drivers/_components/document-upload-dialog.tsx`: the "map every file before
  uploading" validation hint (amber) → `text-warning`.

## Risk & impact on existing functionality
Color-only class swaps; no logic, props, or data flow changed. Each converted element is local to
its own file's own feedback/notice rendering — none are shared components.

## User experience effect
Three of the five files are corporate-customer-facing (verification, settings, allowances pages
in the company portal) — their save-confirmation banners now use the theme-consistent
`--success` token instead of a hardcoded emerald, matching the rest of the portal's dark-mode
support. The other two files (`driver-activity.tsx`, `document-upload-dialog.tsx`) are
internal-admin-only and purely cosmetic.

## Files modified
| File | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/company-portal/[id]/verification/page.tsx` | Success notice → success token | #2816 |
| `admin-dashboard/src/app/company-portal/[id]/settings/page.tsx` | Save-feedback text → success token | #2816 |
| `admin-dashboard/src/app/company-portal/[id]/allowances/page.tsx` | Save-feedback text → success token | #2816 |
| `admin-dashboard/src/app/dashboard/drivers/_components/driver-activity.tsx` | "Empty before" stat → warning token | #2816 |
| `admin-dashboard/src/app/dashboard/drivers/_components/document-upload-dialog.tsx` | Validation hint → warning token | #2816 |

## Before/after snippet
```tsx
// company-portal settings/allowances/verification — before (identical pattern)
<p className="rounded bg-emerald-50 dark:bg-emerald-900/20 p-2 text-xs text-emerald-800 dark:text-emerald-300">{feedback}</p>
// after
<p className="rounded bg-success/15 p-2 text-xs text-success">{feedback}</p>
```

## Rollback plan
Pure CSS class revert — `git revert` this commit; no data migration, flag, or config change.

## Verification performed
- `npx eslint` on all 5 changed files: 0 errors, 4 warnings (pre-existing unrelated advisories
  only).
- `npx tsc --noEmit`: clean.
- `npx vitest run`: 35 files / 339 tests passed.
- `npm run build`: **production build completed successfully**.

## What was NOT verified
No visual-regression tooling exists in this repo (standing gap). Colors were reasoned about
against the existing token definitions, not screenshotted. Not tested against a live
Supabase-backed corporate-portal session.
