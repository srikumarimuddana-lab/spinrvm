# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-21 |
| Author | Claude Code (session, vikas@ngitservices.com) |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | (see PR) |
| Related issue or gap ID | #2816 Stage 1, Batch 7 (sub-batch 2) — see `docs/change-log/2026-08-21-admin-color-token-migration-plan.md` |

## 1. Issue / gap identified

Continuing the #2816 hardcoded-Tailwind-color-token migration. This
sub-batch covers `cloud-messaging/page.tsx` (28 broken lines),
`staff/page.tsx` (14), `quests/page.tsx` (14), `disputes/page.tsx` (15).
`audit-logs/page.tsx` (49) and `app/track/[rideId]/page.tsx` (33) — the
two largest files in the re-scoped remaining-file list — were confirmed
out of scope: `audit-logs/page.tsx` was already fully processed in Batch
6 (its residual count is the documented `ACTION_CONFIG` exclusion), and
`app/track/[rideId]/page.tsx` is a public, unauthenticated rider tracking
link, not part of the admin dark-mode surface (see finding below).

## 2. Root cause / findings

- **`app/track/[rideId]/page.tsx` — excluded from the migration
  entirely.** This route renders for anyone with a share link, is wrapped
  in the root layout's `ThemeProvider defaultTheme="dark"` like every
  other route, yet deliberately uses zero `dark:` variants across all 469
  lines — a public tracking page must render consistently regardless of
  the admin app's theme or the visitor's system preference. Converting
  any of its raw colors to dashboard semantic tokens would risk pulling
  dark-mode values into a page designed to be theme-independent. No lines
  touched.
- **`cloud-messaging/page.tsx`**: `NOTIFICATION_TYPES` (5-state
  categorical notification-type map: info/alert/surge/promotion/system
  across blue/amber/purple/pink/gray — no natural success/warning/
  destructive fit) suppressed with `eslint-disable-next-line` + reason.
  `STATUS_CONFIG` (5-state message-status map): `sent`/`failed`/
  `pending`/`cancelled` converted to `success`/`destructive`/`warning`/
  `muted` tokens; `scheduled` (blue — a neutral future-state, not
  good/bad) suppressed individually. 2 real fixes in the message-history
  table (`successful`/`failed_count` counts, outline-styled, not
  solid-fill). 1 unknown-status Badge fallback (`bg-zinc-500/15` →
  `bg-muted`, matching the Batch 6 precedent). 1 progress-bar fill
  (`bg-emerald-500` → `bg-success`, no text-contrast concern since it's a
  colored bar, not text-on-fill). Left untouched: a 6-item KPI-
  differentiation stat row (categorical legend, same precedent as
  earlier batches) and 6 decorative section/dialog-header icons.
- **`staff/page.tsx`**: `ROLE_COLORS` (5-state admin-role badge map:
  super_admin/operations/support/finance/custom) — categorical role
  differentiation, not a state color (same class as Rider/Driver role
  badges left alone in Batch 7 sub-batch 1) — given house-convention
  `dark:` pairing (it was missing entirely, a real dark-mode gap) rather
  than token conversion, since "which admin role" has no success/
  warning/destructive meaning. 6 real token fixes: a DISABLED badge
  (yellow → `warning`), an MFA-enabled badge (green → `success`), a
  "Reset MFA" icon-button hover/icon (orange → `warning`, both outline
  ghost-button, not solid-fill), an active/disable toggle icon pair
  (yellow/green → `warning`/`success`), and a delete icon-button hover/
  icon (red → `destructive`). Left untouched: 2 solid-fill white-text
  `AlertDialogAction` buttons (Reset MFA orange, Delete red — contrast-
  risk exclusion).
- **`quests/page.tsx`**: `STATUS_COLORS` (4-state quest-lifecycle map:
  active/completed/claimed/expired) — `active`/`completed`/`claimed`
  suppressed individually (a token collapse would make "completed" and
  "claimed" visually indistinguishable, which matters operationally —
  an admin needs to see unclaimed-vs-paid-out at a glance); `expired`
  converted to `muted` (unambiguous neutral state). 3 real fixes: an
  "Active Now" KPI count (green → `success`), a duplicated inline 3-way
  status badge (Expired/Active/Paused → `muted`/`success`/`muted`,
  mirroring `STATUS_COLORS`'s own treatment), and an unknown-status
  Badge fallback (`bg-gray-100` → `bg-muted`). Left untouched: a reward-
  amount dollar figure (amber — money-category-differentiation, same
  precedent as Batches 5 and 7-sub1), 2 decorative Trophy header icons,
  and a progress-bar fill (`bg-blue-500` — generic in-progress indicator,
  not inherently success/fail until 100%, so left decorative).
- **`disputes/page.tsx`**: `STATUS_COLORS` (4-state dispute-lifecycle
  map: open/under_review/resolved/rejected) — all 4 map cleanly onto
  tokens (`destructive`/`warning`/`success`/`muted`) and were converted.
  A 3-card KPI row (Open/Under Review/Resolved) is a 1:1 mirror of
  `STATUS_COLORS`'s own states (not a generic differentiation legend),
  so both the icon and the count number in each card were converted to
  match. 2 disputed-amount dollar figures (table row + detail dialog)
  converted `red-600` → `destructive` — unlike a neutral revenue/reward
  figure, a dispute's `requested_amount` is genuinely a liability/at-risk
  amount, consistent with the page's own destructive-toned "Open" state.
  1 unknown-status Badge fallback (`bg-gray-100` → `bg-muted`). Left
  untouched: 2 decorative AlertTriangle header/dialog-title icons.

## 3. Fix / remediation

18 real semantic-token fixes across 4 files, 1 house-convention `dark:`
pairing addition (`ROLE_COLORS`, no token equivalent), 4 documented
suppressions (`NOTIFICATION_TYPES` block, `scheduled`, `active`/
`completed`/`claimed`), 1 file excluded from the migration entirely
(`app/track/[rideId]/page.tsx`, out of scope — public theme-independent
page). Remainder deliberately left as decorative/categorical/contrast-
risk per the established per-line rule.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to these 4 files.** No shared component,
  hook, or utility was touched. `STATUS_COLORS`, `ROLE_COLORS`, and
  `NOTIFICATION_TYPES` are each only referenced within their own file
  (grepped to confirm no cross-file import).
- All converted lines are outline/ghost buttons, badges, plain text, or
  a progress-bar fill — none were part of the excluded solid-fill white-
  text button/badge class (each individually checked for `variant=
  "outline"`/`"ghost"` or absence of a filled background before
  conversion).
- Repo-wide lint warning count stayed well under the `--max-warnings`
  ratchet (1507 vs. the 1751 ceiling).

## 5. User-experience effect

**Internal admin only.** Visual effect is a shade shift on already-
semantically-colored icons, badges, KPI numbers, and money figures — no
icon, label, or layout change, no change to which quests/disputes/staff
rows are shown or how they're filtered/sorted.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/cloud-messaging/page.tsx` | `STATUS_CONFIG` (4/5 states) → tokens, 2 table-cell colors, 1 fallback, 1 progress-bar fill → tokens; `NOTIFICATION_TYPES`/`scheduled` documented | #2816 Batch 7 |
| `admin-dashboard/src/app/dashboard/staff/page.tsx` | `ROLE_COLORS` dark: pairing added, 6 icon/badge/hover colors → tokens | #2816 Batch 7 |
| `admin-dashboard/src/app/dashboard/quests/page.tsx` | `STATUS_COLORS` (`expired`) + inline status badge + KPI count + fallback → tokens; 3 lifecycle-stage branches documented | #2816 Batch 7 |
| `admin-dashboard/src/app/dashboard/disputes/page.tsx` | `STATUS_COLORS` (4-state, all converted), 3-card KPI row, 2 amount figures, 1 fallback → tokens | #2816 Batch 7 |

## 7. Before / after

```tsx
// disputes/page.tsx — STATUS_COLORS (before)
const STATUS_COLORS: Record<string, string> = {
  open: "bg-red-100 text-red-700",
  under_review: "bg-amber-100 text-amber-700",
  resolved: "bg-green-100 text-green-700",
  rejected: "bg-gray-100 text-gray-500",
};

// after
const STATUS_COLORS: Record<string, string> = {
  open: "bg-destructive/15 text-destructive",
  under_review: "bg-warning/15 text-warning",
  resolved: "bg-success/15 text-success",
  rejected: "bg-muted text-muted-foreground",
};
```

```tsx
// quests/page.tsx — STATUS_COLORS (before)
const STATUS_COLORS: Record<string, string> = {
  active: "bg-blue-100 text-blue-700",
  completed: "bg-green-100 text-green-700",
  claimed: "bg-amber-100 text-amber-700",
  expired: "bg-gray-100 text-gray-500",
};

// after
const STATUS_COLORS: Record<string, string> = {
  // eslint-disable-next-line no-restricted-syntax -- distinct quest lifecycle stages that must stay visually distinguishable; collapsing to success/warning tokens would make "completed" and "claimed" indistinguishable (#2816)
  active: "bg-blue-100 text-blue-700",
  // eslint-disable-next-line no-restricted-syntax -- see above
  completed: "bg-green-100 text-green-700",
  // eslint-disable-next-line no-restricted-syntax -- see above
  claimed: "bg-amber-100 text-amber-700",
  expired: "bg-muted text-muted-foreground",
};
```

## 8. Rollback plan

`git-revert-safe` — 4 files, all `className` string literals and
documentation comments. No data/API/schema change, no shared-component
change.

## 9. Verification performed

- [x] `npx eslint` on all 4 touched files — 0 errors, 54 warnings (all
  pre-existing: `react-hooks` immutability/exhaustive-deps warnings and
  an unrelated `jsx-a11y/label-has-associated-control`, plus the 2
  remaining decorative-icon color warnings this batch deliberately left
  unconverted).
- [x] `npx tsc --noEmit` — clean, 0 errors.
- [x] `npx vitest run` — 339/339 passed.
- [x] `npm run lint` (the exact CI command) — exit 0, 1507 total warnings
  (under the 1751 ratchet).
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
- [x] A file was excluded from the migration on a real architectural
  finding (`app/track/[rideId]/page.tsx`'s theme-independence), stated
  explicitly rather than silently converting it or silently skipping it
  without explanation.
