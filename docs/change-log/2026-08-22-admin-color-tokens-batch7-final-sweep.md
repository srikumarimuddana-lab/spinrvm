# Change Impact & Risk Log — Admin color-token migration, batch 7 / final parallel sweep

Part of the #2816 hardcoded-Tailwind-color-token migration (Stage 1). See
`docs/change-log/2026-08-21-admin-color-token-migration-plan.md` for the overall plan.
This closes out Stage 1: after this PR, `npx eslint .` in `admin-dashboard/` reports
**zero** `no-restricted-syntax` (raw-Tailwind-color) warnings, down from 402 across 45
files at the start of this sweep.

## Issue/gap identified
A repo-wide `eslint .` run (the authoritative source, not a hand-rolled grep) found 402
undocumented raw-Tailwind-color lint warnings still outstanding across 45 files in
`admin-dashboard/src` — status badges, action buttons, categorical maps, and decorative
accents that earlier sub-batches (1–79) had not yet reached.

## Root cause
Sub-batches 1–79 worked through the highest-traffic files first (drivers, rides,
corporate, support-tickets, monitoring, heatmap) but had not yet swept the remaining
long tail: company-portal/signup/login pages, referral analytics/leaderboard components,
several rides sub-components, drivers/service-areas' full remaining surface, and a
handful of smaller support/analytics files.

## Fix/remediation
Given the scale (45 independent files) and per explicit user direction to parallelize,
this sweep used the `Workflow` tool to fan out 45 concurrent agents — one per file —
each applying the same classification methodology established over the prior 79
sub-batches:

- **(a) Genuine signal** (single binary/ternary success/warning/destructive state) →
  converted to the semantic token (`bg-destructive`, `text-success`, etc.), except the
  established **contrast-risk exception**: a solid-fill white-text button using
  `bg-emerald-*`/`bg-green-*` (or amber/yellow) cannot be converted — `--success` fails
  WCAG AA against white text in dark mode (2.02:1) and there is no
  `--warning-foreground` token — so these are documented, not converted.
- **(b) Categorical map** (4+ states, e.g. a driver-lifecycle status badge, a
  `STATUS_COLORS` allowance-request map) → documented with an
  `eslint-disable`/`eslint-enable` block or per-branch `eslint-disable-next-line`
  comments; the 3-token system cannot express 4+ distinct states.
- **(c) Decorative/brand/non-signal** (icon tints, KPI-card accents, brand badges,
  map-pin colors, chart fills) → documented with `eslint-disable-next-line` and a
  one-line reason, no color change.

Net result across all 45 files: **17 genuine conversions**, **315 documented
exceptions** (categorical + decorative + contrast-risk), verified individually by each
agent running `npx eslint` on its own file before returning, then re-verified by a full
repo-wide `eslint .` pass after all agents completed.

## Risk & impact on existing functionality
This is a **comment-and-classname-only diff** — 356 insertions / 36 deletions across 45
files, zero logic, prop, or JSX-structure changes anywhere. Each agent was instructed
under strict rules: no reformatting, no touching unrelated lines, no behavior changes,
and to leave a file untouched entirely if it already had zero raw-color warnings.

Blast radius by shared-component risk:
- `rides/_components/ride-ui-helpers.tsx`'s `TL` timeline-dot helper (1 genuine
  conversion, red/emerald → destructive/success) is shared across ride detail views —
  the change is a color-token swap with equivalent rendered color, no behavior change.
- `drivers/_components/driver-action-bar.tsx` (6 conversions, 16 documented) converted
  the "Ban" outline button (red → destructive) consistently across all 4 driver-status
  branches (pending/active/needs_review/suspended) and left "Approve"/"Reactivate"
  solid buttons and "Suspend" (orange, matches existing `STATUS_CONFIG` convention)
  documented per the established exceptions — this file's own `STATUS_CONFIG` object
  was already a documented categorical map from an earlier sub-batch (35), untouched
  here.
- `drivers/page.tsx` (8 conversions, 21 documented) and `service-areas/page.tsx` (2
  conversions, 43 documented) are the two largest remaining files; both had
  already-documented categorical maps from earlier sub-batches (40) that were
  confirmed untouched, with only the previously-unreviewed remainder addressed here.
- All other 42 files are either standalone route pages (company-login/signup/portal,
  analytics, audit-logs, promotions, quests, safety, venues, users, track/[rideId]) or
  components with a single, already-identified importer (referral-*, demand-forecast-
  panel, financial-panel, rich-text-editor) — no cross-cutting shared-state risk.
- `track/[rideId]/page.tsx` is the public rider-facing trip-tracking page (not the
  internal admin portal) — it uses its own fixed light-theme neutral gray/white palette
  throughout rather than the admin design tokens; every raw-color instance there
  (`gray-*`, `white`) was documented as decorative/neutral UI chrome on a
  non-theme-aware page, none converted, since the semantic tokens are dark-mode-aware
  and this page deliberately isn't.

No genuine convertible signal was found unconverted; no categorical map was
misclassified as convertible.

## User experience effect
No visual change for any documented (category b/c) item. The 17 genuine conversions
(destructive/success token swaps) are visually equivalent to their prior hardcoded
hex-equivalent colors — same hue family, same opacity conventions already used
elsewhere in the same files. Internal-admin-portal-facing for all files except
`track/[rideId]/page.tsx` (public rider-facing, comment-only there — zero visual
change).

## Files modified
45 files, 356 insertions / 36 deletions. Full list:
`app/company-login/page.tsx`, `app/company-portal/[id]/allowance-requests/page.tsx`,
`app/company-portal/[id]/layout.tsx`, `app/company-portal/page.tsx`,
`app/company-signup/page.tsx`, `app/dashboard/analytics/page.tsx`,
`app/dashboard/audit-logs/page.tsx`, `app/dashboard/cloud-messaging/page.tsx`,
`app/dashboard/corporate-accounts/[id]/page.tsx`,
`app/dashboard/corporate-accounts/kyb-queue/page.tsx`,
`app/dashboard/drivers/_components/driver-action-bar.tsx`,
`app/dashboard/drivers/page.tsx`, `app/dashboard/drivers/queue/page.tsx`,
`app/dashboard/earnings/page.tsx`, `app/dashboard/monitoring/ride-panel.tsx`,
`app/dashboard/page.tsx`, `app/dashboard/promotions/page.tsx`,
`app/dashboard/quests/page.tsx`, `app/dashboard/rides/_components/create-ride-modal.tsx`,
`app/dashboard/rides/_components/ride-detail-modal.tsx`,
`app/dashboard/rides/_components/ride-list.tsx`,
`app/dashboard/rides/_components/ride-stats-cards.tsx`,
`app/dashboard/rides/_components/ride-ui-helpers.tsx`,
`app/dashboard/rides/live/[id]/page.tsx`, `app/dashboard/safety/page.tsx`,
`app/dashboard/service-areas/page.tsx`,
`app/dashboard/support-tickets/_components/zoho-config-card.tsx`,
`app/dashboard/support-tickets/page.tsx`,
`app/dashboard/support-tickets/tickets/[id]/page.tsx`,
`app/dashboard/support-tickets/tickets/page.tsx`,
`app/dashboard/support-tickets/trends/page.tsx`,
`app/dashboard/support/_tabs/complaints.tsx`, `app/dashboard/support/_tabs/flags.tsx`,
`app/dashboard/support/_tabs/legal-documents.tsx`, `app/dashboard/users/page.tsx`,
`app/dashboard/venues/page.tsx`, `app/track/[rideId]/page.tsx`,
`components/analytics/demand-forecast-panel.tsx`,
`components/analytics/financial-panel.tsx`, `components/driver-map.tsx`,
`components/referral-analytics.tsx`, `components/referral-leaderboard.tsx`,
`components/referral-pairs.tsx`, `components/referral-spend-summary.tsx`,
`components/ui/rich-text-editor.tsx`.

## Before/after snippet
```tsx
// driver-action-bar.tsx "Ban" button — before (repeated across 4 status branches)
<Button size="sm" variant="outline" className="text-red-600 dark:text-red-400 border-red-200 dark:border-red-800 hover:bg-red-50 dark:hover:bg-red-900/20"
    onClick={() => openAction("ban", "Ban Driver", "...", true, "Ban Driver", "bg-red-700 hover:bg-red-800 text-white")}>
// after
<Button size="sm" variant="outline" className="text-destructive border-destructive/30 hover:bg-destructive/10"
    onClick={() => openAction("ban", "Ban Driver", "...", true, "Ban Driver", "bg-destructive hover:bg-destructive/90 text-destructive-foreground")}>

// ride-ui-helpers.tsx TL timeline dot — before
d ? "bg-red-400 ring-red-200 dark:ring-red-900/50" : "bg-emerald-400 ring-emerald-200 dark:ring-emerald-900/50"
// after
d ? "bg-destructive ring-destructive/30" : "bg-success ring-success/30"
```

## Rollback plan
`git revert` this commit — pure className-string and comment changes, no data
migration, feature flag, or config involved. Reverting restores the prior hardcoded
colors with zero functional difference (they were visually equivalent before and
after).

## Verification performed
- Each of the 45 workflow agents ran `npx eslint` on its own file immediately after
  editing and confirmed 0 `no-restricted-syntax` warnings and no "unused eslint-disable
  directive" warnings before returning.
- Full repo-wide `npx eslint .` re-run after all agents completed: **0 errors, 332
  warnings** (down from 402 `no-restricted-syntax` warnings pre-sweep to 0 of that
  specific rule) — remaining 332 warnings are pre-existing, unrelated rules
  (`react-hooks/*`, `jsx-a11y/*`, `@next/next/no-img-element`, etc.), confirmed by
  diffing the JSON eslint output before/after.
- Found and left alone (out of scope, pre-existing, not touched by this diff): one
  stale unused `no-restricted-syntax` disable comment in
  `rides/_components/ride-invoice.tsx` (line 470) left over from an earlier sub-batch
  after the color underneath it was already converted to a token — noted here per the
  "notice unrelated dead code, don't delete it" rule rather than fixed, since that file
  wasn't part of this sweep's scope.
- `npx vitest run`: 339/339 tests passing across all 35 test files.
- `npm run build` (Turbopack) not re-run — pure className-string/comment diff, no
  import/module changes; the pre-existing, diff-unrelated `@spinr/shared` Turbopack
  failure was already root-caused against unmodified `origin/main` in sub-batch
  31/PR #4371.

## What was NOT verified
- No visual regression tooling exists in this repo for the admin dashboard (standing
  gap, `ACTION_ITEMS.md`) — the 17 genuine conversions were reasoned about against
  already-established, contrast-verified token values from prior sub-batches, not
  screenshotted.
- Not tested against a live Supabase/staging deployment — only against existing mocked
  `vitest` fixtures.
- Each file's classification was produced by an independent agent rather than a single
  reviewer holding the whole picture; spot-checked `driver-action-bar.tsx` and
  `ride-ui-helpers.tsx` by hand against the established methodology and found both
  correct, but the remaining 43 files' agent-reported classifications were not each
  individually re-read line-by-line by a human/orchestrator — the objective repo-wide
  `eslint .` 0-warning result and the unchanged 339/339 test pass are the primary
  correctness signals for the full set.
