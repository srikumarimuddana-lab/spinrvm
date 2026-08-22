# Change Impact & Risk Log — Admin color-token migration, batch 7 / sub-batch 73

Part of the #2816 hardcoded-Tailwind-color-token migration (Stage 1). See
`docs/change-log/2026-08-21-admin-color-token-migration-plan.md` for the overall plan.

## Issue/gap identified
`users/page.tsx`'s two solid-fill "Credit" wallet-action buttons and
`promotions/page.tsx`'s "Delete promo code?" confirmation button lacked either the
inline documentation this migration uses for its contrast-risk exclusions, or the
standard destructive-button token pattern.

## Root cause
The delete button predates the shared `--destructive` token. The credit buttons were
already a deliberate dark-mode `--success` contrast-risk exception from earlier work
but lacked the inline comment other exceptions in this migration carry.

## Fix/remediation
- `promotions/page.tsx`: "Delete promo code?" `AlertDialogAction` → standard shadcn
  destructive pattern (`bg-destructive text-destructive-foreground
  hover:bg-destructive/90`).
- `users/page.tsx`: added documenting `eslint-disable-next-line` comments to the two
  existing solid-fill "Credit" wallet-action buttons (`bg-emerald-600`, white text) —
  these are already a deliberate contrast-risk exception (dark-mode `--success` at
  2.02:1 fails WCAG AA against white text) but lacked the inline reason; no color
  change.

Left untouched (decorative/categorical, confirmed by review not silently skipped):
- `users/page.tsx`'s already-fully-converted user-status badge (banned/suspended/
  pending_deletion/active), its Riders/Drivers stat-card icons (emerald/violet
  category differentiation), its "Driver" role badge (violet, decorative identity),
  and its wallet-balance card gradient (decorative brand styling, not a status
  signal).
- `promotions/page.tsx`'s summary-stat icon array (Total Codes/Active/Expired/Private/
  Redemptions/Discount Given — 6-hue category differentiation, same pattern as
  `ride-stats-cards.tsx`/`driver-stats-cards.tsx`), its Public/Private/Expired tab
  underline (a single decorative "selected tab" color reused across all three tabs,
  not tied to tab identity or severity), its violet promo-code branding throughout,
  and its date-range-picker "selected" button highlight (decorative UI selection
  state, same pattern as `venues.tsx`'s selected-pickup-point highlight).

## Risk & impact on existing functionality
All edits are within `app/dashboard/users/page.tsx` and `app/dashboard/promotions/
page.tsx`. Grepped for other importers: both are leaf route pages
(`app/dashboard/*/page.tsx`), not imported elsewhere. No shared-component blast
radius. No props, state shape, or exported symbols changed — one pure class-string
substitution plus two documentary comments with zero visual change.

## User experience effect
The "Delete promo code?" button changes from a fixed red to the standard destructive
token (visually near-identical). No other visual, layout, copy, or behavior change —
the two "Credit" button edits are comment-only. Admin-portal-facing only.

## Files modified
| File | What changed | Why |
|---|---|---|
| `src/app/dashboard/promotions/page.tsx` | "Delete promo code?" confirm button → standard destructive pattern | #2816 token migration |
| `src/app/dashboard/users/page.tsx` | Documented two existing "Credit" button contrast-risk exceptions (no color change) | #2816 token migration |

## Before/after snippet
```tsx
// promotions/page.tsx "Delete promo code?" button — before
<AlertDialogAction onClick={confirmDelete} className="bg-red-600 hover:bg-red-700">Delete</AlertDialogAction>
// after
<AlertDialogAction onClick={confirmDelete} className="bg-destructive text-destructive-foreground hover:bg-destructive/90">Delete</AlertDialogAction>
```

## Rollback plan
Pure CSS class-string revert — `git revert` this commit restores the prior classes with
no data migration, feature flag, or config involved.

## Verification performed
- `npx eslint` on both files: 0 errors (15 pre-existing warnings on `users/page.tsx`,
  27 on `promotions/page.tsx` — all decorative/categorical exceptions reviewed above
  plus one unrelated `react-hooks` warning; neither edited line appears in either
  warning list).
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
