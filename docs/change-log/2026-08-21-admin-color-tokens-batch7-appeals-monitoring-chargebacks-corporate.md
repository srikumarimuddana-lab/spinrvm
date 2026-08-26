# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-21 |
| Author | Claude Code (session, vikas@ngitservices.com) |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | (see PR) |
| Related issue or gap ID | #2816 Stage 1, Batch 7 (sub-batch 3) — see `docs/change-log/2026-08-21-admin-color-token-migration-plan.md` |

## 1. Issue / gap identified

Continuing the #2816 hardcoded-Tailwind-color-token migration. This
sub-batch covers `drivers/appeals/page.tsx` (12 broken lines),
`monitoring/ride-panel.tsx` (11), `disputes/chargebacks-tab.tsx` (11),
`corporate-accounts/[id]/page.tsx` (10), `corporate-accounts/page.tsx` (9).

## 2. Root cause / findings

- **`drivers/appeals/page.tsx`**: `STATUS_COLORS` (3-state:
  pending/approved/denied) all map cleanly to
  `warning`/`success`/`destructive`. A 3-card KPI row is a 1:1 mirror of
  those same 3 states (icon + count each), converted to match — same
  pattern as the disputes-page KPI row in Batch 7 sub-batch 2. One
  unknown-status Badge fallback → `bg-muted`. Left untouched: 2
  decorative Gavel header/dialog icons.
- **`monitoring/ride-panel.tsx`**: `STATUS_COLORS` (4-state ride-progress
  timeline: searching/driver_assigned/driver_arrived/in_progress) is
  rendered as a **solid-fill white-text badge** (`text-white
  ${STATUS_COLORS[...]}`) — the Batch 1 contrast-risk exclusion applies
  directly (dark-mode `--success` fails WCAG AA against white text), and
  two of the four hues (yellow, purple) have no token equivalent
  regardless. Documented with a block `eslint-disable`/`eslint-enable`
  (matching the `audit-logs.tsx` `ACTION_CONFIG` precedent) rather than
  converted; its unknown-status fallback documented the same way. One
  real fix: a fare-amount stat (`text-green-600`, plain text, not
  solid-fill) → `text-success`, parallel to the "successful" amount
  conversions in earlier sub-batches. Left untouched: pickup/dropoff
  map-pin icon colors (blue/red — a fixed brand convention shared with
  the rider/driver apps' map pins, not a state indicator) and a purple
  "driver" avatar/icon accent theme (3 lines, categorical identity color,
  not a state).
- **`disputes/chargebacks-tab.tsx`**: `STATUS_COLORS` (7-key map, but
  only 4 distinct hues since several dispute states share a color:
  needs_response/warning_needs_response=red, under_review/
  warning_under_review=amber, won/warning_closed=green, lost=gray) — all
  converted to `destructive`/`warning`/`success`/`muted`, since the
  hue-sharing already means no distinction is lost by tokenizing.
  `daysRemainingColor()`'s dynamic urgency text (≤1 day = destructive,
  ≤3 days = warning) converted — a real semantic urgency signal. One
  unknown-status Badge fallback → `bg-muted`. Left untouched: 1
  decorative Send-icon dialog title.
- **`corporate-accounts/[id]/page.tsx`** and **`corporate-accounts/
  page.tsx`** (identical `STATUS_PILL_CLASSES`, duplicated across both
  files): `pending_verification` and `suspended` given house-convention
  `dark:` pairing rather than token conversion — collapsing either onto
  `warning` would make "not yet verified" and "suspended for cause"
  visually indistinguishable, which matters operationally (same
  reasoning as the quest-lifecycle exclusion in Batch 7 sub-batch 2).
  `active` → `success`, `closed` → `muted` (both unambiguous). Each
  file's `TRANSITIONS`/no-dark-mode red error banner and 2 flagged-
  wallet/KYB-reminder chip borders (amber/sky, in `corporate-accounts/
  page.tsx` only) given matching house-convention `dark:` pairing —
  these are themed alert sections whose container already had `dark:`
  support, so the child chip was brought in line with its own section's
  established theme rather than switched to a semantic token that would
  look inconsistent next to its sibling elements. An error-state banner
  (`corporate-accounts/[id]/page.tsx`, no `dark:` at all) converted to
  the `border-destructive/40 bg-destructive/5 text-destructive` house
  pattern (seen in `heatmap/page.tsx`, `monitoring/page.tsx`). A KYB-
  document link (`text-blue-600`, no `dark:`) given a `dark:text-blue-
  400` pairing matching the link-color convention used in `promotions/
  page.tsx` and `service-areas/page.tsx`. A delete icon-button
  (`text-red-500`, ghost button) converted to `text-destructive`. Left
  untouched: all `TRANSITIONS.confirmClass`/`AlertDialogAction`/`Badge`
  solid-fill white-text buttons (contrast-risk exclusion).

## 3. Fix / remediation

15 real semantic-token fixes, 7 house-convention `dark:` pairing
additions (2 duplicated `STATUS_PILL_CLASSES` maps × 2 keys each, 1
error banner, 1 link, 2 alert-section chips), 1 documented block-level
suppression (`ride-panel.tsx`'s solid-fill `STATUS_COLORS` + its
fallback). Remainder deliberately left as decorative/categorical/
contrast-risk per the established per-line rule.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to these 5 files.** No shared component,
  hook, or utility was touched. `STATUS_PILL_CLASSES` is duplicated
  (not shared/imported) between the two corporate-accounts files —
  confirmed via grep that each file defines its own copy — so the
  parallel edit was made independently in both, not via a shared import.
- All converted lines are outline/ghost buttons, badges, plain text, or
  alert-banner text — none were part of the excluded solid-fill white-
  text button/badge class (each individually checked for `variant=
  "outline"`/`"ghost"` or absence of a filled background before
  conversion). The one genuine solid-fill map found this sub-batch
  (`ride-panel.tsx`'s `STATUS_COLORS`) was correctly left unconverted
  and documented rather than risking a contrast regression.
- Repo-wide lint warning count stayed well under the `--max-warnings`
  ratchet (1475 vs. the 1751 ceiling).

## 5. User-experience effect

**Internal admin only.** Visual effect is a shade shift on already-
semantically-colored icons, badges, KPI numbers, error banners, and
chip borders — no icon, label, or layout change, no change to which
appeals/rides/chargebacks/companies are shown or how they're filtered.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/drivers/appeals/page.tsx` | `STATUS_COLORS` (3-state, all converted), 3-card KPI row, 1 fallback → tokens | #2816 Batch 7 |
| `admin-dashboard/src/app/dashboard/monitoring/ride-panel.tsx` | `STATUS_COLORS` + fallback documented (solid-fill contrast-risk); 1 fare-amount color → token | #2816 Batch 7 |
| `admin-dashboard/src/app/dashboard/disputes/chargebacks-tab.tsx` | `STATUS_COLORS` (7-key, all converted), urgency-color function, 1 fallback → tokens | #2816 Batch 7 |
| `admin-dashboard/src/app/dashboard/corporate-accounts/[id]/page.tsx` | `STATUS_PILL_CLASSES` (2/4 states converted, 2 given dark: pairing), error banner, link, delete icon → tokens/dark: | #2816 Batch 7 |
| `admin-dashboard/src/app/dashboard/corporate-accounts/page.tsx` | Same `STATUS_PILL_CLASSES` treatment (duplicated map), 2 alert-chip borders given dark: pairing, delete icon → token | #2816 Batch 7 |

## 7. Before / after

```tsx
// monitoring/ride-panel.tsx — STATUS_COLORS (before)
const STATUS_COLORS: Record<string, string> = {
    searching: "bg-yellow-500",
    driver_assigned: "bg-blue-500",
    driver_arrived: "bg-purple-500",
    in_progress: "bg-green-500",
};
// ... used as: className={`mt-1 text-white ${STATUS_COLORS[ride.status] ?? "bg-gray-500"}`}

// after
/* eslint-disable no-restricted-syntax -- solid-fill white-text ride-progress badge; token substitution risks a dark-mode contrast regression (see #2816 Batch 1 finding on --success vs white text), and yellow/purple have no token equivalent regardless */
const STATUS_COLORS: Record<string, string> = {
    searching: "bg-yellow-500",
    driver_assigned: "bg-blue-500",
    driver_arrived: "bg-purple-500",
    in_progress: "bg-green-500",
};
/* eslint-enable no-restricted-syntax */
```

```tsx
// corporate-accounts/page.tsx (and [id]/page.tsx) — STATUS_PILL_CLASSES (before)
const STATUS_PILL_CLASSES: Record<CompanyStatus, string> = {
    pending_verification: "bg-yellow-100 text-yellow-800 hover:bg-yellow-100",
    active: "bg-emerald-100 text-emerald-800 hover:bg-emerald-100",
    suspended: "bg-orange-100 text-orange-800 hover:bg-orange-100",
    closed: "bg-gray-200 text-gray-700 hover:bg-gray-200",
};

// after
const STATUS_PILL_CLASSES: Record<CompanyStatus, string> = {
    pending_verification: "bg-yellow-100 text-yellow-800 hover:bg-yellow-100 dark:bg-yellow-900/40 dark:text-yellow-300",
    active: "bg-success/15 text-success hover:bg-success/15",
    suspended: "bg-orange-100 text-orange-800 hover:bg-orange-100 dark:bg-orange-900/30 dark:text-orange-300",
    closed: "bg-muted text-muted-foreground hover:bg-muted",
};
```

## 8. Rollback plan

`git-revert-safe` — 5 files, all `className` string literals and
documentation comments. No data/API/schema change, no shared-component
change.

## 9. Verification performed

- [x] `npx eslint` on all 5 touched files — 0 errors, 50 warnings (all
  pre-existing: `react-hooks/purity` (`Date.now()` in render, unrelated
  to this diff), 2 unrelated `no-unused-expressions`, plus the
  deliberately-left-unconverted decorative/categorical lines).
- [x] `npx tsc --noEmit` — clean, 0 errors.
- [x] `npx vitest run` — 339/339 passed.
- [x] `npm run lint` (the exact CI command) — exit 0, 1475 total warnings
  (under the 1751 ratchet).
- [x] `npm run build` (real production build) — succeeded.
- [ ] Not manually click-tested/screenshotted — same standing sandbox
  limitation as every prior change-log this session; visual-regression
  baseline still not seeded.

## 10. Sign-off

- [x] Rollback plan is concrete and testable.
- [x] Blast radius is stated per file, not assumed — including
  confirming `STATUS_PILL_CLASSES` is a duplicated, not shared,
  definition across the two corporate-accounts files.
- [x] No silent behavior change — every conversion is a color-only
  visual change on an already-semantically-meaningful element; all
  click handlers, filters, and conditional rendering are unchanged.
- [x] The one genuine solid-fill contrast-risk map found this sub-batch
  was identified and documented rather than converted, consistent with
  the Batch 1 WCAG AA finding.
