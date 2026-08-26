# Change Impact & Risk Log — #2816 Batch 7 sub-batch 28: corporate account detail, company-portal members mirror, legacy import, portal role badges

## Issue/gap identified
Five more admin-dashboard files still used raw Tailwind color utilities for status maps and
single-signal notices instead of the semantic tokens.

## Root cause
Pre-dates the token migration; written against literal Tailwind palette classes.

## Fix/remediation
- `dashboard/corporate-accounts/[id]/page.tsx`: `STATUS_PILL_CLASSES` (pending_verification/
  suspended — the same map as the list page's `corporate-accounts/page.tsx`, already converted in
  an earlier sub-batch) → warning/destructive tokens; the outline-style "Suspend" button (not
  solid-fill) → `text-destructive`. Left the `TRANSITIONS` confirm-dialog `confirmClass` values
  (orange/emerald/red) and the solid-fill "Reactivate" button untouched — solid-fill contrast-risk
  exclusion — and a blue "view details" link untouched (informational).
- `company-portal/[id]/members/page.tsx` (**corporate-customer-facing**): its own `STATUS_COLORS`
  mirror (invited/active/suspended/removed) → warning/success/destructive/muted tokens; the
  invite-feedback banner (ok/not-ok) → success/warning tokens.
- `dashboard/bulk-operations/_components/LegacyBookingImport.tsx`: a single warning-severity
  notice (amber) → `--warning` tokens; a single success notice (green) → `--success` tokens.
- `company-portal/page.tsx` and `company-portal/[id]/layout.tsx` (**both
  corporate-customer-facing**): the membership-role `Badge` (uniformly emerald regardless of role
  — owner/admin/member all render identically, so this is not a categorical role map, just a
  decorative "verified membership" styling) → `bg-success/15 text-success`. Left the decorative
  `Building2` brand-icon wrapper in both files untouched — the same pattern already excluded on
  `company-signup/page.tsx` and `company-login/page.tsx` in earlier sub-batches.

## Risk & impact on existing functionality
Color-only class swaps; no logic, props, or data flow changed.
- `corporate-accounts/[id]/page.tsx`'s `STATUS_PILL_CLASSES` is local to that detail page (a
  separate copy from the already-converted list page's map, not a shared import) — grepped to
  confirm no cross-file dependency.
- `company-portal/[id]/members/page.tsx`'s `STATUS_COLORS` is the corporate-customer-facing mirror
  of the admin-side map converted in sub-batch 27; both now use identical tokens, keeping the two
  surfaces visually consistent for the same underlying status values.
- `LegacyBookingImport.tsx`'s notices and the two portal role badges are each local to their own
  file.

## User experience effect
Three of the five files are corporate-customer-facing (`company-portal/[id]/members/page.tsx`,
`company-portal/page.tsx`, `company-portal/[id]/layout.tsx`) — their member-status badges,
invite-feedback banner, and role badge now use the theme-consistent tokens instead of hardcoded
colors, matching the rest of the portal's dark-mode support. The other two files
(`corporate-accounts/[id]/page.tsx`, `LegacyBookingImport.tsx`) are internal-admin-only and purely
cosmetic.

## Files modified
| File | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/corporate-accounts/[id]/page.tsx` | `STATUS_PILL_CLASSES` + outline "Suspend" button → warning/destructive tokens | #2816 |
| `admin-dashboard/src/app/company-portal/[id]/members/page.tsx` | `STATUS_COLORS` + invite-feedback banner → tokens | #2816 |
| `admin-dashboard/src/app/dashboard/bulk-operations/_components/LegacyBookingImport.tsx` | Warning notice + success notice → tokens | #2816 |
| `admin-dashboard/src/app/company-portal/page.tsx` | Membership-role badge → success token | #2816 |
| `admin-dashboard/src/app/company-portal/[id]/layout.tsx` | Membership-role badge → success token | #2816 |

## Before/after snippet
```tsx
// company-portal/[id]/members/page.tsx STATUS_COLORS — before
invited: "bg-yellow-100 text-yellow-800 dark:bg-yellow-900/40 dark:text-yellow-300",
active: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300",
suspended: "bg-orange-100 text-orange-800 dark:bg-orange-900/30 dark:text-orange-300",
removed: "bg-gray-200 text-gray-600 dark:bg-gray-800 dark:text-gray-400",
// after
invited: "bg-warning/15 text-warning",
active: "bg-success/15 text-success",
suspended: "bg-destructive/15 text-destructive",
removed: "bg-muted text-muted-foreground",
```

## Rollback plan
Pure CSS class revert — `git revert` this commit; no data migration, flag, or config change.

## Verification performed
- `npx eslint` on all 5 changed files: 0 errors, 12 warnings (pre-existing unrelated advisories,
  plus the deliberately-left raw-color warnings on the solid-fill confirm buttons/dialog classes
  and the informational link).
- `npx tsc --noEmit`: clean.
- `npx vitest run`: 35 files / 339 tests passed.
- `npm run build`: **production build completed successfully**.

## What was NOT verified
No visual-regression tooling exists in this repo (standing gap). Colors were reasoned about
against the existing token definitions, not screenshotted. Not tested against a live
Supabase-backed admin/corporate-portal session.
