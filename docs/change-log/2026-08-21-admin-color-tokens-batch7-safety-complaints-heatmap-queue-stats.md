# Change Impact & Risk Log — #2816 Batch 7 sub-batch 22: safety incidents, complaints tab, heatmap, queue stats

## Issue/gap identified
Four more admin-dashboard files still used raw Tailwind color utilities for status/severity maps
and single-signal indicators instead of the semantic tokens.

## Root cause
Pre-dates the token migration; written against literal Tailwind palette classes.

## Fix/remediation
- `dashboard/safety/page.tsx`: `statusTone()` (open/in_progress/resolved — closed/duplicate were
  already on muted tokens) → `bg-destructive/15 text-destructive` / `bg-warning/15 text-warning` /
  `bg-success/15 text-success`; `severityTone()`'s sev1/sev2 → destructive/warning (sev3, the
  lowest tier with no clean token fit, kept its raw blue with a new
  `eslint-disable-next-line` documenting why — the same treatment already applied to
  `support/_tabs/complaints.tsx`'s "investigating" state); the page-header `Shield` icon (red,
  intentionally safety-severity-toned) → `text-destructive`; the incident-timeline "Resolved"
  confirmation line → `text-success`. Left the pickup/dropoff address-list dots (emerald/red)
  untouched — the established fixed pickup/dropoff UI convention used elsewhere in this migration
  — and the solid-fill emerald action button (contrast-risk exclusion).
- `dashboard/support/_tabs/complaints.tsx`: the "Review Complaint" dialog's `FileWarning` icon
  (amber) → `text-warning`. Its `S_CFG` status map was already fully converted/documented in an
  earlier pass (open/resolved/dismissed on tokens, "investigating" already carrying its own
  `eslint-disable-next-line`) — verified, no further work needed there. Left the solid-fill
  emerald "Resolve" and red "Delete" buttons untouched (contrast-risk exclusion).
- `dashboard/heatmap/page.tsx`: the "surge active" area badge (amber) → `bg-warning/10
  text-warning border-warning/30` — a single warning-severity signal (surge is active on this
  area, worth an admin's attention). Left the peak/off-peak `TrendingUp`/`TrendingDown` stat icons
  and the peak-hour bar-chart coloring (orange) untouched — the same consistent decorative "peak"
  emphasis theme already established for `demand-forecast-panel.tsx` in an earlier sub-batch, not
  a warning of a problem.
- `drivers/queue/_components/queue-stats.tsx`: the wait-time tone map (mirrors `slaTone()` in the
  parent `queue/page.tsx`, already converted in an earlier sub-batch: ≥24h / ≥4h / under →
  red/amber/emerald) → `bg-destructive/15 text-destructive` / `bg-warning/15 text-warning` /
  `bg-success/15 text-success`. Left the "Pending" tile's blue accent untouched (with a new
  documenting comment) — it's a plain count, not a tiered signal, so it has no destructive/
  warning/success equivalent.

Verified, no change needed: `dashboard/monitoring/toolbar.tsx` — its Online/On-Ride/Offline/
Rides/Demand filter-toggle chips are a 5-category filter-differentiation row (Online and Offline
were already converted to success/muted tokens in an earlier pass; On Ride/Rides/Demand's amber/
blue/orange are categorical, not signals) — no edits made.

## Risk & impact on existing functionality
Color-only class swaps; no logic, props, or data flow changed.
- `statusTone()`/`severityTone()` are local to `safety/page.tsx`; `S_CFG` is local to
  `complaints.tsx`; the queue-stats tone map mirrors (but is a separate copy of) the parent
  page's `slaTone()` — both were kept in sync with the same threshold-to-color mapping.
- The heatmap surge badge is local to that page's area-card rendering.

## User experience effect
All four files are internal-admin-only screens (safety-incident triage, support complaints tab,
service-area heatmap, driver-approval queue stats). Purely cosmetic color change — the underlying
status/severity classification and surge-detection logic are unchanged.

## Files modified
| File | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/safety/page.tsx` | `statusTone()`/`severityTone()` + header icon + resolved-confirmation → destructive/warning/success tokens | #2816 |
| `admin-dashboard/src/app/dashboard/support/_tabs/complaints.tsx` | Review-dialog warning icon → warning token | #2816 |
| `admin-dashboard/src/app/dashboard/heatmap/page.tsx` | "Surge active" badge → warning token | #2816 |
| `admin-dashboard/src/app/dashboard/drivers/queue/_components/queue-stats.tsx` | Wait-time tone map → destructive/warning/success tokens | #2816 |

## Before/after snippet
```tsx
// safety/page.tsx statusTone() — before
case "open": return { bg: "bg-red-100 dark:bg-red-900/30", text: "text-red-700 dark:text-red-300", label: "Open" };
// after
case "open": return { bg: "bg-destructive/15", text: "text-destructive", label: "Open" };
```

## Rollback plan
Pure CSS class revert (plus one added lint-suppression comment on the sev3/blue exception) —
`git revert` this commit; no data migration, flag, or config change.

## Verification performed
- `npx eslint` on all 4 changed files: 0 errors, 20 warnings (pre-existing unrelated advisories,
  plus the deliberately-left raw-color warnings on the pickup/dropoff dots, solid-fill buttons,
  and decorative peak/trend icons).
- `npx tsc --noEmit`: clean.
- `npx vitest run`: 35 files / 339 tests passed.
- `npm run build`: **production build completed successfully**.

## What was NOT verified
No visual-regression tooling exists in this repo (standing gap). Colors were reasoned about
against the existing token definitions, not screenshotted. Not tested against a live
Supabase-backed admin session.
