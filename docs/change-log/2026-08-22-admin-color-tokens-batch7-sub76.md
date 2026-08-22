# Change Impact & Risk Log — Admin color-token migration, batch 7 / sub-batch 76

Part of the #2816 hardcoded-Tailwind-color-token migration (Stage 1). See
`docs/change-log/2026-08-21-admin-color-token-migration-plan.md` for the overall plan.

## Issue/gap identified
`corporate-accounts/[id]/page.tsx`'s company-status-transition confirm buttons
(suspend/reactivate/close) and `[id]/members/page.tsx`'s allowance-request
approve/deny buttons and "Remove member" confirm button used fixed Tailwind shades.
Two categories: the destructive-tier ones (close, deny, remove) had a clean token
equivalent; the success-tier ones (reactivate, approve) and one warning-tier one
(suspend, plus a pending-count badge) did not — same solid-fill contrast-risk pattern
established throughout this migration, extended here to a case with no
`--warning-foreground` token at all.

## Root cause
These predate the shared tokens. The destructive branches simply hadn't been converted
yet; the success/warning branches are genuine dark-mode contrast-risk exceptions
(confirmed here for the first time that no solid-fill `--warning` button pattern exists
anywhere in this design system — `globals.css` defines `--warning` as a text/tint token
only, no paired foreground for white-on-warning text).

## Fix/remediation
- `[id]/page.tsx`: `TRANSITIONS.close.confirmClass` (permanent, irreversible account
  closure) → standard shadcn destructive pattern.
- `[id]/page.tsx`: documented `TRANSITIONS.suspend.confirmClass` (orange, disruptive but
  reversible) as a no-token-equivalent exception — this migration has never introduced
  a solid-fill warning button anywhere, and dark-mode `--warning` is not
  contrast-verified against white text.
- `[id]/page.tsx`: documented `TRANSITIONS.reactivate.confirmClass` and its duplicate
  trigger `Button` (both solid emerald) as the standard dark-mode `--success`
  contrast-risk exception.
- `[id]/members/page.tsx`: documented the approve/deny dialog's approve branch and the
  inline table "Approve" button (both solid emerald) as the same `--success`
  contrast-risk exception; converted the deny branch to standard destructive.
- `[id]/members/page.tsx`: documented the "Allowance requests" pending-count badge
  (solid yellow, white text) as a no-token-equivalent exception, same reasoning as the
  suspend button above.
- `[id]/members/page.tsx`: "Remove member?" confirm button → standard destructive
  pattern.

Left untouched (decorative/already-tokenized, confirmed by review not silently
skipped):
- `[id]/page.tsx`'s "View KYB document" link (blue) — decorative link-styling
  convention.
- `[id]/members/page.tsx`'s `REQUEST_STATUS_COLORS` (pending/approved/auto_approved/
  denied) — already fully converted from a prior sub-batch.

## Risk & impact on existing functionality
All edits are within `app/dashboard/corporate-accounts/[id]/page.tsx` and
`app/dashboard/corporate-accounts/[id]/members/page.tsx`. Grepped for other importers:
both are dynamic-route leaf pages, not imported elsewhere. No shared-component blast
radius. No props, state shape, or exported symbols changed.

## User experience effect
"Close company", "Deny allowance request", and "Remove member" now use the standard
destructive token (visually near-identical to the prior fixed red). The five
success/warning-tier documentation edits have zero visual change. No layout or copy
change to any element. Admin-portal-facing only.

## Files modified
| File | What changed | Why |
|---|---|---|
| `src/app/dashboard/corporate-accounts/[id]/page.tsx` | "Close" transition → standard destructive; documented "Suspend"/"Reactivate" no-token/contrast-risk exceptions | #2816 token migration |
| `src/app/dashboard/corporate-accounts/[id]/members/page.tsx` | "Deny"/"Remove member" → standard destructive; documented "Approve" contrast-risk exception and pending-count badge no-token exception | #2816 token migration |

## Before/after snippet
```tsx
// [id]/page.tsx TRANSITIONS.close — before
confirmClass: "bg-red-600 hover:bg-red-700",
// after
confirmClass: "bg-destructive text-destructive-foreground hover:bg-destructive/90",
```

## Rollback plan
Pure CSS class-string revert — `git revert` this commit restores the prior classes with
no data migration, feature flag, or config involved.

## Verification performed
- `npx eslint` on both files: 0 errors (4 pre-existing warnings on `[id]/page.tsx` — 1
  raw-color warning on the intentionally-left blue link, 3 unrelated `react-hooks`
  warnings; `[id]/members/page.tsx`: 1 pre-existing unrelated `react-hooks` warning,
  0 raw-color warnings remaining).
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
- Whether a proper `--warning-foreground` token should eventually be added to
  `globals.css` (so solid-fill warning buttons like "Suspend" and the pending-count
  badge can be converted) is out of scope for this sub-batch — flagged here as a real
  gap rather than silently left unconverted with no explanation.
