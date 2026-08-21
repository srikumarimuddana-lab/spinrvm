# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-21 |
| Author | Claude Code (session, vikas@ngitservices.com) |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | (see PR) |
| Related issue or gap ID | #2816 Stage 1, Batch 5 — see `docs/change-log/2026-08-21-admin-color-token-migration-plan.md`. **`risk:medium`** — payments-adjacent surface (payout status displays), per the migration plan's own flag for this batch. |

## 1. Issue / gap identified

`earnings/page.tsx`, `earnings/payouts/page.tsx`,
`earnings/payouts/[id]/page.tsx` (111+31+27 raw occurrences per the plan
doc) needed #2816 per-line classification.

## 2. Root cause / findings

Two recurring shapes across all three files:

1. **Two payout-status `STATUS_CONFIG` maps** (in `payouts/page.tsx` and
   `payouts/[id]/page.tsx`, near-identical: completed/paid/pending/
   processing/failed/cancelled) and one inline `statusBadge()` function in
   `page.tsx` (completed/pending/failed/default) — all genuinely
   dark-mode-broken (no `dark:` treatment anywhere) and, unlike the
   6-7-state hero/status maps excluded in earlier batches, these map
   cleanly: completed/paid→success, pending→warning, failed→destructive,
   cancelled/default→neutral (→ `bg-muted text-muted-foreground`).
   **Migrated to semantic tokens.** The one state that genuinely doesn't
   fit (`processing`, blue) keeps its raw hue with a house-convention
   `dark:text-blue-400` addition, not a token — same "categorical state
   with no 3-token equivalent" reasoning as prior batches, applied to
   just this one entry rather than excluding the whole map (the other 4-5
   states *do* fit cleanly here, unlike driver-timeline.tsx's earlier
   fully-mixed case).
2. **"Total Paid / Pending / Failed" and "Why payouts are failing / At-risk
   drivers / Top drivers" summary-card icons and numbers** — genuinely
   semantic (success/warning/destructive), appear near-identically in
   both `page.tsx` and `payouts/page.tsx` → migrated to tokens.
3. **Retry-action icon** (`payouts/page.tsx`) — a "retry this failed
   payout" button icon → `text-warning` (action-needed semantic).
4. **Copy-confirmation checkmark and a success toast** (`payouts/[id]/page.tsx`)
   → `text-success`/`bg-success/15`.

**Deliberately left untouched**: a large KPI/chart-legend color system in
`page.tsx` (subscription-revenue cards, chart-comparison icons, per-row
emerald/violet/amber earnings-breakdown columns — differentiating *metric
categories* like "driver earnings" vs. "admin earnings" vs. "tips", not
app states) and a cancellation-reason chart legend — same "categorical
data-differentiation color, not a semantic state" class already excluded
in Batches 2-3. These are `500`-shade plain-text/icon usages, which (per
the same reasoning applied throughout this session) are generally legible
in both themes without a `dark:` variant, unlike the light-pastel-shade
badges that motivated the real fixes above.

## 3. Fix / remediation

See §2. 3 status-config maps migrated (with the one non-fitting state
kept raw + given a house-convention dark: addition), 6 summary-card
icon/number pairs migrated, 1 retry icon, 1 confirmation icon, 1 success
toast.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to 3 files**, string-literal className changes
  only — no logic/prop/state changes, no calculation changes. This batch
  touches a payments-adjacent surface (payout amounts/status display) but
  **only the color classes rendering already-computed values** — the
  underlying `formatCurrency()`/status-string logic is completely
  untouched, so there is no risk to the actual money math or state
  transitions, only to how existing correct values are colored.
- The two `STATUS_CONFIG` maps' fallback-to-neutral entries
  (`cancelled`/default) change from `zinc-*` to the app's actual
  `bg-muted`/`text-muted-foreground` tokens — a close but not
  byte-identical neutral in light mode (flagged, same category as
  Batch 3's gray→muted-token conversions).
- Repo-wide lint warning count stayed under the `--max-warnings` ratchet
  (confirmed via `npm run lint`, exit 0).

## 5. User-experience effect

**Internal admin only, payments-adjacent.** Payout status badges
(completed/pending/failed/cancelled) previously showed 100%-light-mode-
only pastel colors with no dark-mode treatment on a page admins likely
check regularly (payout health monitoring) — now render correctly in
dark mode. No change to what status is shown for any payout, only its
color.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/earnings/page.tsx` | 1 status-badge function + 6 icon/number fixes | #2816 Batch 5 |
| `admin-dashboard/src/app/dashboard/earnings/payouts/page.tsx` | 1 `STATUS_CONFIG` map + 3 summary-card pairs + 1 retry icon | #2816 Batch 5 |
| `admin-dashboard/src/app/dashboard/earnings/payouts/[id]/page.tsx` | 1 `STATUS_CONFIG` map + 1 confirmation icon + 1 success toast | #2816 Batch 5 |

## 7. Before / after (representative)

```tsx
// payouts/page.tsx STATUS_CONFIG — before
completed: { label: "Completed", cls: "bg-green-500/15 text-green-700" },
pending:   { label: "Pending",   cls: "bg-amber-500/15 text-amber-700" },
processing:{ label: "Processing",cls: "bg-blue-500/15 text-blue-700" },
failed:    { label: "Failed",    cls: "bg-red-500/15 text-red-700" },
cancelled: { label: "Cancelled", cls: "bg-zinc-500/15 text-zinc-600" },

// after
completed: { label: "Completed", cls: "bg-success/15 text-success" },
pending:   { label: "Pending",   cls: "bg-warning/15 text-warning" },
processing:{ label: "Processing",cls: "bg-blue-500/15 text-blue-700 dark:text-blue-400" },
failed:    { label: "Failed",    cls: "bg-destructive/15 text-destructive" },
cancelled: { label: "Cancelled", cls: "bg-muted text-muted-foreground" },
```

## 8. Rollback plan

`git-revert-safe` — three files, string-literal className changes only,
no data/API/schema change. No wallet/Stripe/payout logic touched.

## 9. Verification performed

- [x] Real production build (`npm run build`) — succeeded.
- [x] `npx tsc --noEmit` — clean.
- [x] `npx vitest run` — 339/339 passed.
- [x] `npm run lint` (the exact CI command) — exit 0, 1,632 total warnings.
- [x] `npx eslint` on all 3 touched files — 0 errors.
- [x] Confirmed no `formatCurrency`/status-string/API-call logic was
  touched — grepped the diff to verify every change is a className
  literal, not a value or condition.
- [ ] Not manually click-tested/screenshotted in dark mode — same
  sandbox limitation as every prior UI change-log this session; visual-
  regression baseline still not seeded. **Given this is a payments-
  adjacent surface**, recommend a real dark-mode click-through before
  this PR merges if the visual-regression baseline isn't seeded by then.

## 10. Sign-off

- [x] Rollback plan is concrete and testable.
- [x] Blast radius is stated, not assumed — explicitly confirmed no money-calculation or state-transition code was touched.
- [x] No silent behavior change — flagged the one category (neutral-fallback→muted-token) where light-mode rendering shifts slightly; explicitly recommended a manual dark-mode check given the payments-adjacent risk tier.
