# Change Impact & Risk Log — #2816 Batch 7 sub-batch 13: corporate-accounts list, staff, disputes, driver appeals, AI console

## Issue/gap identified
Five more admin-dashboard files still used raw Tailwind color utilities for status
badges/icons/informational cards instead of the `--success`/`--warning`/`--destructive` semantic
tokens.

## Root cause
Pre-dates the token migration; written against literal Tailwind palette classes.

## Fix/remediation
- `dashboard/corporate-accounts/page.tsx`: `STATUS_PILL_CLASSES` categorical map — `active` and
  `closed` were already on tokens from an earlier pass; converted the two still-raw entries,
  `pending_verification` (yellow → `bg-warning/15 text-warning`) and `suspended` (orange →
  `bg-destructive/15 text-destructive`), completing the 4-state map on tokens. Converted the
  "wallets flagged" risk-portfolio alert card (border/background/text/link-border, all amber) to
  `--warning` tokens — a single warning-severity signal, not a categorical map. Left the "KYB
  re-verification due" card (sky/blue) untouched — informational, not a success/warning/destructive
  signal, matching the existing precedent for blue informational banners elsewhere in this
  migration. Left the solid-fill `Active`/`Inactive` account Badge (`bg-emerald-500`) and the
  solid-fill red "Delete" `AlertDialogAction` untouched — both are solid-fill white-text elements,
  the established contrast-risk exclusion.
- `dashboard/staff/page.tsx`: `ROLE_COLORS` (super_admin/operations/support/finance/custom, 5
  distinct roles) documented with a block `eslint-disable`/`eslint-enable` comment — the same
  "admin-role map, too many states for 3 tokens" treatment used elsewhere in this migration. Left
  the solid-fill orange "Reset MFA" and red "Delete" `AlertDialogAction` buttons untouched
  (solid-fill exclusion).
- `dashboard/disputes/page.tsx`: 2 header/dialog-title `AlertTriangle` icons (amber) → `text-warning`;
  the dispute-resolution outcome box → `bg-success/10` / `text-success` (single positive-outcome
  signal — note the box renders for any terminal `selected.resolution` value including "rejected",
  which is pre-existing behavior unrelated to this color-only change and out of scope here).
- `dashboard/drivers/appeals/page.tsx`: 2 header/dialog-title `Gavel` icons (amber) → `text-warning`;
  the approved/denied outcome box — a genuine 2-state ternary — converted both branches to
  `bg-success/10`/`text-success` (approved) and `bg-destructive/10`/`text-destructive` (denied).
- `dashboard/ai-console/page.tsx`: 2 identical "promo savings" line-item texts (`text-green-600`,
  no prior `dark:` pairing) → `text-success`.

## Risk & impact on existing functionality
Color-only class swaps; no logic, props, or data flow changed anywhere in this sub-batch.
- `STATUS_PILL_CLASSES` and `StatusPill` are defined and used only within
  `corporate-accounts/page.tsx` — not a shared component.
- `ROLE_COLORS` is local to `staff/page.tsx`.
- The dispute/appeal outcome boxes and icons are each local to their own page.
- The `ai-console` promo-savings text pattern is duplicated identically in two places in the same
  file (recommended-quote list item and fare-breakdown line); both converted for consistency.

## User experience effect
All five files are internal-admin-only screens; no rider/driver/corporate-customer-facing surface
touched. Visible effect is limited to badge/icon/card color — the underlying status logic,
labels, and data are unchanged. Not visible mid-session differently than the prior colors would
have been (same semantic meaning, theme-consistent color only).

## Files modified
| File | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/corporate-accounts/page.tsx` | `pending_verification`/`suspended` status pills + wallet-risk alert card → warning/destructive tokens | #2816 |
| `admin-dashboard/src/app/dashboard/staff/page.tsx` | `ROLE_COLORS` documented as an intentional categorical exclusion | #2816 |
| `admin-dashboard/src/app/dashboard/disputes/page.tsx` | 2 warning icons + resolution outcome box → tokens | #2816 |
| `admin-dashboard/src/app/dashboard/drivers/appeals/page.tsx` | 2 warning icons + approved/denied outcome box → tokens | #2816 |
| `admin-dashboard/src/app/dashboard/ai-console/page.tsx` | 2 promo-savings texts → `text-success` | #2816 |

## Before/after snippet
```tsx
// corporate-accounts/page.tsx — before
pending_verification: "bg-yellow-100 text-yellow-800 hover:bg-yellow-100 dark:bg-yellow-900/40 dark:text-yellow-300",
suspended: "bg-orange-100 text-orange-800 hover:bg-orange-100 dark:bg-orange-900/30 dark:text-orange-300",
// after
pending_verification: "bg-warning/15 text-warning hover:bg-warning/15",
suspended: "bg-destructive/15 text-destructive hover:bg-destructive/15",
```
```tsx
// drivers/appeals/page.tsx — before
<div className={`... ${selected.status === "approved" ? "bg-green-50 dark:bg-green-950/30" : "bg-red-50 dark:bg-red-950/30"}`}>
// after
<div className={`... ${selected.status === "approved" ? "bg-success/10" : "bg-destructive/10"}`}>
```

## Rollback plan
Pure CSS class revert — `git revert` this commit; no data migration, flag, or config change.

## Verification performed
- `npx eslint` on all 5 changed files: 0 errors, 21 warnings (all pre-existing advisories unrelated
  to color — `react-hooks/set-state-in-effect`, a hoisting warning in `staff/page.tsx`, unescaped
  entities, a label-association a11y note — plus the 2 deliberately-left raw-color warnings on the
  solid-fill MFA-reset/delete buttons in `staff/page.tsx`).
- `npx tsc --noEmit`: clean.
- `npx vitest run`: 35 files / 339 tests passed.
- `npm run build`: **production build completed successfully**, including
  `/dashboard/corporate-accounts`, `/dashboard/staff`, `/dashboard/disputes`,
  `/dashboard/drivers/appeals`, `/dashboard/ai-console` routes.

## What was NOT verified
No visual-regression tooling exists in this repo (standing gap). Colors were reasoned about
against the existing token definitions in `globals.css`, not screenshotted. Not tested against a
live Supabase-backed admin session — only static review of the conditional logic branches touched.
