# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-21 |
| Author | Claude Code (session, vikas@ngitservices.com) |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | (see PR) |
| Related issue or gap ID | #2816 Stage 1, Batch 7 (first sub-batch) — see `docs/change-log/2026-08-21-admin-color-token-migration-plan.md` |

## 1. Issue / gap identified

Continuing the #2816 hardcoded-Tailwind-color-token migration into the
long-tail backlog beyond Batches 1-6. This sub-batch covers the five
smallest/quickest untouched files from the re-scoped remaining-file list:
`users/page.tsx` (27 broken lines), `promotions/page.tsx` (23),
`corporate-accounts/[id]/members/page.tsx` (10), `analytics/page.tsx` (6),
`drivers/queue/page.tsx` (2).

## 2. Root cause / findings

Per-line classification (not per-file) applied to all 5 files:

- **`analytics/page.tsx`** — 3 real fixes: `CheckCircle`/Completion Rate
  icon (`text-green-500` → `text-success`), `XCircle`/Cancellation Rate
  icon (`text-red-500` → `text-destructive`), and a low-completion-rate
  alert card's ring + `TrendingDown` icon (`ring-red-500`/`text-red-500`
  → `ring-destructive`/`text-destructive`) — confirmed genuine via its
  sibling count-number, which already used
  `text-red-600 dark:text-red-400`. Left untouched: a decorative header
  icon (`BarChart3 text-blue-500`) and the amber `DollarSign` "Revenue"
  KPI icon (categorical KPI-differentiation legend item, same precedent
  as Batch 5's money-category colors).
- **`drivers/queue/page.tsx`** — 1 real fix: a `FileWarning` icon
  (`text-amber-600` → `text-warning`). Left untouched: a solid-fill
  `bg-emerald-600 hover:bg-emerald-700 text-white` button (contrast-risk
  exclusion class from Batch 1).
- **`corporate-accounts/[id]/members/page.tsx`** — 3 real fixes:
  `REQUEST_STATUS_COLORS` (4-state pending/approved/auto_approved/denied
  badge map) was missing `dark:` pairing entirely, unlike its sibling
  `STATUS_COLORS` map two lines above — added the exact `dark:` pairs
  already established as house convention in this file
  (yellow/emerald) and codebase-wide (`bg-blue-900/40 dark:text-blue-300`
  from `sentry-logs/page.tsx`, `bg-red-900/30 dark:text-red-300` from
  `drivers/expiring/page.tsx`). A success `Check` icon inside an
  already-dark-aware "Invite created" panel (`text-emerald-600` with no
  `dark:` variant, unlike its siblings) → `text-success`. An outline
  "Deny" button's icon-adjacent text (`text-red-600`, not solid-fill) →
  `text-destructive`. Left untouched: two solid-fill white-text buttons
  (Approve/Remove, `bg-emerald-600`/`bg-red-600`) and one solid-fill
  pending-count badge (`bg-yellow-500 text-white`) — all contrast-risk
  exclusions.
- **`promotions/page.tsx`** — 5 real fixes: `STATUS_CONFIG` (3-state
  active/inactive/expired badge map, tinted `/15` pattern) →
  `bg-success/15 text-success`, `bg-muted text-muted-foreground`,
  `bg-destructive/15 text-destructive`; an active/inactive `ToggleRight`
  icon (`text-emerald-500` → `text-success`, matching its sibling
  `ToggleLeft`'s already-token `text-muted-foreground`); a `*required`
  field marker (`text-red-500` → `text-destructive`); a missing-value
  input's warning border/ring (`border-amber-400
  focus-visible:ring-amber-400` → `border-warning
  focus-visible:ring-warning`); a chip-remove button's hover state
  (`hover:text-red-500` → `hover:text-destructive`). Left untouched: a
  6-item KPI-differentiation stat row (Total/Active/Expired/Private/
  Redemptions/Discount, categorical legend — same precedent as Batch 5
  and this batch's own `analytics/page.tsx` finding), 3 decorative
  section-header icons (violet/blue), a selected-row highlight
  (`bg-violet-500/5`, decorative), and 2 solid-fill white-text buttons.
- **`users/page.tsx`** — 6 real fixes: an account-status badge ternary
  (banned/suspended/pending_deletion/active), duplicated in the table row
  and the detail-panel header — 3 of 4 branches map cleanly to tokens
  (`bg-red-500/15` → `bg-destructive/15 text-destructive`,
  `bg-amber-500/15` → `bg-warning/15 text-warning`, `bg-emerald-500/15` →
  `bg-success/15 text-success`); the 4th (`pending_deletion`, orange) has
  no token equivalent and is suppressed with
  `eslint-disable-next-line`/reason, same pattern as `ACTION_CONFIG` in
  Batch 6 but scoped to a single ternary branch instead of a whole block.
  Two dropdown-menu action icons (`CheckCircle`/Activate `text-green-600`
  → `text-success`, `AlertTriangle`/Suspend `text-amber-600` →
  `text-warning`, both on outline buttons, not solid-fill). A wallet
  transaction credit/debit amount (`isCredit ? text-emerald-600 :
  text-red-600` → `text-success`/`text-destructive`). A ride-status badge
  (outline variant, completed/cancelled/other → `text-success`/
  `text-destructive`/`text-warning`). A `*required` reason-field marker
  (`text-red-500` → `text-destructive`). Left untouched: a 4-card KPI
  stat row (Total/Riders/Drivers/Dual-role, categorical — same precedent
  as elsewhere), two Rider/Driver role badges (categorical role-type
  differentiation, not a state color), a decorative gradient panel, and
  2 solid-fill buttons (one plain emerald "Activate" CTA, one credit/
  debit `AlertDialogAction` whose existing debit branch already used
  `bg-destructive text-destructive-foreground` — its credit branch stays
  raw `bg-emerald-600` because no `--success-foreground` token exists in
  `globals.css`, so a same-pattern conversion would render unreadable
  text; this mirrors the Batch 1 finding that dark-mode `--success` fails
  WCAG AA against white text).

## 3. Fix / remediation

18 real semantic-token fixes across 5 files (3 analytics, 1 drivers/queue,
3 corporate members, 5 promotions, 6 users), 1 documented ternary-branch
exclusion (`pending_deletion`), remainder deliberately left as decorative/
categorical/contrast-risk per the established per-line rule.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to these 5 files.** No shared component,
  hook, or utility was touched — every change is a `className` literal
  inside the file that renders it. `REQUEST_STATUS_COLORS` and
  `STATUS_CONFIG` are each only referenced within their own file (grepped
  to confirm no cross-file import).
- All converted lines are outline buttons, badges, or icons — none of
  the touched lines were part of the excluded solid-fill white-text
  button/badge class (each was individually checked for `variant=
  "outline"`/`variant="ghost"` or absence of a filled background before
  conversion).
- Repo-wide lint warning count stayed well under the `--max-warnings`
  ratchet (1547 vs. the 1751 ceiling — down from prior batches as more
  raw-color lines convert to tokens).

## 5. User-experience effect

**Internal admin only.** Visual effect is a shade shift (e.g. `emerald-600`
→ the `--success` token's `#15803d` in light mode / `#30d158` in dark
mode) on already-semantically-colored icons, badges, and borders — no
icon, label, or layout change. No behavior change to any click handler,
form validation logic, or data flow.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/analytics/page.tsx` | 3 icon/ring color → tokens | #2816 Batch 7 |
| `admin-dashboard/src/app/dashboard/drivers/queue/page.tsx` | 1 icon color → token | #2816 Batch 7 |
| `admin-dashboard/src/app/dashboard/corporate-accounts/[id]/members/page.tsx` | `REQUEST_STATUS_COLORS` dark: pairing added, 2 icon/text colors → tokens | #2816 Batch 7 |
| `admin-dashboard/src/app/dashboard/promotions/page.tsx` | `STATUS_CONFIG` → tokens, 4 icon/border/text colors → tokens | #2816 Batch 7 |
| `admin-dashboard/src/app/dashboard/users/page.tsx` | status-badge ternary (3/4 branches) → tokens + 1 documented exclusion, 4 icon/text colors → tokens | #2816 Batch 7 |

## 7. Before / after

```tsx
// corporate-accounts/[id]/members/page.tsx — REQUEST_STATUS_COLORS (before)
const REQUEST_STATUS_COLORS: Record<AllowanceRequestRow["status"], string> = {
    pending: "bg-yellow-100 text-yellow-800",
    approved: "bg-emerald-100 text-emerald-800",
    auto_approved: "bg-blue-100 text-blue-800",
    denied: "bg-red-100 text-red-700",
};

// after
const REQUEST_STATUS_COLORS: Record<AllowanceRequestRow["status"], string> = {
    pending: "bg-yellow-100 text-yellow-800 dark:bg-yellow-900/40 dark:text-yellow-300",
    approved: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300",
    auto_approved: "bg-blue-100 text-blue-800 dark:bg-blue-900/40 dark:text-blue-300",
    denied: "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-300",
};
```

```tsx
// users/page.tsx — account-status badge (before)
user.status === "banned" ? "bg-red-500/15 text-red-600"
: user.status === "suspended" ? "bg-amber-500/15 text-amber-600"
: user.status === "pending_deletion" ? "bg-orange-500/15 text-orange-600"
: "bg-emerald-500/15 text-emerald-600"

// after
user.status === "banned" ? "bg-destructive/15 text-destructive"
: user.status === "suspended" ? "bg-warning/15 text-warning"
// eslint-disable-next-line no-restricted-syntax -- pending_deletion has no semantic-token equivalent (#2816)
: user.status === "pending_deletion" ? "bg-orange-500/15 text-orange-600"
: "bg-success/15 text-success"
```

## 8. Rollback plan

`git-revert-safe` — 5 files, all `className` string literals and one
suppression comment. No data/API/schema change, no shared-component
change.

## 9. Verification performed

- [x] `npx eslint` on all 5 touched files — 0 errors, 101 warnings (all
  pre-existing/deliberately-excluded raw-color lines plus 2 pre-existing
  `react-hooks/set-state-in-effect` warnings unrelated to this diff).
- [x] `npx tsc --noEmit` — repo-wide errors are all pre-existing
  `GeoJSON`-namespace failures in unrelated map components; confirmed via
  `git stash` that the identical 43-line error set exists on unmodified
  `origin/main`. None of the 5 touched files appear in the error list.
- [x] `npx vitest run` — 339/339 passed.
- [x] `npm run lint` (the exact CI command) — exit 0, 1547 total warnings
  (under the 1751 ratchet).
- [ ] **`npm run build` — blocked by a pre-existing environment issue,
  not this diff.** Turbopack fails on `@spinr/shared`'s raw `.ts` files
  under `node_modules` ("Unknown module type"). Confirmed via `git stash`
  that the identical failure occurs on unmodified `origin/main` in this
  sandbox — a workspace-package resolution artifact of this session's
  `npm install`, not a regression from this change. Prior batches in this
  series (1-6) report a clean `npm run build`; this is the first batch
  where the sandbox environment itself was in this broken state at
  verification time.
- [ ] Not manually click-tested/screenshotted — same standing sandbox
  limitation as every prior change-log this session; visual-regression
  baseline still not seeded.

## 10. Sign-off

- [x] Rollback plan is concrete and testable.
- [x] Blast radius is stated per file, not assumed.
- [x] No silent behavior change — every conversion is a color-only visual
  change on an already-semantically-meaningful element; all click
  handlers, validation logic, and conditional rendering are unchanged.
- [x] Build-tool gap (`npm run build` blocked by a pre-existing sandbox
  issue) stated explicitly rather than silently omitted.
