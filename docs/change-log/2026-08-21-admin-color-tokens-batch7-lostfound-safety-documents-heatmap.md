# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-21 |
| Author | Claude Code (session, vikas@ngitservices.com) |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | (see PR) |
| Related issue or gap ID | #2816 Stage 1, Batch 7 (sub-batch 7) — see `docs/change-log/2026-08-21-admin-color-token-migration-plan.md` |

## 1. Issue / gap identified

Continuing the #2816 hardcoded-Tailwind-color-token migration. This
sub-batch covers `support/_tabs/lost-and-found.tsx` (6 broken lines),
`rides/_components/ride-lost-found.tsx` (6), `safety/page.tsx` (5),
`drivers/_components/document-reviewer.tsx` (5), and `heatmap/page.tsx`
(5).

## 2. Root cause / findings

- **`support/_tabs/lost-and-found.tsx`** and **`rides/_components/
  ride-lost-found.tsx`** (an identical 4-state map, duplicated between
  the admin lost-and-found tab and the per-ride lost-and-found widget,
  confirmed independently defined in each): `reported` → `warning`,
  `resolved` → `success`, `unresolved` → `destructive`;
  `driver_notified` (in-progress, neither good nor bad) documented with
  `eslint-disable-next-line` in both files. A resolve-action ghost-
  button icon and two tinted resolve/unresolve buttons (not solid-fill)
  converted to match. Left untouched: a solid-fill white-text
  `AlertDialogAction` (delete confirmation).
- **`safety/page.tsx`**: an "open incidents" count icon (`AlertTriangle`,
  red, plain text next to a number) → `destructive`. Left untouched: a
  decorative `Shield` page-header icon, pickup/dropoff marker dots
  (matching the fixed green/red map-pin brand convention used
  elsewhere), and a solid-fill white-text button.
- **`drivers/_components/document-reviewer.tsx`**: a notify-toggle
  `Bell`/`BellOff` icon pair — `BellOff` already used the token
  `text-muted-foreground`; `Bell` (blue, "on" state) given a matching
  `dark:text-blue-400` pairing rather than a semantic token, since "will
  notify" is an on/off affordance, not a success/warning/destructive
  state. Left untouched: 4 solid-fill white-text approve/reject buttons
  (contrast-risk exclusion).
- **`heatmap/page.tsx`**: a 4-card demand/supply KPI row where one
  sibling card ("Demand Pressure") already used `text-destructive` — an
  "Idle Supply" card's `Car` icon (green, available-driver count)
  converted to `success` to match, since available supply is a
  genuinely positive operational signal (same reasoning as the
  `is_online` convention used throughout this batch series). A
  supply-vs-gap progress bar's supply segment (green, paired with a
  sibling segment that already used `bg-destructive`) converted to
  `success`. Left untouched: "Active Demand" (orange) and "Surge
  Active" (amber) KPI cards — decorative categorical differentiation,
  not state indicators — and a peak-demand histogram bar (orange,
  matching the "orange = demand" convention established on the same
  page).

## 3. Fix / remediation

7 real semantic-token fixes, 1 dark: pairing addition (notify-toggle
icon), 2 documented suppressions (`driver_notified` in both
lost-and-found files). Remainder deliberately left as decorative/
contrast-risk per the established per-line rule.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to these 5 files.** No shared component,
  hook, or utility was touched. The lost-and-found status map is
  confirmed duplicated (not shared/imported) between the admin tab and
  the per-ride widget.
- All converted lines are ghost buttons, tinted (non-solid-fill)
  buttons, plain text, icons, or progress-bar segments — none were part
  of the excluded solid-fill white-text button/badge class.
- Repo-wide lint warning count stayed well under the `--max-warnings`
  ratchet (1461 vs. the 1751 ceiling).

## 5. User-experience effect

**Internal admin only.** Visual effect is a shade shift on
already-semantically-colored icons, buttons, and progress-bar segments
— no icon, label, or layout change, no change to which lost items,
safety incidents, or demand areas are shown.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/support/_tabs/lost-and-found.tsx` | `S_CFG` (3/4 states + 1 more icon) → tokens, 1 documented | #2816 Batch 7 |
| `admin-dashboard/src/app/dashboard/rides/_components/ride-lost-found.tsx` | `STATUS_ICONS` (3/4 states, same treatment) + 2 button colors → tokens | #2816 Batch 7 |
| `admin-dashboard/src/app/dashboard/safety/page.tsx` | Open-incidents count icon → destructive | #2816 Batch 7 |
| `admin-dashboard/src/app/dashboard/drivers/_components/document-reviewer.tsx` | Notify-toggle icon given dark: pairing | #2816 Batch 7 |
| `admin-dashboard/src/app/dashboard/heatmap/page.tsx` | Idle-supply icon + supply-bar segment → success, matching sibling destructive elements | #2816 Batch 7 |

## 7. Before / after

```tsx
// support/_tabs/lost-and-found.tsx + ride-lost-found.tsx — status map (before)
const S_CFG: Record<string, { l: string; c: string }> = {
    reported: { l: "Reported", c: "bg-amber-500/15 text-amber-600" },
    driver_notified: { l: "Driver Notified", c: "bg-blue-500/15 text-blue-600" },
    resolved: { l: "Resolved", c: "bg-emerald-500/15 text-emerald-600" },
    unresolved: { l: "Unresolved", c: "bg-red-500/15 text-red-600" },
};

// after
const S_CFG: Record<string, { l: string; c: string }> = {
    reported: { l: "Reported", c: "bg-warning/15 text-warning" },
    // eslint-disable-next-line no-restricted-syntax -- "driver_notified" (in progress, neither good nor bad) has no semantic-token equivalent; must stay distinct from reported/resolved/unresolved (#2816)
    driver_notified: { l: "Driver Notified", c: "bg-blue-500/15 text-blue-600" },
    resolved: { l: "Resolved", c: "bg-success/15 text-success" },
    unresolved: { l: "Unresolved", c: "bg-destructive/15 text-destructive" },
};
```

## 8. Rollback plan

`git-revert-safe` — 5 files, all `className` string literals and
documentation comments. No data/API/schema change, no shared-component
change.

## 9. Verification performed

- [x] `npx eslint` on all 5 touched files — 0 errors, 47 warnings (all
  pre-existing, mostly a `react-hooks/set-state-in-effect` warning and
  the expected residual raw-color warnings on deliberately-excluded
  lines).
- [x] `npx tsc --noEmit` — clean, 0 errors.
- [x] `npx vitest run` — 339/339 passed.
- [x] `npm run lint` (the exact CI command) — exit 0, 1461 total
  warnings (under the 1751 ratchet).
- [x] `npm run build` (real production build) — succeeded.
- [ ] Not manually click-tested/screenshotted — same standing sandbox
  limitation as every prior change-log this session; visual-regression
  baseline still not seeded.

## 10. Sign-off

- [x] Rollback plan is concrete and testable.
- [x] Blast radius is stated per file, not assumed — including
  confirming the lost-and-found status map is duplicated, not shared.
- [x] No silent behavior change — every conversion is a color-only
  visual change on an already-semantically-meaningful element; all
  click handlers, filters, and conditional rendering are unchanged.
