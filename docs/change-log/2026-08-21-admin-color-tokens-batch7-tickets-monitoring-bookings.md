# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-21 |
| Author | Claude Code (session, vikas@ngitservices.com) |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | (see PR) |
| Related issue or gap ID | #2816 Stage 1, Batch 7 (sub-batch 4) — see `docs/change-log/2026-08-21-admin-color-token-migration-plan.md` |

## 1. Issue / gap identified

Continuing the #2816 hardcoded-Tailwind-color-token migration. This
sub-batch covers `support-tickets/tickets/page.tsx` (8 broken lines),
`monitoring/toolbar.tsx` (8), `company-portal/[id]/bookings/page.tsx`
(8), and `support-tickets/tickets/[id]/page.tsx` (7).
`rides/_components/ride-ui-helpers.tsx` (7) was confirmed already fully
handled in an earlier batch — its residual count is the documented
`STATUS_CONFIG` exclusion (dot fields explicitly don't need `dark:` per
the existing in-file comment) — no new work there.

## 2. Root cause / findings

- **`support-tickets/tickets/page.tsx`** and **`support-tickets/
  tickets/[id]/page.tsx`** (duplicated `statusClass()`/inline function,
  confirmed independently defined in each file, not shared): a Zoho
  Desk ticket status classifier using substring matching (open/hold/
  escalated/closed/fallback). `hold` → `warning`, `escalated` →
  `destructive`, `closed` and the fallback → `muted`; `open` documented
  with `eslint-disable-next-line` (no token fits "new/active", and it
  must stay visually distinct from the other three). `tickets/page.tsx`
  also has a `priorityClass()` (high/urgent/medium/other) — all 3 map
  cleanly and were converted. `tickets/[id]/page.tsx` additionally had
  2 blue link-color spots (a "show full message" toggle and signature
  `<a>` tags) with no `dark:` variant — given `dark:text-blue-400`
  pairing matching the link-color convention already used in
  `promotions/page.tsx`/`service-areas/page.tsx`.
- **`monitoring/toolbar.tsx`**: the Online/Offline live-count toggle
  buttons and the WebSocket-connected `Wifi` icon are genuine driver-
  online-state indicators — converted to `success`/`muted-foreground/40`,
  matching the exact convention already established in `drivers/
  page.tsx` (`driver.is_online ? "bg-success" : "bg-muted-foreground/40"`).
  The `Wifi`/`WifiOff` pair was especially clear: `WifiOff` already used
  `text-destructive`, so `Wifi` was converted to `text-success` to
  match its own sibling. Left untouched: "On Ride" (amber), "Rides"
  (blue), and "Demand" (orange) — these are map-layer-toggle legend
  colors, not state indicators (no "on ride" or "rides layer" concept
  maps to success/warning/destructive), consistent with the KPI-
  differentiation-legend precedent from earlier batches.
- **`company-portal/[id]/bookings/page.tsx`**: `STATUS_STYLES` is the
  actual backend ride state machine (8 states: scheduled/searching/
  driver_assigned/driver_accepted/driver_arrived/in_progress/completed/
  cancelled) rendered on a corporate-rider-facing page. Collapsing
  driver_assigned/driver_accepted/driver_arrived onto the 3-token system
  would lose the exact stage a business user's booking is at —
  documented with a block `eslint-disable`/`eslint-enable` (same
  pattern as `audit-logs.tsx`'s `ACTION_CONFIG` and `ride-ui-
  helpers.tsx`'s own `STATUS_CONFIG`). It had zero `dark:` variants at
  all (a real gap, unlike `ride-ui-helpers.tsx`'s already-compliant
  twin), so each of the 8 states was given house-convention `dark:`
  pairing matching the hue conventions already established elsewhere
  in the codebase (indigo/violet/emerald from `drivers/page.tsx` and
  `driver-timeline.tsx`; sky/amber/red following the same shape).

## 3. Fix / remediation

8 real semantic-token fixes, 8 house-convention `dark:` pairing
additions (2 link colors + the 8-state `STATUS_STYLES` map, minus 2
already covered by the block-suppress count), 3 documented suppressions
(`open` × 2 duplicated files, `STATUS_STYLES` block). Remainder
deliberately left as decorative/categorical per the established
per-line rule; one file confirmed already fully handled with no new
work needed.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to these 4 files.** No shared component,
  hook, or utility was touched. `statusClass()` is duplicated (not
  shared/imported) between the two support-tickets files — confirmed
  independently defined in each.
- All converted lines are toggle buttons, plain text, badges, or icons
  — none were part of the excluded solid-fill white-text button/badge
  class.
- Repo-wide lint warning count stayed well under the `--max-warnings`
  ratchet (1482 vs. the 1751 ceiling).

## 5. User-experience effect

**Internal admin only**, except `company-portal/[id]/bookings/page.tsx`
which is corporate-rider-facing (a business user viewing their own
company's ride bookings). Visual effect there is the ride-status badges
gaining dark-mode support for the first time (previously they'd have
rendered with light-mode colors regardless of theme — a real, if minor,
dark-mode bug now fixed) plus a shade shift on the semantic-token
conversions elsewhere. No label, icon, or layout change; no change to
which bookings/tickets are shown or filtered.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/support-tickets/tickets/page.tsx` | `statusClass()`/`priorityClass()` → tokens (open documented) | #2816 Batch 7 |
| `admin-dashboard/src/app/dashboard/support-tickets/tickets/[id]/page.tsx` | Same `statusClass()` treatment; 2 link colors given dark: pairing | #2816 Batch 7 |
| `admin-dashboard/src/app/dashboard/monitoring/toolbar.tsx` | Online/Offline toggle + Wifi icon → success/muted tokens, matching driver is_online convention | #2816 Batch 7 |
| `admin-dashboard/src/app/company-portal/[id]/bookings/page.tsx` | `STATUS_STYLES` (8-state ride machine) given dark: pairing + documented block suppression | #2816 Batch 7 |

## 7. Before / after

```tsx
// monitoring/toolbar.tsx — Online toggle (before)
filters.showOnline
    ? "bg-green-500/10 text-green-600 ring-1 ring-green-500/30"
    : "text-muted-foreground hover:bg-muted"
// ...
<span className="h-2 w-2 rounded-full bg-green-500" />

// after
filters.showOnline
    ? "bg-success/10 text-success ring-1 ring-success/30"
    : "text-muted-foreground hover:bg-muted"
// ...
<span className="h-2 w-2 rounded-full bg-success" />
```

```tsx
// company-portal/[id]/bookings/page.tsx — STATUS_STYLES (before)
const STATUS_STYLES: Record<string, string> = {
    scheduled: "bg-sky-100 text-sky-800",
    searching: "bg-amber-100 text-amber-800",
    // ... 6 more states, no dark: at all
};

// after
/* eslint-disable no-restricted-syntax -- ride state machine (8 distinct operational states); collapsing driver_assigned/driver_accepted/driver_arrived onto the 3-token system would lose the exact stage a corporate booking is at, which matters to a business user tracking their own bookings (#2816) */
const STATUS_STYLES: Record<string, string> = {
    scheduled: "bg-sky-100 text-sky-800 dark:bg-sky-900/30 dark:text-sky-300",
    searching: "bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-300",
    // ... all 8 states now dark-mode aware
};
/* eslint-enable no-restricted-syntax */
```

## 8. Rollback plan

`git-revert-safe` — 4 files, all `className` string literals and
documentation comments. No data/API/schema change, no shared-component
change.

## 9. Verification performed

- [x] `npx eslint` on all 4 touched files — 0 errors, 12 warnings (all
  pre-existing: 1 unrelated `react-hooks/set-state-in-effect`, plus the
  expected remaining raw-color warnings on lines that now carry a
  `dark:` pairing but still use a non-token base hue — same as every
  prior batch's link-color and categorical-map conversions).
- [x] `npx tsc --noEmit` — clean, 0 errors.
- [x] `npx vitest run` — 339/339 passed.
- [x] `npm run lint` (the exact CI command) — exit 0, 1482 total
  warnings (under the 1751 ratchet).
- [x] `npm run build` (real production build) — succeeded.
- [ ] Not manually click-tested/screenshotted — same standing sandbox
  limitation as every prior change-log this session; visual-regression
  baseline still not seeded. Notable here: `company-portal/[id]/
  bookings/page.tsx` is externally visible to corporate customers, so
  this gap applies to a customer-facing surface, not just internal
  admin — flagged explicitly rather than treated the same as an
  internal-only change.

## 10. Sign-off

- [x] Rollback plan is concrete and testable.
- [x] Blast radius is stated per file, not assumed — including
  confirming `statusClass()` is duplicated, not shared, between the two
  support-tickets files.
- [x] No silent behavior change — every conversion is a color-only
  visual change on an already-semantically-meaningful element; all
  click handlers, filters, and conditional rendering are unchanged.
- [x] A customer-facing file's added dark-mode support is called out
  explicitly in the UX-effect section rather than lumped in with
  internal-only changes.
