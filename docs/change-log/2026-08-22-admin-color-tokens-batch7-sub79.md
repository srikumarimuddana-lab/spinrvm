# Change Impact & Risk Log — Admin color-token migration, batch 7 / sub-batch 79

Part of the #2816 hardcoded-Tailwind-color-token migration (Stage 1). See
`docs/change-log/2026-08-21-admin-color-token-migration-plan.md` for the overall plan.

## Issue/gap identified
Nine `no-restricted-syntax` raw-Tailwind-color lint warnings had no documenting
`eslint-disable-next-line`/block comment yet, across three monitoring/heatmap files:
- `monitoring/page.tsx`: the "Live Ride Monitoring" header's Radio icon + pulsing dot
  (3 raw-color literals).
- `heatmap/page.tsx`: two KPI-card header icons ("Active Demand" TrendingUp,
  "Surge Active" TrendingDown) and the demand-forecast bar-chart fill (2 literals in
  one ternary).
- `monitoring/driver-panel.tsx`: the star-rating icon and the "Current Ride" link-card
  accent.

## Root cause
All nine are decorative/conventional UI accents, not success/warning/destructive
signals: a standard "live broadcast" red pulsing-dot convention, KPI-card icon tints,
a single-hue bar-chart intensity fill, the star-rating amber convention (already
established as a documented exception elsewhere in this migration), and a link-card
accent color. None was ever a genuine convertible signal, but none had been given the
inline documentation this migration uses to mark "reviewed and left" vs. "not yet
reviewed."

## Fix/remediation
Documented all nine with `eslint-disable-next-line`/block comments explaining why each
is decorative rather than a status signal. No color values changed anywhere in this
diff — comment-only.

## Risk & impact on existing functionality
Comment-only diff — no className/JSX values changed. Grepped for other importers:
`monitoring/page.tsx` and `heatmap/page.tsx` are standalone route pages;
`driver-panel.tsx` is imported only by `monitoring/page.tsx`. Zero blast radius beyond
the three files touched.

## User experience effect
None — no visual change. Internal-admin-only surfaces (live monitoring, heatmap).

## Files modified
| File | What changed | Why |
|---|---|---|
| `src/app/dashboard/monitoring/page.tsx` | Documented "Live" header red-dot convention (3 literals) | #2816 token migration |
| `src/app/dashboard/heatmap/page.tsx` | Documented 2 KPI-card icon accents + 1 bar-chart fill (2 literals) | #2816 token migration |
| `src/app/dashboard/monitoring/driver-panel.tsx` | Documented star-rating icon + link-card accent | #2816 token migration |

## Before/after snippet
```tsx
// heatmap/page.tsx bar-chart fill — before
<div
    className={`w-full rounded-t transition-all ${slot.isPeak ? "bg-orange-600" : "bg-orange-500/70"}`}
// after
<div
    // eslint-disable-next-line no-restricted-syntax -- forecast bar-chart fill, single-hue intensity accent not a success/warning/destructive signal (#2816)
    className={`w-full rounded-t transition-all ${slot.isPeak ? "bg-orange-600" : "bg-orange-500/70"}`}
```

## Rollback plan
Pure comment addition — `git revert` this commit restores the prior (undocumented but
functionally identical) files with no data migration, feature flag, or config involved.

## Verification performed
- `npx eslint` on all 3 touched files: 0 errors; all 9 targeted `no-restricted-syntax`
  raw-color warnings are gone (now suppressed with documented reasons, verified with no
  "unused eslint-disable directive" warnings remaining); remaining warnings are
  pre-existing, unrelated `react-hooks` warnings.
- `npx vitest run`: 339/339 tests passing across all 35 test files.
- `npm run build` (Turbopack) not re-run — pure comment-only diff, no import/module
  changes; the pre-existing, diff-unrelated `@spinr/shared` Turbopack failure was
  already root-caused against unmodified `origin/main` in sub-batch 31/PR #4371.

## What was NOT verified
- No visual regression tooling exists in this repo for the admin dashboard (standing
  gap, `ACTION_ITEMS.md`) — not applicable here since no className changed.
- Not tested against a live Supabase/staging deployment.
