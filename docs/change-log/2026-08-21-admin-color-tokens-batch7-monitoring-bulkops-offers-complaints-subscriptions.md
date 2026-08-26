# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-21 |
| Author | Claude Code (session, vikas@ngitservices.com) |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | (see PR) |
| Related issue or gap ID | #2816 Stage 1, Batch 7 (sub-batch 5) — see `docs/change-log/2026-08-21-admin-color-token-migration-plan.md` |

## 1. Issue / gap identified

Continuing the #2816 hardcoded-Tailwind-color-token migration. This
sub-batch covers `monitoring/page.tsx` (7 broken lines),
`bulk-operations/_components/LegacyBookingImport.tsx` (7),
`components/analytics/driver-offers-panel.tsx` (7),
`support/_tabs/complaints.tsx` (7), and `subscriptions/page.tsx` (7).

## 2. Root cause / findings

- **`monitoring/page.tsx`**: a "Live Ride Monitoring" header uses a red
  pulsing dot — a standard broadcast/"LIVE" UI convention, not a state
  color; left untouched. A per-ride status badge (4-branch ternary:
  searching/driver_assigned/driver_arrived/fallback) is a ride-lifecycle
  indicator with no `dark:` support at all — `searching`/
  `driver_assigned`/`driver_arrived` each given house-convention `dark:`
  pairing and documented (`eslint-disable-next-line` each, since
  collapsing them would lose which stage a ride is at); the fallback
  branch (which already lumped `driver_accepted`/`in_progress`/
  `completed` together in the original code, so no information is lost
  by tokenizing it) converted to `success`.
- **`bulk-operations/_components/LegacyBookingImport.tsx`**: a `Stat`
  component's `tone="error"`/`tone="warn"` props map directly to
  `destructive`/`warning`. An info-banner icon (already inside a `dark:`-
  aware container) given a matching `dark:text-amber-400` pairing rather
  than switching to a token, to stay visually consistent with its raw-
  amber-themed container. 4 more real fixes: a file-selected checkmark,
  an error-count line, a warning-count line, and a commit-success
  checkmark (all plain text/icons, not solid-fill) → `success`/
  `destructive`/`warning` tokens.
- **`components/analytics/driver-offers-panel.tsx`**: a 3-item KPI row
  (Accepted/Declined/Ignored) mirrors real dispatch-offer outcomes 1:1 →
  `success`/`destructive`/`warning`. The per-driver table columns for
  the same 3 outcomes, plus an `is_online` dot, converted to match —
  the `is_online` dot now uses the exact same `bg-success` convention
  already established in `drivers/page.tsx`.
- **`support/_tabs/complaints.tsx`**: `S_CFG` (4-state: open/
  investigating/resolved/dismissed) — `open` → `warning`, `resolved` →
  `success`, `dismissed` → `muted`; `investigating` (in-progress, neither
  good nor bad) documented with `eslint-disable-next-line` since it must
  stay visually distinct from the other three. Left untouched: a
  decorative `FileWarning` dialog-title icon and 2 solid-fill white-text
  buttons (Resolve, Delete — contrast-risk exclusion).
- **`subscriptions/page.tsx`**: a 3-card KPI row (Active/Expired/
  Cancelled) — `Active` → `success` (icon + count), `Cancelled` →
  `destructive` (icon + count), `Expired` → `muted` (a lapsed-but-not-
  wrongdoing state, same treatment as `quests/page.tsx`'s `expired` and
  `disputes/chargebacks-tab.tsx`'s `lost` from earlier sub-batches). A
  delete icon-button (outline, not solid-fill) → `destructive`.

## 3. Fix / remediation

18 real semantic-token fixes, 4 house-convention `dark:` pairing
additions (3 ride-lifecycle branches + 1 info-banner icon), 4 documented
suppressions (`searching`/`driver_assigned`/`driver_arrived` in
`monitoring/page.tsx`, `investigating` in `complaints.tsx`). Remainder
deliberately left as decorative/contrast-risk per the established
per-line rule.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to these 5 files.** No shared component,
  hook, or utility was touched. `S_CFG`, the `Stat` tone prop, and the
  KPI arrays are each local to their own file.
- All converted lines are outline buttons, plain text, icons, badges, or
  table cells — none were part of the excluded solid-fill white-text
  button/badge class (each individually checked).
- Repo-wide lint warning count stayed well under the `--max-warnings`
  ratchet (1447 vs. the 1751 ceiling).

## 5. User-experience effect

**Internal admin only.** Visual effect is a shade shift on already-
semantically-colored icons, badges, and KPI numbers — no icon, label,
or layout change, no change to which rides/complaints/subscriptions are
shown or how they're filtered/sorted.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/monitoring/page.tsx` | Ride-status badge (3 states given dark: + documented, 1 fallback → success) | #2816 Batch 7 |
| `admin-dashboard/src/app/dashboard/bulk-operations/_components/LegacyBookingImport.tsx` | `Stat` tone prop, info-icon dark: pairing, 4 icon/text colors → tokens | #2816 Batch 7 |
| `admin-dashboard/src/components/analytics/driver-offers-panel.tsx` | 3-item KPI row + per-driver table columns + is_online dot → tokens | #2816 Batch 7 |
| `admin-dashboard/src/app/dashboard/support/_tabs/complaints.tsx` | `S_CFG` (2/4 states converted, 1 documented) → tokens | #2816 Batch 7 |
| `admin-dashboard/src/app/dashboard/subscriptions/page.tsx` | 3-card KPI row + delete icon → tokens | #2816 Batch 7 |

## 7. Before / after

```tsx
// components/analytics/driver-offers-panel.tsx — KPI row (before)
{ label: "Accepted", value: totals.accepted ?? 0, Icon: CheckCircle, cls: "text-emerald-600" },
{ label: "Declined", value: totals.declined ?? 0, Icon: XCircle, cls: "text-red-600" },
{ label: "Ignored", value: totals.ignored ?? 0, Icon: Clock, cls: "text-amber-600" },

// after
{ label: "Accepted", value: totals.accepted ?? 0, Icon: CheckCircle, cls: "text-success" },
{ label: "Declined", value: totals.declined ?? 0, Icon: XCircle, cls: "text-destructive" },
{ label: "Ignored", value: totals.ignored ?? 0, Icon: Clock, cls: "text-warning" },
```

```tsx
// support/_tabs/complaints.tsx — S_CFG (before)
const S_CFG: Record<string, { l: string; c: string }> = {
    open: { l: "Open", c: "bg-amber-500/15 text-amber-600" },
    investigating: { l: "Investigating", c: "bg-blue-500/15 text-blue-600" },
    resolved: { l: "Resolved", c: "bg-emerald-500/15 text-emerald-600" },
    dismissed: { l: "Dismissed", c: "bg-zinc-500/15 text-zinc-600" },
};

// after
const S_CFG: Record<string, { l: string; c: string }> = {
    open: { l: "Open", c: "bg-warning/15 text-warning" },
    // eslint-disable-next-line no-restricted-syntax -- "investigating" (in progress, neither good nor bad) has no semantic-token equivalent; must stay distinct from open/resolved/dismissed (#2816)
    investigating: { l: "Investigating", c: "bg-blue-500/15 text-blue-600" },
    resolved: { l: "Resolved", c: "bg-success/15 text-success" },
    dismissed: { l: "Dismissed", c: "bg-muted text-muted-foreground" },
};
```

## 8. Rollback plan

`git-revert-safe` — 5 files, all `className` string literals and
documentation comments. No data/API/schema change, no shared-component
change.

## 9. Verification performed

- [x] `npx eslint` on all 5 touched files — 0 errors, 31 warnings (all
  pre-existing, mostly a `react-hooks/set-state-in-effect` warning and
  the expected residual raw-color warnings on deliberately-excluded
  lines).
- [x] `npx tsc --noEmit` — clean, 0 errors.
- [x] `npx vitest run` — 339/339 passed.
- [x] `npm run lint` (the exact CI command) — exit 0, 1447 total
  warnings (under the 1751 ratchet).
- [x] `npm run build` (real production build) — succeeded.
- [ ] Not manually click-tested/screenshotted — same standing sandbox
  limitation as every prior change-log this session; visual-regression
  baseline still not seeded.

## 10. Sign-off

- [x] Rollback plan is concrete and testable.
- [x] Blast radius is stated per file, not assumed.
- [x] No silent behavior change — every conversion is a color-only
  visual change on an already-semantically-meaningful element; all
  click handlers, filters, and conditional rendering are unchanged.
- [x] Ride-lifecycle states given dark-mode support for the first time
  in `monitoring/page.tsx` — a real, if minor, dark-mode bug fixed, not
  just a token substitution.
