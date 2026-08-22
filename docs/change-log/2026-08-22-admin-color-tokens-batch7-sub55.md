# Change Impact & Risk Log — Admin color-token migration, batch 7 / sub-batch 55

Part of the #2816 hardcoded-Tailwind-color-token migration (Stage 1). See
`docs/change-log/2026-08-21-admin-color-token-migration-plan.md` for the overall plan.

## Issue/gap identified
- `drivers/decals/page.tsx`: the welcome-letter "Done" completion indicator used a fixed
  `text-emerald-500` instead of the `--success` token.
- `drivers/queue/_components/queue-stats.tsx`: a genuine threshold-driven tone system
  (median-wait / oldest-in-queue / over-24h counters flip red/amber/emerald based on
  SLA thresholds) used fixed Tailwind shades instead of `--destructive`/`--warning`/`--success`.
- `disputes/page.tsx`: the resolved-dispute summary block used fixed green shades.
- `drivers/appeals/page.tsx`: the approved/denied appeal summary block used fixed
  green/red shades.

## Root cause
Same as prior sub-batches: these components predate the shared semantic tokens.

## Fix/remediation
- `decals/page.tsx`: `text-emerald-500` → `text-success` on the "Done" checkmark — a
  genuine completion signal, not decorative.
- `queue-stats.tsx`: `emerald`/`amber`/`red` tone classes converted to
  `bg-success/15 text-success` / `bg-warning/15 text-warning` / `bg-destructive/15
  text-destructive` respectively. The `blue` tone (used only for the always-present,
  non-signal "Pending" count) is left as a hand-picked color and documented with an
  `eslint-disable-next-line` — no semantic token exists for a neutral-informational tone.
- `disputes/page.tsx`: the resolved-dispute block (`bg-green-50 dark:bg-green-950/30`,
  `text-green-700 dark:text-green-400`) → `bg-success/10`, `text-success`.
- `appeals/page.tsx`: the approved/denied block converted per branch —
  `bg-green-50`/`text-green-700` → `bg-success/10`/`text-success` for "approved";
  `bg-red-50`/`text-red-700` → `bg-destructive/10`/`text-destructive` for "denied".

Left untouched (established exclusions, consistent with prior sub-batches): the amber
`AlertTriangle`/`Gavel` decorative header and dialog-title icons in `disputes/page.tsx`
and `appeals/page.tsx`.

## Risk & impact on existing functionality
All four files are standalone leaf pages/components (`queue-stats.tsx` is consumed only
by the drivers-queue approval page) — no shared-component blast radius beyond that one
known consumer, which passes only `stats` data, never raw classes. No props, state
shape, or exported symbols changed.

## User experience effect
Purely color-token substitutions to visually equivalent (already-approved,
contrast-verified) tokens. The `queue-stats.tsx` conversion preserves the same
threshold-driven red/amber/emerald behavior — a driver-approvals SLA signal — under
both light and dark themes. No layout, copy, or behavior change. Admin-portal-facing
only.

## Files modified
| File | What changed | Why |
|---|---|---|
| `src/app/dashboard/drivers/decals/page.tsx` | "Done" checkmark → `text-success` | #2816 token migration |
| `src/app/dashboard/drivers/queue/_components/queue-stats.tsx` | tone system → `--success`/`--warning`/`--destructive` (blue tone documented as an intentional exception) | #2816 token migration |
| `src/app/dashboard/disputes/page.tsx` | resolved-dispute block → `bg-success/10`/`text-success` | #2816 token migration |
| `src/app/dashboard/drivers/appeals/page.tsx` | approved/denied block → `bg-success/10`/`text-success` or `bg-destructive/10`/`text-destructive` | #2816 token migration |

## Before/after snippet
```tsx
// queue-stats.tsx — before
emerald: "bg-emerald-50 dark:bg-emerald-900/20 text-emerald-700 dark:text-emerald-300",
amber: "bg-amber-50 dark:bg-amber-900/20 text-amber-700 dark:text-amber-300",
red: "bg-red-50 dark:bg-red-900/20 text-red-700 dark:text-red-300",
// after
emerald: "bg-success/15 text-success",
amber: "bg-warning/15 text-warning",
red: "bg-destructive/15 text-destructive",
```
```tsx
// appeals/page.tsx — before
<div className={`rounded-lg p-3 space-y-1 ${selected.status === "approved" ? "bg-green-50 dark:bg-green-950/30" : "bg-red-50 dark:bg-red-950/30"}`}>
// after
<div className={`rounded-lg p-3 space-y-1 ${selected.status === "approved" ? "bg-success/10" : "bg-destructive/10"}`}>
```

## Rollback plan
Pure CSS class-string revert (plus removing the `eslint-disable-next-line` comment in
`queue-stats.tsx`, which changes no runtime behavior) — `git revert` this commit
restores the prior hardcoded classes with no data migration, feature flag, or config
involved.

## Verification performed
- `npx eslint` on all four edited files: 0 errors. Remaining warnings are all
  pre-existing/expected: the decorative header-icon exclusions (disputes/appeals),
  unrelated pre-existing `react-hooks` warnings, pre-existing `jsx-a11y` label warnings,
  and a pre-existing unescaped-apostrophe warning in `appeals/page.tsx` — none
  introduced by this change.
- `npx vitest run`: 339/339 tests passing across all 35 test files.
- `npm run build` (Turbopack) not re-run — the pre-existing, diff-unrelated
  `@spinr/shared` "Unknown module type" Turbopack failure was already root-caused
  against unmodified `origin/main` in sub-batch 31/PR #4371; this sub-batch is plain
  Tailwind class-string edits with no import/module changes.

## What was NOT verified
- No visual regression tooling exists in this repo for the admin dashboard (standing
  gap, `ACTION_ITEMS.md`) — token substitutions, including the queue-stats SLA
  threshold coloring, were reasoned about against previously contrast-verified token
  values, not screenshotted.
- Not tested against a live Supabase/staging deployment — only against the existing
  mocked `vitest` fixtures.
