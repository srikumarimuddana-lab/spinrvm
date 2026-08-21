# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-21 |
| Author | Claude Code (session, vikas@ngitservices.com) |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | (see PR) |
| Related issue or gap ID | #2816 Stage 1, Batch 7 (sub-batch 6) — see `docs/change-log/2026-08-21-admin-color-token-migration-plan.md` |

## 1. Issue / gap identified

Continuing the #2816 hardcoded-Tailwind-color-token migration. This
sub-batch covers `support-tickets/page.tsx` (7 broken lines),
`monitoring/driver-panel.tsx` (7), `monitoring/redis/page.tsx` (6),
`company-portal/[id]/verification/page.tsx` (6), and
`components/auto-payouts-panel.tsx` (6).

## 2. Root cause / findings

- **`support-tickets/page.tsx`**: this is the **third** copy of the
  `statusClass()` substring-matching function found this session
  (identical to the one in `support-tickets/tickets/page.tsx` and
  `tickets/[id]/page.tsx` from earlier sub-batches, each independently
  defined, not shared). Given the same treatment: `hold`/`escalated`/
  `closed`/fallback → `warning`/`destructive`/`muted`; `open` documented.
  A "Zoho not connected" banner (`text-amber-700`, no `dark:`) converted
  to `text-warning` — a genuine warning state. A single-use `accent`
  prop on a reusable `StatCard` (blue, used once for "Open / active")
  left untouched — decorative highlight, not a multi-item legend, and
  ties to the same "open" state that has no token fit.
- **`monitoring/driver-panel.tsx`**: an `is_online` `Badge` using
  `variant="default"` with `bg-green-500` override — the `default`
  Badge variant sets `text-primary-foreground`, which is not guaranteed
  to contrast against a green override (same shape as the Batch 1
  finding) — left untouched as a contrast-risk exclusion. A "Stale"
  outline badge (amber, no `dark:`) → `warning` tokens. A plain-text
  `is_online` status line → `success`. A "Current Ride" link-style
  button (blue, categorical link affordance) given `dark:text-blue-400`
  pairing rather than a token, matching the link-color convention used
  elsewhere. Two 3-state ride/document status badges (completed/
  cancelled/other, verified/expired/other) — all converted to
  `success`/`destructive`/`warning`. A star-rating icon (amber) left
  untouched — a fixed, universal star-rating convention, not a state.
- **`monitoring/redis/page.tsx`**: `memoryColor()` (a memory-usage
  progress-bar fill, no text-contrast concern) — all 3 tiers converted
  to `destructive`/`warning`/`success`. Two warning alert `Card`s
  (backend-offline, eviction-count) and one destructive alert `Card`
  (WS fan-out degraded) — each had raw-yellow/red borders with no
  `dark:` support paired with text that already had `dark:` — converted
  fully to `warning`/`destructive` tokens for consistency rather than
  just adding a `dark:` pairing to the raw hue.
- **`company-portal/[id]/verification/page.tsx`** (corporate-customer-
  facing): 5 one-off state icons, each in its own dedicated single-state
  card (pending/under_review = `warning`, approved = `success`,
  suspended/rejected = `destructive`) — all converted, since each icon
  is tied 1:1 to a real KYB review state, not a shared categorical
  legend. A "rejected" card's border (`border-red-200`, no `dark:` at
  all) converted to `border-destructive/40`, matching the house pattern
  used in `heatmap/page.tsx`/`monitoring/page.tsx`.
- **`components/auto-payouts-panel.tsx`**: `STATUS_CONFIG` (4-state
  payout-batch map: completed/partial/running/failed) — `completed`/
  `partial`/`failed` converted; `running` (in-progress, neither good nor
  bad) documented — must stay distinct from the other three. Its
  unknown-status fallback → `bg-muted text-muted-foreground`. An
  empty-state "no blocked drivers" checkmark → `success`.

## 3. Fix / remediation

19 real semantic-token fixes, 1 house-convention `dark:` pairing
addition (link color), 2 documented suppressions (`open` in
`support-tickets/page.tsx`, `running` in `auto-payouts-panel.tsx`).
Remainder deliberately left as decorative/contrast-risk per the
established per-line rule.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to these 5 files.** No shared component,
  hook, or utility was touched. `statusClass()` is now confirmed
  duplicated identically across three separate files (not shared).
- All converted lines are outline badges, plain text, progress-bar
  fills, or one-off icons — none were part of the excluded solid-fill
  white-text button/badge class. The one genuine contrast-risk case
  found this sub-batch (the `is_online` `Badge` with `variant="default"`
  + a raw color override) was correctly identified and left unconverted.
- Repo-wide lint warning count stayed well under the `--max-warnings`
  ratchet (1440 vs. the 1751 ceiling).

## 5. User-experience effect

**Internal admin only**, except `company-portal/[id]/verification/
page.tsx` which is corporate-customer-facing (a business user checking
their own KYB verification status). Visual effect is a shade shift on
already-semantically-colored icons, badges, progress bars, and alert
cards — no icon, label, or layout change, no change to which
tickets/drivers/payouts are shown or how they're filtered.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/support-tickets/page.tsx` | `statusClass()` (3rd duplicate) → tokens (open documented), warning banner → token | #2816 Batch 7 |
| `admin-dashboard/src/app/dashboard/monitoring/driver-panel.tsx` | Stale badge, is_online text, link color, 2 status badges → tokens/dark: | #2816 Batch 7 |
| `admin-dashboard/src/app/dashboard/monitoring/redis/page.tsx` | `memoryColor()`, 3 alert Cards → tokens | #2816 Batch 7 |
| `admin-dashboard/src/app/company-portal/[id]/verification/page.tsx` | 5 KYB-state icons + 1 card border → tokens | #2816 Batch 7 |
| `admin-dashboard/src/components/auto-payouts-panel.tsx` | `STATUS_CONFIG` (3/4 states + fallback), empty-state icon → tokens | #2816 Batch 7 |

## 7. Before / after

```tsx
// monitoring/redis/page.tsx — memoryColor() (before)
function memoryColor(percent: number | null | undefined): string {
    if (percent == null) return "bg-muted";
    if (percent >= 85) return "bg-red-500";
    if (percent >= 60) return "bg-yellow-500";
    return "bg-emerald-500";
}

// after
function memoryColor(percent: number | null | undefined): string {
    if (percent == null) return "bg-muted";
    if (percent >= 85) return "bg-destructive";
    if (percent >= 60) return "bg-warning";
    return "bg-success";
}
```

```tsx
// components/auto-payouts-panel.tsx — STATUS_CONFIG (before)
const STATUS_CONFIG: Record<string, { label: string; cls: string }> = {
    completed: { label: "Completed", cls: "bg-green-500/15 text-green-700" },
    partial: { label: "Partial", cls: "bg-amber-500/15 text-amber-700" },
    running: { label: "Running", cls: "bg-blue-500/15 text-blue-700" },
    failed: { label: "Failed", cls: "bg-red-500/15 text-red-700" },
};

// after
const STATUS_CONFIG: Record<string, { label: string; cls: string }> = {
    completed: { label: "Completed", cls: "bg-success/15 text-success" },
    partial: { label: "Partial", cls: "bg-warning/15 text-warning" },
    // eslint-disable-next-line no-restricted-syntax -- "running" (in progress, neither good nor bad) has no semantic-token equivalent; must stay distinct from the other three (#2816)
    running: { label: "Running", cls: "bg-blue-500/15 text-blue-700" },
    failed: { label: "Failed", cls: "bg-destructive/15 text-destructive" },
};
```

## 8. Rollback plan

`git-revert-safe` — 5 files, all `className` string literals and
documentation comments. No data/API/schema change, no shared-component
change.

## 9. Verification performed

- [x] `npx eslint` on all 5 touched files — 0 errors, 11 warnings (all
  pre-existing, mostly a `react-hooks/set-state-in-effect` warning).
- [x] `npx tsc --noEmit` — clean, 0 errors.
- [x] `npx vitest run` — 339/339 passed.
- [x] `npm run lint` (the exact CI command) — exit 0, 1440 total
  warnings (under the 1751 ratchet).
- [x] `npm run build` (real production build) — succeeded.
- [ ] Not manually click-tested/screenshotted — same standing sandbox
  limitation as every prior change-log this session; visual-regression
  baseline still not seeded. Notable: `company-portal/[id]/
  verification/page.tsx` is corporate-customer-facing, so this gap
  applies to a customer-facing surface, not just internal admin.

## 10. Sign-off

- [x] Rollback plan is concrete and testable.
- [x] Blast radius is stated per file, not assumed.
- [x] No silent behavior change — every conversion is a color-only
  visual change on an already-semantically-meaningful element; all
  click handlers, filters, and conditional rendering are unchanged.
- [x] The one genuine contrast-risk case found this sub-batch (the
  `is_online` solid-fill `Badge`) was identified and left unconverted
  rather than risking a WCAG AA regression.
