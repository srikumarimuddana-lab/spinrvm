# Change Impact & Risk Log — #2816 Batch 7 sub-batch 18: sidebar nav badges, company login, financial panel, settings

## Issue/gap identified
Four more admin-dashboard files still used raw Tailwind color utilities for single-signal
success/warning/destructive indicators instead of the semantic tokens.

## Root cause
Pre-dates the token migration; written against literal Tailwind palette classes.

## Fix/remediation
- `components/sidebar.tsx`: the nav "emphasize" (needs-attention) icon tint, the collapsed-mode
  notification dot, and the expanded-mode count pill — all three are the same "pending items
  need attention" feature (KYB queue, expiring documents, etc. surfaced in the nav) — converted
  from amber to `text-warning` / `bg-warning` / `bg-warning/15 text-warning` respectively.
- `company-login/page.tsx` (**public/corporate-customer-facing**): the "Register your company"
  CTA link (`text-emerald-700`, previously missing any `dark:` variant at all) → `text-success`
  — fixes a real missing dark-mode variant, not just a token swap. Left the decorative
  `Building2` brand-icon wrapper untouched — same treatment as the identical pattern on
  `company-signup/page.tsx`, a branding icon rather than a signal.
- `components/analytics/financial-panel.tsx`: the fetch-error message (`text-red-600
  dark:text-red-400`) → `text-destructive`. Left the "Surge penetration" `Zap` icon (amber)
  untouched — a decorative stat-label icon in a panel where every other stat icon (DollarSign,
  Receipt, Clock, Repeat, Building2) is uncolored; surge is informational, not itself a
  warning/error state.
- `dashboard/settings/page.tsx`: the deliverability "failed" count (turns red above a 1% failure
  threshold) → `text-destructive`; the "MFA is enabled" confirmation (`text-green-600
  dark:text-green-400`) → `text-success`.

Verified, no change needed: `dashboard/vehicle-types/page.tsx` — its only match is a solid-fill
red "Delete" `AlertDialogAction` button, the established contrast-risk exclusion; no edits made.

## Risk & impact on existing functionality
Color-only class swaps; no logic, props, or data flow changed.
- `components/sidebar.tsx` is the shared app-wide navigation — grepped: it's rendered once from
  the dashboard layout, not duplicated. Only the 3 amber classes changed; the `item.emphasize` /
  `badgeFor()` logic that decides *whether* to show the badge is untouched.
- `company-login/page.tsx`, `financial-panel.tsx`, and `settings/page.tsx` changes are each local
  to their own file.

## User experience effect
- `company-login/page.tsx` is public/corporate-customer-facing — the "Register your company" link
  now renders with dark-mode support it previously lacked entirely (a real contrast fix, not just
  a token swap).
- The sidebar badge/dot/pill change affects every admin session (shared nav), but is purely a
  color swap on an existing "needs attention" indicator — the same items get flagged as before.
- `financial-panel.tsx` and `settings/page.tsx` changes are internal-admin-only and purely cosmetic.

## Files modified
| File | What changed | Why |
|---|---|---|
| `admin-dashboard/src/components/sidebar.tsx` | Nav emphasis icon, notification dot, count pill → warning token | #2816 |
| `admin-dashboard/src/app/company-login/page.tsx` | "Register your company" link → success token (fixes missing dark-mode variant) | #2816 |
| `admin-dashboard/src/components/analytics/financial-panel.tsx` | Fetch-error message → destructive token | #2816 |
| `admin-dashboard/src/app/dashboard/settings/page.tsx` | Failed-deliverability count + MFA-enabled confirmation → destructive/success tokens | #2816 |

## Before/after snippet
```tsx
// sidebar.tsx — before (3 occurrences of the same pattern)
item.emphasize && !active && "text-amber-600 dark:text-amber-500",
<span className="absolute top-1 right-1 w-2 h-2 rounded-full bg-amber-500 ring-2 ring-sidebar" />
<span className="ml-auto bg-amber-100 dark:bg-amber-900/30 text-amber-700 dark:text-amber-300 ...">
// after
item.emphasize && !active && "text-warning",
<span className="absolute top-1 right-1 w-2 h-2 rounded-full bg-warning ring-2 ring-sidebar" />
<span className="ml-auto bg-warning/15 text-warning ...">
```

## Rollback plan
Pure CSS class revert — `git revert` this commit; no data migration, flag, or config change.

## Verification performed
- `npx eslint` on all 4 changed files: 0 errors, 10 warnings (pre-existing unrelated advisories,
  plus the 2 deliberately-left raw-color warnings on the decorative brand icon and surge-stat icon).
- `npx tsc --noEmit`: clean.
- `npx vitest run`: 35 files / 339 tests passed.
- `npm run build`: **production build completed successfully**.

## What was NOT verified
No visual-regression tooling exists in this repo (standing gap). Colors were reasoned about
against the existing token definitions, not screenshotted. Not tested against a live
Supabase-backed admin/corporate-login session.
