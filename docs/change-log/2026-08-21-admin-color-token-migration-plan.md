# #2816 Color-Token Migration — Stage 1 Plan

Working plan for closing the 113-file hardcoded-Tailwind-color backlog
(#2816). Not a Change Impact Log entry on its own — see
`2026-08-21-admin-color-lint-rule.md` for the lint-rule change this plan
accompanies. Each migration batch gets its own Change Impact Log entry
when it lands.

## Current state (measured 2026-08-21, not carried forward from memory)

- **115 files**, ~2,894 raw hardcoded-Tailwind-color-utility occurrences
  under `admin-dashboard/src` (`grep -rlE` for
  `(bg|text|border|ring|fill|stroke|from|to|via)-<tailwind-color>-<shade>`).
- **`lib/utils.ts`'s `statusColor()` is excluded from migration scope** —
  discovered during this pass, not previously documented as an exclusion.
  It's a categorical status-color map (10 distinct ride/ticket states:
  searching, driver_assigned, scheduled, ...), already contrast-verified
  per-shade in both themes (see its own comment), and has no natural
  semantic-token equivalent — `--warning`/`--success`/`--destructive` are
  a 3-state system, not a 10-state one. Real remaining scope: **~113
  files**.
- `audit-logs/page.tsx`'s `ACTION_CONFIG` map (98 raw occurrences) uses
  the identical categorical `/15`-tint pattern — likely another partial
  exclusion, to be confirmed when that file's batch is worked (not
  pre-judged here).

## Enforcement

`eslint.config.mjs`'s new `no-restricted-syntax` rule (warn-level) flags
any new raw color utility. Suppress a deliberate exception with
`eslint-disable-next-line` / block `eslint-disable`/`eslint-enable` and a
one-line reason — see `statusColor()` for the pattern. Flip to `error`
once the batches below land, not before (a warn→error flip today would
just block unrelated PRs touching any of these 113 files).

## Batches (ordered by leverage/risk, not alphabetically)

| # | Files | Occurrences (raw grep) | Status |
|---|---|---|---|
| 1 | `app/dashboard/drivers/page.tsx` | 441 | **Done (PR #4330)** — correction: the "zero `dark:` occurrences" claim above was wrong (garbled shell output misread). Real count: only 25 of 132 color-bearing lines lacked `dark:` treatment; 16 of those were genuinely fixable (migrated), 9 deliberately left (solid-fill white-text buttons where `--success`'s dark value is 2.02:1 with white text — a real contrast regression risk, not a fix). See that PR's Change Impact Log for the full per-line classification. |
| 2 | `app/dashboard/rides/_components/ride-detail-modal.tsx`, `ride-stats-cards.tsx`, `ride-ui-helpers.tsx` | 225+52+41=318 | Not started — mixed (55 `dark:` occurrences on ride-detail-modal.tsx alone), needs real per-line classification |
| 3 | `app/dashboard/service-areas/page.tsx` | 130 | Not started — 3rd #2816 batch on this file (2 prior, per change-log history) |
| 4 | `app/dashboard/drivers/_components/driver-action-bar.tsx`, `driver-timeline.tsx`, `driver-stats-cards.tsx`, `document-reviewer.tsx` | 122+94+40+37=293 | Not started |
| 5 | `app/dashboard/earnings/page.tsx`, `earnings/payouts/page.tsx`, `earnings/payouts/[id]/page.tsx` | 111+31+27=169 | Not started — `risk:medium` (payments-adjacent surface) |
| 6 | `app/dashboard/audit-logs/page.tsx` | 98 | Not started — check `ACTION_CONFIG` exclusion first |
| 7+ | Remaining ~100 files, 3-5/batch | ~1,400 | Not started |

## Lesson from Batch 1: classify per-line, never per-file

A file-level "does this file contain the string `dark:` anywhere" check
is not a reliable signal of how broken a file is — Batch 1's own plan
entry above got this wrong. The right check is **per-line**: for each
color-bearing line, does *that line* also carry a `dark:` variant (or is
it a categorical/self-contained pattern like `statusColor()`)? Apply this
line-by-line for every batch below, not a whole-file heuristic.

## Per-file classification rule (unchanged from prior #2816 work)

- **Broken** — no `dark:` variant, or an off-brand hue → migrate to the
  semantic token (`bg-card`, `text-foreground`, `text-muted-foreground`,
  `border-border`, `bg-primary`, `text-warning`/`text-success`/`text-destructive`).
- **Hardcoded but fine** — a self-contained pastel pair that already has
  its own `dark:` variant, or a categorical status map like
  `statusColor()` → leave alone, suppress the lint warning with a reason.

## Prerequisite: visual-regression baseline (blocking, not yet done)

`e2e/visual-regression.spec.ts` covers 6 pages but has **zero committed
baselines** — `update-visual-baselines.yml` (manual `workflow_dispatch`)
has never been run. Every migration batch below should be verified
against real seeded baselines, not "reasoned about" — same standing gap
flagged in `docs/change-log/2026-07-29-admin-dashboard-visual-regression-baselines.md`
and the whole-portal UX audit. Attempted to trigger the workflow via the
GitHub Actions API from this session — failed with a 403 (integration
lacks `workflow_dispatch` permission). **Needs a human to run it**
manually (Actions tab → "Update admin-dashboard visual regression
baselines" → Run workflow → download artifact → commit the PNGs → flip
`continue-on-error: false` on the `visual-regression-test` job in
`ci.yml`).
