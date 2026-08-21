# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-21 |
| Author | Claude Code (session, vikas@ngitservices.com) |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | (see PR) |
| Related issue or gap ID | #2816 Stage 1, Batch 7 (sub-batch 8) — see `docs/change-log/2026-08-21-admin-color-token-migration-plan.md` |

## 1. Issue / gap identified

Continuing the #2816 hardcoded-Tailwind-color-token migration. This
sub-batch covers `drivers/import/page.tsx` (5 broken lines),
`components/analytics/demand-forecast-panel.tsx` (4),
`rides/_components/create-ride-modal.tsx` (4),
`company-portal/[id]/members/page.tsx` (4), and `support/_tabs/
flags.tsx` (4). Two files with residual counts —
`corporate-accounts/[id]/members/page.tsx` and `corporate-accounts/
[id]/page.tsx` — were confirmed already fully processed in earlier
sub-batches (their remaining lines are documented solid-fill
exclusions), no new work needed there.

## 2. Root cause / findings

- **`drivers/import/page.tsx`**: a `Stat` component's `tone="error"`/
  `tone="warn"` props (same shape as the earlier `LegacyBookingImport.tsx`
  component) → `destructive`/`warning`. A commit-success checkmark icon
  (already inside a `dark:`-aware bordered container, but the icon
  itself lacked its own `dark:` variant) → `success`. "Errors"/
  "Warnings" section headings (plain text, no `dark:`) → `destructive`/
  `warning`.
- **`components/analytics/demand-forecast-panel.tsx`**: a "peak demand"
  themed panel already using amber consistently with `dark:` pairing on
  most elements (a peak-hour grid cell and its count both already had
  `dark:bg-amber-950`/`dark:text-amber-400`) — two remaining spots
  (a "Peak Hours" KPI count and a peak-hour `Zap` icon) were missing
  their own `dark:` pairing despite sibling elements having it; given
  matching `dark:text-amber-400` for consistency with the panel's own
  established convention, not converted to a token (amber = "peak
  demand", not a success/warning/destructive state). Two decorative
  header icons left untouched.
- **`rides/_components/create-ride-modal.tsx`**: pickup/dropoff `MapPin`
  icons left untouched (matching the fixed blue/red map-pin brand
  convention used elsewhere, e.g. `monitoring/ride-panel.tsx`). A fare-
  override "(overridden)" label (amber, no `dark:`) → `warning` — a
  genuine "admin manually changed this" flag. A reusable `FareRow`'s
  single-use `accent` prop (used once, to highlight an applied promo
  discount) → `success`, since a discount is a genuinely favorable
  outcome for the rider (unlike the single-use decorative `accent`
  props left alone in earlier sub-batches, which had no such tie).
- **`company-portal/[id]/members/page.tsx`** (corporate-customer-
  facing): `STATUS_COLORS` (invited/active/suspended/removed) is the
  identical 4-state member-status concept already handled in the
  admin-facing `corporate-accounts/[id]/members/page.tsx`'s own
  `STATUS_COLORS` — given the exact same treatment for consistency
  across the two surfaces: house-convention `dark:` pairing on all 4
  states (was missing entirely — a real dark-mode bug on a customer-
  facing page), not token conversion, since collapsing "invited" vs
  "suspended" onto shared tokens would lose the distinction.
- **`support/_tabs/flags.tsx`**: an Active/Inactive safety-flag badge
  pair (amber/zinc, no `dark:`) → `warning`/`muted` — "Active" means a
  flag is currently in effect on a rider/driver, a genuine
  attention-worthy state. Left untouched: a rider/driver-type icon pair
  (blue/emerald, categorical role differentiation) and 2 decorative/
  solid-fill elements.

## 3. Fix / remediation

9 real semantic-token fixes, 3 house-convention `dark:` pairing
additions (2 in `demand-forecast-panel.tsx` matching its own sibling
convention, 1 full 4-state map in `company-portal/[id]/members/
page.tsx`). Remainder deliberately left as decorative/categorical per
the established per-line rule; 2 files confirmed already fully handled
with no new work needed.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to these 5 files.** No shared component,
  hook, or utility was touched. `company-portal/[id]/members/page.tsx`'s
  `STATUS_COLORS` mirrors but does not share code with
  `corporate-accounts/[id]/members/page.tsx`'s map — confirmed
  independently defined.
- All converted lines are plain text, icons inside already-`dark:`-aware
  containers, or badges — none were part of the excluded solid-fill
  white-text button/badge class.
- Repo-wide lint warning count stayed well under the `--max-warnings`
  ratchet (1466 vs. the 1751 ceiling).

## 5. User-experience effect

**Internal admin only**, except `company-portal/[id]/members/page.tsx`
which is corporate-customer-facing (a business user managing their own
company's members). That page's member-status badges gain dark-mode
support for the first time — previously they'd have rendered
light-mode colors regardless of theme, a real if minor dark-mode bug
now fixed. No icon, label, or layout change elsewhere; no change to
which drivers/forecasts/rides/members/flags are shown or filtered.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/drivers/import/page.tsx` | `Stat` tone prop, success icon, error/warning headings → tokens | #2816 Batch 7 |
| `admin-dashboard/src/components/analytics/demand-forecast-panel.tsx` | Peak-hours count + Zap icon given dark: pairing matching panel convention | #2816 Batch 7 |
| `admin-dashboard/src/app/dashboard/rides/_components/create-ride-modal.tsx` | Fare-override label → warning; promo-discount accent → success | #2816 Batch 7 |
| `admin-dashboard/src/app/company-portal/[id]/members/page.tsx` | `STATUS_COLORS` (4-state) given dark: pairing, matching admin-facing sibling | #2816 Batch 7 |
| `admin-dashboard/src/app/dashboard/support/_tabs/flags.tsx` | Active/Inactive flag badge → warning/muted | #2816 Batch 7 |

## 7. Before / after

```tsx
// company-portal/[id]/members/page.tsx — STATUS_COLORS (before)
const STATUS_COLORS: Record<CorporateMemberStatus, string> = {
    invited: "bg-yellow-100 text-yellow-800",
    active: "bg-emerald-100 text-emerald-800",
    suspended: "bg-orange-100 text-orange-800",
    removed: "bg-gray-200 text-gray-600",
};

// after
const STATUS_COLORS: Record<CorporateMemberStatus, string> = {
    invited: "bg-yellow-100 text-yellow-800 dark:bg-yellow-900/40 dark:text-yellow-300",
    active: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300",
    suspended: "bg-orange-100 text-orange-800 dark:bg-orange-900/30 dark:text-orange-300",
    removed: "bg-gray-200 text-gray-600 dark:bg-gray-800 dark:text-gray-400",
};
```

## 8. Rollback plan

`git-revert-safe` — 5 files, all `className` string literals. No
data/API/schema change, no shared-component change.

## 9. Verification performed

- [x] `npx eslint` on all 5 touched files — 0 errors, 33 warnings (all
  pre-existing, mostly a `react-hooks/set-state-in-effect` warning plus
  the panel's remaining decorative-icon color warnings).
- [x] `npx tsc --noEmit` — clean, 0 errors.
- [x] `npx vitest run` — 339/339 passed.
- [x] `npm run lint` (the exact CI command) — exit 0, 1466 total
  warnings (under the 1751 ratchet).
- [x] `npm run build` (real production build) — succeeded.
- [ ] Not manually click-tested/screenshotted — same standing sandbox
  limitation as every prior change-log this session; visual-regression
  baseline still not seeded. Notable: `company-portal/[id]/members/
  page.tsx` is corporate-customer-facing, so this gap applies to a
  customer-facing surface, not just internal admin.

## 10. Sign-off

- [x] Rollback plan is concrete and testable.
- [x] Blast radius is stated per file, not assumed — including
  confirming two "residual count" files needed no new work.
- [x] No silent behavior change — every conversion is a color-only
  visual change on an already-semantically-meaningful element; all
  click handlers, filters, and conditional rendering are unchanged.
- [x] A customer-facing file's added dark-mode support is called out
  explicitly rather than lumped in with internal-only changes.
