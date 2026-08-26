# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-21 |
| Author | Claude Code (session, vikas@ngitservices.com) |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | (see PR) |
| Related issue or gap ID | #2816 Stage 1, Batch 7 (sub-batch 9) — see `docs/change-log/2026-08-21-admin-color-token-migration-plan.md` |

## 1. Issue / gap identified

Continuing the #2816 hardcoded-Tailwind-color-token migration into the
largest remaining files. This sub-batch covers `earnings/page.tsx` (25
broken lines), `rides/_components/ride-detail-modal.tsx` (19),
`drivers/_components/driver-action-bar.tsx` (17), `dashboard/page.tsx`
(15), and `bulk-operations/page.tsx` (13).

## 2. Root cause / findings

- **`earnings/page.tsx`**: the file is dominated (~18 of 25 lines) by a
  driver-earnings (emerald) / admin-earnings (violet) / tips (amber)
  money-category-differentiation scheme repeated across KPI cards,
  table columns, and a subscriptions-comparison section (same emerald/
  violet/amber pattern reused for total-revenue/MRR/range-revenue) —
  left untouched, matching the established money-category precedent
  from Batches 5 and 7. Two real fixes: an "X active" inline count
  (the only colored word in a plain-text "active · expired · cancelled"
  sentence) → `success`; a 3-way cancellation-cause bar+legend (rider/
  driver/system, where "system" already used `bg-muted-foreground/60`)
  → `warning`/`destructive` for rider/driver, completing the set's
  internal consistency. Left untouched: 2 solid-fill white-text buttons.
- **`rides/_components/ride-detail-modal.tsx`**: its `STATUS_META`
  (7-state ride-lifecycle hero badge) was **already** documented and
  block-suppressed from an earlier point in this session — confirmed,
  no new work. 2 real fixes: two icons (`Route`, `Clock`) inside an
  already-`dark:`-aware "Actual (GPS)" panel, given matching `dark:`
  pairing (their sibling text already had it); a promo-discount amount
  (`text-emerald-600`, a genuine savings/favorable indicator, same
  reasoning as the earlier `create-ride-modal.tsx` promo conversion) →
  `success`. Left untouched: pickup/dropoff labels and a rider/driver
  flag-color pair (categorical), several fixed-convention star-rating
  icons, a decorative "Tip" icon/amount (money category), and a
  solid-fill white-text button.
- **`drivers/_components/driver-action-bar.tsx`**: all 17 broken lines
  are solid-fill white-text action buttons (Approve/Suspend/Ban/
  Reactivate/Unban) — confirmed the entire file's remaining raw-color
  usage falls under the Batch 1 contrast-risk exclusion. No conversions
  applicable.
- **`dashboard/page.tsx`**: a `STAT_COLOR_CLASSES` generic multi-hue
  icon-tile palette (the file's own comment explains it exists to avoid
  dynamic-class purging, not to express any single semantic axis) left
  untouched — a genuinely categorical/decorative utility. A ride-
  breakdown bar (Completed/In progress/Searching/Scheduled/Cancelled)
  and its matching completion/cancellation-rate numbers — `Completed`
  → `success`, `Cancelled` → `destructive` (both bar segment and rate
  number); the middle three (in-progress/searching/scheduled) have no
  clean token fit and were left categorical. A "live update" pulse dot
  left as a decorative convention (matches the "LIVE" broadcast-dot
  precedent).
- **`bulk-operations/page.tsx`**: the same `Stat` tone-prop pattern seen
  in `LegacyBookingImport.tsx`/`drivers/import/page.tsx` → tokens. 5
  "Errors"/"Warnings"/"Phone duplicates" section headings (repeated
  across 3 near-identical import-tool sub-flows on this page) →
  `destructive`/`warning`. 3 success-confirmation elements (an
  "Updated" inline badge, 2 commit-success `CheckCircle2` icons inside
  already-`dark:`-aware containers) → `success`. A succeeded/failed
  result summary — `succeeded` → `success`, `failed` → `warning`
  (preserving the author's original amber severity choice rather than
  upgrading it to destructive, since this migration doesn't re-judge
  severity, only substitutes the token for the existing color). Left
  untouched: an "Already mapped — review to update" informational
  heading (sky, a distinct informational tier with no token fit).

## 3. Fix / remediation

19 real semantic-token fixes across 4 files (2 conversions + 2 dark:
pairings in `earnings`/`ride-detail-modal`, 4 in `dashboard/page.tsx`,
10 in `bulk-operations/page.tsx`). One file (`driver-action-bar.tsx`)
confirmed to need zero changes — entirely solid-fill exclusions. One
file's categorical map (`ride-detail-modal.tsx`'s `STATUS_META`)
confirmed already documented from earlier in this session.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to these 5 files.** No shared component,
  hook, or utility was touched. The `Stat` tone-prop component and the
  Errors/Warnings heading pattern are each locally defined per file
  (confirmed independently, not imported from a shared module).
- All converted lines are plain text, bar/progress segments, or icons
  inside already-`dark:`-aware containers — none were part of the
  excluded solid-fill white-text button/badge class.
- Repo-wide lint warning count stayed well under the `--max-warnings`
  ratchet (1342 vs. the 1751 ceiling) — the largest single-batch drop
  this series, reflecting how much of this remaining backlog turned out
  to be already-documented or solid-fill exclusions rather than new
  conversions.

## 5. User-experience effect

**Internal admin only.** Visual effect is a shade shift on
already-semantically-colored bars, rates, icons, and headings — no
icon, label, or layout change, no change to which rides/earnings/
imports are shown or how they're filtered.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/earnings/page.tsx` | 1 active-state text + 3-way cancellation-cause bar/legend → tokens | #2816 Batch 7 |
| `admin-dashboard/src/app/dashboard/rides/_components/ride-detail-modal.tsx` | 2 icons given dark: pairing, 1 promo-discount amount → success | #2816 Batch 7 |
| `admin-dashboard/src/app/dashboard/drivers/_components/driver-action-bar.tsx` | No changes — confirmed all-solid-fill exclusion | #2816 Batch 7 |
| `admin-dashboard/src/app/dashboard/page.tsx` | Ride-breakdown Completed/Cancelled bar + rate numbers → tokens | #2816 Batch 7 |
| `admin-dashboard/src/app/dashboard/bulk-operations/page.tsx` | `Stat` tone, 5 headings, 3 success confirmations, succeeded/failed summary → tokens | #2816 Batch 7 |

## 7. Before / after

```tsx
// dashboard/page.tsx — ride breakdown (before)
<BarStat label="Completed" value={bd.completed} total={rides.total} color="bg-emerald-500" />
<BarStat label="Cancelled" value={bd.cancelled} total={rides.total} color="bg-red-400" />
// ...
<p className="text-lg font-bold text-emerald-500">{completionPct}%</p>
<p className="text-lg font-bold text-red-400">{cancellationPct}%</p>

// after
<BarStat label="Completed" value={bd.completed} total={rides.total} color="bg-success" />
<BarStat label="Cancelled" value={bd.cancelled} total={rides.total} color="bg-destructive" />
// ...
<p className="text-lg font-bold text-success">{completionPct}%</p>
<p className="text-lg font-bold text-destructive">{cancellationPct}%</p>
```

## 8. Rollback plan

`git-revert-safe` — 4 modified files, all `className` string literals.
No data/API/schema change, no shared-component change.

## 9. Verification performed

- [x] `npx eslint` on all 5 touched files — 0 errors, 207 warnings (the
  large majority pre-existing/expected: `driver-action-bar.tsx`'s 17
  documented solid-fill exclusions plus decorative/categorical lines
  across the other 4 files).
- [x] `npx tsc --noEmit` — clean, 0 errors.
- [x] `npx vitest run` — 339/339 passed.
- [x] `npm run lint` (the exact CI command) — exit 0, 1342 total
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
- [x] `driver-action-bar.tsx` was verified to need zero changes rather
  than assumed — every one of its 17 flagged lines was individually
  checked and confirmed solid-fill.
