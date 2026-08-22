# Change Impact & Risk Log — #2816 Batch 7 sub-batch 20: driver timeline, ride stats cards, document reviewer, demand forecast

## Issue/gap identified
Four more admin-dashboard files still used raw Tailwind color utilities for status maps and
single-signal indicators instead of the semantic tokens.

## Root cause
Pre-dates the token migration; written against literal Tailwind palette classes.

## Fix/remediation
- `drivers/_components/driver-timeline.tsx`: `EVENT_CONFIG` (19 distinct driver-activity event
  kinds: registered, document uploads/approvals, suspend/ban/reactivate, online/offline, notes,
  subscription events, etc.) documented with a block `eslint-disable`/`eslint-enable` comment —
  the same "categorical map, too many states for 3 tokens" treatment used elsewhere in this
  migration.
- `dashboard/rides/_components/ride-stats-cards.tsx`: the "Platform Net (post-promo)" revenue
  card's conditional color (red when negative, emerald when positive — a genuine binary
  positive/negative signal) → `text-destructive`/`bg-destructive/15` and
  `text-success`/`bg-success/15`. Left the rest of the file's ~15 `StatCard`/`RevenueCard` accent
  colors untouched — a decorative multi-column KPI/revenue-breakdown differentiation row (blue,
  violet, teal, pink, slate, indigo, etc., each purely to distinguish columns), the same exclusion
  applied to every other multi-stat KPI row in this migration.
- `drivers/_components/document-reviewer.tsx`: `statusTone()` (approved/rejected/pending — a
  clean 3-state fit) → `bg-success/15 text-success` / `bg-destructive/15 text-destructive` /
  `bg-warning/15 text-warning`; the inline error banner → `bg-destructive/10 border-destructive/30
  text-destructive`; the approve-panel and reject-panel containers → `border-success/30
  bg-success/10` and `border-destructive/30 bg-destructive/10`. Left the solid-fill
  emerald/red Approve/Reject submit buttons untouched (contrast-risk exclusion) and the blue
  "notify" `Bell` icon untouched (informational toggle indicator, not a signal).
- `components/analytics/demand-forecast-panel.tsx`: `DATA_BASIS_COLORS` (historical_average /
  limited_history / default_pattern — a clean 3-state data-quality map) → `bg-success/15
  text-success` / `bg-warning/15 text-warning` / `bg-muted text-muted-foreground`, plus its
  fallback default (`|| "bg-gray-100 dark:bg-gray-800"` → `|| "bg-muted"`). Left the "Next Peak" /
  "24h Total" stat-label icons and the peak-hour row highlight untouched — a consistent decorative
  "peak" emphasis color used throughout the same panel, not a warning of a problem.

Also verified, no change needed: `drivers/_components/driver-stats-cards.tsx` — its ~9 `StatCard`
accent colors are the same decorative multi-column KPI differentiation pattern as
`ride-stats-cards.tsx`'s untouched portion; no edits made.

## Risk & impact on existing functionality
Color-only class swaps; no logic, props, or data flow changed.
- `EVENT_CONFIG`, `statusTone()`, and `DATA_BASIS_COLORS` are each local `Record`/function maps in
  their own component, not shared.
- The `ride-stats-cards.tsx` change touches only the one conditional revenue-card color; the
  `platformAfterNeg` boolean that decides *which* branch applies is untouched.

## User experience effect
All four files are internal-admin-only screens (driver activity timeline, ride revenue stats,
driver document review, demand-forecast analytics). Purely cosmetic — the underlying event
classification, approval-status logic, and revenue sign calculation are unchanged.

## Files modified
| File | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/drivers/_components/driver-timeline.tsx` | `EVENT_CONFIG` documented as an intentional categorical exclusion | #2816 |
| `admin-dashboard/src/app/dashboard/rides/_components/ride-stats-cards.tsx` | "Platform Net" positive/negative revenue card → destructive/success tokens | #2816 |
| `admin-dashboard/src/app/dashboard/drivers/_components/document-reviewer.tsx` | `statusTone()` + error banner + approve/reject panels → success/warning/destructive tokens | #2816 |
| `admin-dashboard/src/components/analytics/demand-forecast-panel.tsx` | `DATA_BASIS_COLORS` + fallback → success/warning/muted tokens | #2816 |

## Before/after snippet
```tsx
// document-reviewer.tsx statusTone() — before
case "approved": return "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300";
case "rejected": return "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-300";
case "pending": return "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300";
// after
case "approved": return "bg-success/15 text-success";
case "rejected": return "bg-destructive/15 text-destructive";
case "pending": return "bg-warning/15 text-warning";
```

## Rollback plan
Pure CSS class revert (plus one added lint-suppression block) — `git revert` this commit; no data
migration, flag, or config change.

## Verification performed
- `npx eslint` on all 4 changed files: 0 errors, 38 warnings (pre-existing unrelated
  `react-hooks/set-state-in-effect`/`exhaustive-deps` advisories, plus the deliberately-left
  raw-color warnings on the multi-column KPI stat rows in `ride-stats-cards.tsx` and
  `driver-stats-cards.tsx`, the solid-fill approve/reject buttons, and the peak-hour decorative
  highlight).
- `npx tsc --noEmit`: clean.
- `npx vitest run`: 35 files / 339 tests passed.
- `npm run build`: **production build completed successfully**.

## What was NOT verified
No visual-regression tooling exists in this repo (standing gap). Colors were reasoned about
against the existing token definitions, not screenshotted. Not tested against a live
Supabase-backed admin session.
