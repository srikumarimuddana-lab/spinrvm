# Change Impact & Risk Log — #2816 Batch 7 sub-batch 17: referral leaderboard, KPI tile, ticket detail, referral pairs

## Issue/gap identified
Four more admin-dashboard files still used raw Tailwind color utilities for single-signal
success/warning/destructive indicators and status maps instead of the semantic tokens.

## Root cause
Pre-dates the token migration; written against literal Tailwind palette classes.

## Fix/remediation
- `components/referral-leaderboard.tsx`: fetch-error banner (`text-red-600 dark:text-red-400`) →
  `text-destructive`; the "Qualified" leaderboard-table column (`text-emerald-600
  dark:text-emerald-400`) → `text-success` — a genuine single positive-signal count standing next
  to two uncolored columns (Referrals, Earned), not a multi-category differentiation. Left the
  3-stat `SummaryCard` row (violet/sky/emerald) and the decorative `Trophy` icon untouched — the
  former is a multi-column KPI differentiation row, the latter a fixed decorative convention
  (trophy = gold/amber), neither a success/warning/destructive signal.
- `components/analytics/kpi-tile.tsx`: `StatTile`'s `tone` prop (good/warn/bad, a genuine 3-state
  enum) → `text-success`/`text-warning`/`text-destructive`; `KpiCard`'s meeting/below-target border
  and value color → `border-warning` / `text-success`/`text-warning`; `SampleNote`'s small-sample
  `Minus` icon → `text-warning`.
- `dashboard/support-tickets/tickets/[id]/page.tsx`: the "needs assignment" service-area warning
  card (border+background) and its warning text → `border-warning bg-warning/10` / `text-warning`.
  Left untouched (verified, no change needed): the already-documented `statusClass()` "open" state
  (pre-existing `eslint-disable-next-line` from an earlier pass); the message-thread accent border
  (comment/agent-reply/customer, a 3-way categorical origin differentiation, not a signal); and the
  "show full message" / rendered-content links (informational blue, already dark-aware).
- `components/referral-pairs.tsx`: `STATUS_COLOR` (paid/processing/failed/expired — `expired` was
  already on the muted token from an earlier pass) → completed the map: `paid` → `text-success`,
  `processing` → `text-warning` (consistent with the "in-progress/under-review → warning" precedent
  used for dispute/appeal status maps elsewhere in this migration), `failed` → `text-destructive`.

## Risk & impact on existing functionality
Color-only class swaps; no logic, props, or data flow changed.
- `kpi-tile.tsx`'s `StatTile`/`KpiCard`/`SampleNote` are shared exports — grepped their importers:
  used across the marketplace-KPI dashboard tabs (dispatch/fare/safety KPI panels). Only the color
  classes changed, not the components' props or rendering structure, so every caller renders
  identically apart from the token-driven color.
- `referral-leaderboard.tsx`, `referral-pairs.tsx`, and the ticket-detail page changes are each
  local to their own file.

## User experience effect
All four files are internal-admin-only screens (referral analytics, KPI dashboards, support
ticket detail, referral ledger). Purely cosmetic color change — the underlying tone/status logic
and labels are unchanged, and the KPI tiles' pass/fail semantics still ship with an icon and
spelled-out target text (per the file's own existing "never colour-alone" comment), unaffected by
this change.

## Files modified
| File | What changed | Why |
|---|---|---|
| `admin-dashboard/src/components/referral-leaderboard.tsx` | Error banner + "Qualified" column → destructive/success tokens | #2816 |
| `admin-dashboard/src/components/analytics/kpi-tile.tsx` | `tone` prop + KPI-card border/value + sample-size icon → tokens | #2816 |
| `admin-dashboard/src/app/dashboard/support-tickets/tickets/[id]/page.tsx` | "Needs assignment" warning card → warning token | #2816 |
| `admin-dashboard/src/components/referral-pairs.tsx` | `STATUS_COLOR` completed on tokens (paid/processing/failed) | #2816 |

## Before/after snippet
```tsx
// kpi-tile.tsx StatTile — before
tone === "good" ? "text-emerald-600 dark:text-emerald-400"
: tone === "warn" ? "text-amber-600 dark:text-amber-400"
: tone === "bad" ? "text-red-600 dark:text-red-400"
: "text-foreground";
// after
tone === "good" ? "text-success"
: tone === "warn" ? "text-warning"
: tone === "bad" ? "text-destructive"
: "text-foreground";
```

## Rollback plan
Pure CSS class revert — `git revert` this commit; no data migration, flag, or config change.

## Verification performed
- `npx eslint` on all 4 changed files: 0 errors, 10 warnings (pre-existing unrelated
  `react-hooks/set-state-in-effect` advisories, plus the deliberately-left raw-color warnings on
  the multi-column KPI stat row, the message-thread accent border, and the decorative icons).
- `npx tsc --noEmit`: clean.
- `npx vitest run`: 35 files / 339 tests passed.
- `npm run build`: **production build completed successfully**.

## What was NOT verified
No visual-regression tooling exists in this repo (standing gap). Colors were reasoned about
against the existing token definitions, not screenshotted. Not tested against a live
Supabase-backed admin session.
