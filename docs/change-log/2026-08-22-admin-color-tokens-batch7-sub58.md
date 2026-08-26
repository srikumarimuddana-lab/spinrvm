# Change Impact & Risk Log — Admin color-token migration, batch 7 / sub-batch 58

Part of the #2816 hardcoded-Tailwind-color-token migration (Stage 1). See
`docs/change-log/2026-08-21-admin-color-token-migration-plan.md` for the overall plan.

## Issue/gap identified
- `corporate-accounts/[id]/subscription/page.tsx`'s `STATUS_BADGE` map
  (active/past_due/cancelled) — a genuine 3-state subscription-status signal — used
  fixed light-only Tailwind tints (no dark-mode variant at all), and the "Cancels at
  period end" notice used a fixed amber shade.
- `corporate-accounts/[id]/members/allowance-dialog.tsx`'s error message used a fixed
  `text-red-600`.

## Root cause
Same as prior sub-batches: these components predate the shared `--success`/
`--warning`/`--destructive` tokens. The `STATUS_BADGE` map additionally had no
dark-mode variant at all, so it would have rendered with poor contrast in dark mode
even before this migration.

## Fix/remediation
- `STATUS_BADGE`: `active` (bg-emerald-100/text-emerald-800) → `bg-success/15
  text-success`; `past_due` (bg-amber-100/text-amber-800) → `bg-warning/15
  text-warning`; `cancelled` (bg-slate-100/text-slate-700) → `bg-muted
  text-muted-foreground` — a genuine subscription-lifecycle signal, and the token
  conversion also fixes the pre-existing missing-dark-mode-variant gap since the
  tokens carry their own dark-mode values.
- "Cancels at period end" notice: `text-amber-700 dark:text-amber-400` → `text-warning`.
- `allowance-dialog.tsx` error message: `text-red-600` → `text-destructive`.

Left untouched (established exclusions, confirmed zero-edit-needed): `rides/live/[id]/page.tsx`'s
blue "LIVE" pulsing indicator (established broadcast-indicator exclusion), pickup-marker
emerald dot (established pickup/dropoff-dot exclusion), and driver/rider avatar icon
colors (emerald/blue, decorative entity-type differentiation) — all confirmed matching
prior established exclusions, no changes made to that file. `ride-invoice.tsx`'s jsPDF
`setFillColor`/`setTextColor` RGB calls are PDF-canvas fill colors, not Tailwind
classes, and are out of this migration's scope entirely.

## Risk & impact on existing functionality
Both edited files are standalone leaf pages under `corporate-accounts/[id]/` (no
shared-component blast radius). No props, state shape, or exported symbols changed.

## User experience effect
The `STATUS_BADGE` conversion is a genuine visual improvement (fixes a previously
missing dark-mode variant), not just a token substitution — in dark mode, the
subscription-status badge on this admin-facing corporate subscription page was
previously unstyled for dark mode and now renders correctly. Otherwise, purely color-
token substitutions to visually equivalent (already-approved, contrast-verified)
tokens. Admin-portal-facing only.

## Files modified
| File | What changed | Why |
|---|---|---|
| `src/app/dashboard/corporate-accounts/[id]/subscription/page.tsx` | `STATUS_BADGE` map → `--success`/`--warning`/`--muted`; "Cancels at period end" notice → `--warning` | #2816 token migration (also fixes missing dark-mode variant) |
| `src/app/dashboard/corporate-accounts/[id]/members/allowance-dialog.tsx` | error message → `text-destructive` | #2816 token migration |

## Before/after snippet
```tsx
// subscription/page.tsx — before (no dark: variant at all)
const STATUS_BADGE: Record<string, string> = {
    active: "bg-emerald-100 text-emerald-800",
    past_due: "bg-amber-100 text-amber-800",
    cancelled: "bg-slate-100 text-slate-700",
};
// after
const STATUS_BADGE: Record<string, string> = {
    active: "bg-success/15 text-success",
    past_due: "bg-warning/15 text-warning",
    cancelled: "bg-muted text-muted-foreground",
};
```

## Rollback plan
Pure CSS class-string revert — `git revert` this commit restores the prior hardcoded
classes with no data migration, feature flag, or config involved. Note: reverting also
reintroduces the pre-existing missing-dark-mode-variant gap on `STATUS_BADGE`.

## Verification performed
- `npx eslint` on both edited files: 0 errors, 0 `no-restricted-syntax` warnings (fully
  clean on that rule). One remaining warning is an unrelated pre-existing
  `react-hooks/set-state-in-effect` warning already present before this change.
- `npx vitest run`: 339/339 tests passing across all 35 test files.
- `npm run build` (Turbopack) not re-run — the pre-existing, diff-unrelated
  `@spinr/shared` "Unknown module type" Turbopack failure was already root-caused
  against unmodified `origin/main` in sub-batch 31/PR #4371; this sub-batch is plain
  Tailwind class-string edits with no import/module changes.

## What was NOT verified
- No visual regression tooling exists in this repo for the admin dashboard (standing
  gap, `ACTION_ITEMS.md`) — the `STATUS_BADGE` dark-mode fix in particular was reasoned
  about against previously contrast-verified token values, not screenshotted, so the
  actual rendered dark-mode contrast improvement was not visually confirmed.
- Not tested against a live Supabase/staging deployment — only against the existing
  mocked `vitest` fixtures.
