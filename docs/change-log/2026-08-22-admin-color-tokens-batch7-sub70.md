# Change Impact & Risk Log — Admin color-token migration, batch 7 / sub-batch 70

Part of the #2816 hardcoded-Tailwind-color-token migration (Stage 1). See
`docs/change-log/2026-08-21-admin-color-token-migration-plan.md` for the overall plan.

## Issue/gap identified
The live-monitoring dashboard components (`driver-panel.tsx`, `ride-panel.tsx`,
`toolbar.tsx`) had a few remaining genuine status/warning signals on fixed Tailwind
shades: a "searching for a driver" warning banner, a "Complete Ride" success action
button, and an "On Ride" live-count filter chip.

## Root cause
These sections predate the shared `--success`/`--warning`/`--destructive` tokens.

## Fix/remediation
- `ride-panel.tsx`: "Searching for a driver…" banner (dashed-border, no solid-fill
  contrast risk) → `border-warning/40 bg-warning/10 text-warning`. "Complete Ride"
  outline button → `text-success border-success/40 hover:bg-success/10`.
- `toolbar.tsx`: "On Ride" live-count chip → `bg-warning/10 text-warning ring-warning/30`
  (dot → `bg-warning`), consistent with the adjacent "Online" chip already on
  `--success` and "Offline" already on `--muted-foreground`.
- `driver-panel.tsx`: added a documenting `eslint-disable-next-line` to the existing
  solid-fill "Online" badge (`bg-green-500`, white text) — this was already a
  deliberate contrast-risk exception (dark-mode `--success` at 2.02:1 fails WCAG AA
  against white text) but lacked the inline comment other files in this migration use
  to record the reasoning; no color change.

Left untouched (decorative/no-token-equivalent, confirmed by review not silently
skipped):
- `ride-panel.tsx`'s `STATUS_COLORS` (searching/driver_assigned/driver_arrived/
  in_progress) — already a documented solid-fill contrast-risk + no-token-equivalent
  exception from a prior batch.
- `ride-panel.tsx`'s purple driver-avatar/hover accents — decorative driver-identity
  color, same exclusion pattern as rider/driver identity colors elsewhere.
- `driver-panel.tsx`'s amber star-rating fill and blue "view active ride" link button —
  decorative rating/link conventions, not status signals.
- `toolbar.tsx`'s "Rides" (blue) and "Demand" (orange) filter chips — each now carries
  an `eslint-disable-next-line`; they're filter-category toggles (not status signals)
  with no semantic token equivalent, distinguishing one filter from another the same
  way the existing "Online"/"Offline"/"On Ride" chips form one categorical set.

## Risk & impact on existing functionality
All edits are within `app/dashboard/monitoring/{driver-panel,ride-panel,toolbar}.tsx`.
Grepped for other importers: all three are only used by `app/dashboard/monitoring/
page.tsx` (the live-monitoring dashboard). No shared-hook or shared-component blast
radius beyond that one page. No props, state shape, or exported symbols changed.

## User experience effect
Visually equivalent color substitutions (bg/text pairs map to the same hue family at
equivalent opacity), plus one purely-documentary comment addition with zero visual
change. No layout, copy, or behavior change. Admin-portal-facing only, on the live
monitoring dashboard.

## Files modified
| File | What changed | Why |
|---|---|---|
| `src/app/dashboard/monitoring/ride-panel.tsx` | "Searching for a driver" banner, "Complete Ride" button → `--warning`/`--success` | #2816 token migration |
| `src/app/dashboard/monitoring/toolbar.tsx` | "On Ride" chip → `--warning`; documented "Rides"/"Demand" chip exceptions | #2816 token migration |
| `src/app/dashboard/monitoring/driver-panel.tsx` | Documented existing "Online" badge contrast-risk exception (no color change) | #2816 token migration |

## Before/after snippet
```tsx
// ride-panel.tsx "Complete Ride" button — before
className="w-full gap-1.5 text-xs text-green-600 dark:text-green-400 border-green-600 dark:border-green-700 hover:bg-green-50 dark:hover:bg-green-900/20"
// after
className="w-full gap-1.5 text-xs text-success border-success/40 hover:bg-success/10"
```

## Rollback plan
Pure CSS class-string revert — `git revert` this commit restores the prior classes with
no data migration, feature flag, or config involved.

## Verification performed
- `npx eslint` on all three files: 0 errors, 11 pre-existing warnings across
  `driver-panel.tsx`/`ride-panel.tsx` (documented decorative exceptions +
  unrelated `react-hooks` warnings); `toolbar.tsx`: 0 problems.
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
