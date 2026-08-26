# Change Impact & Risk Log — Admin color-token migration, batch 7 / sub-batch 69

Part of the #2816 hardcoded-Tailwind-color-token migration (Stage 1). See
`docs/change-log/2026-08-21-admin-color-token-migration-plan.md` for the overall plan.

## Issue/gap identified
`ride-detail-modal.tsx` had several genuine status-signal color usages still on fixed
Tailwind shades instead of the shared semantic tokens: the payment-status badge
(paid/waived_admin vs failed vs pending), the dispatch-offer outcome badges and their
summary counts (accepted/declined/expired), the flag-reason chip, the complaint-category
chip, and the "Confirm Cancel" button.

## Root cause
These sections predate the shared `--success`/`--warning`/`--destructive` tokens
introduced in `globals.css`.

## Fix/remediation
- Payment-status badge: paid/waived_admin → `bg-success/15 text-success`; failed →
  `bg-destructive/15 text-destructive`; else (pending) → `bg-warning/15 text-warning`.
- `OFFER_META` (dispatch-offer outcome badges) and its mirrored summary-count array:
  accepted → success, declined → destructive, expired ("Ignored") → warning.
  `preempted` (blue) left on its fixed shade with an `eslint-disable-next-line` — it's a
  neutral dispatch outcome, not a success/warning/destructive signal, and no dedicated
  token exists for it (same "no neutral/info tier" pattern applied elsewhere in this
  migration, e.g. `PAYOUT_STATUS_STYLE`'s "processing" branch).
- Flag-reason chip → `bg-destructive/15 text-destructive` (a flag is inherently a
  negative marker — same reasoning as the established rejection-reason-box conversions
  elsewhere in this migration).
- Complaint-category chip → `bg-warning/15 text-warning` (this row's icon and status
  badge were already converted to warning/success tokens in a prior sub-batch; this
  keeps the row internally consistent).
- "Confirm Cancel" button → standard shadcn destructive pattern:
  `bg-destructive text-destructive-foreground hover:bg-destructive/90`.

Left untouched (decorative/established exclusions, confirmed by review not silently
skipped):
- `STATUS_META` (7-state ride-lifecycle hero badge) and `STATUS_CONFIG` in
  `ride-ui-helpers.tsx` — both already documented categorical exceptions from prior work.
- `PHASE_COLORS` (driver-phase label map: navigating_to_pickup/arrived_at_pickup/
  trip_in_progress/online_idle/unknown) — all "in progress" variants, no
  success/warning/destructive signal among them.
- Pickup/dropoff and rider/driver identity colors throughout (blue/red, blue/emerald) —
  established fixed-role conventions, not status signals.
- Money-category differentiation (driver total emerald / platform net violet /
  incentives amber) — same decorative category-differentiation pattern excluded
  elsewhere (e.g. `ride-stats-cards.tsx`, `earnings/page.tsx`).
- `ride-ui-helpers.tsx`'s `TL` timeline-dot red/emerald — a fixed pickup-vs-dropoff-style
  role convention, not a state signal.
- `ride-list.tsx`'s rider(blue)/driver(emerald) avatar icons — decorative identity,
  consistent with the same exclusion applied elsewhere.
- `company-login/page.tsx`, `company-signup/page.tsx`, `company-portal/page.tsx` — their
  only match is a decorative emerald "Building2" brand icon tile used consistently
  across all three corporate-portal entry pages; not a status signal.

## Risk & impact on existing functionality
All edits are within `ride-detail-modal.tsx`, a single dialog component. Grepped for
other importers: it's only imported by `app/dashboard/rides/page.tsx`. No shared-hook
or shared-component blast radius. No props, state shape, or exported symbols changed —
pure Tailwind class-string substitutions to already-approved, contrast-verified tokens.

## User experience effect
Visually equivalent color substitutions only (bg/text pairs map to the same hue family
at equivalent opacity). No layout, copy, or behavior change. Admin-portal-facing only —
this modal is only reachable from the rides admin dashboard.

## Files modified
| File | What changed | Why |
|---|---|---|
| `src/app/dashboard/rides/_components/ride-detail-modal.tsx` | Payment-status badge, `OFFER_META` + summary counts, flag-reason chip, complaint-category chip, Confirm Cancel button → `--success`/`--warning`/`--destructive` tokens | #2816 token migration |

## Before/after snippet
```tsx
// Payment-status badge — before
ride.payment_status === "paid" || ride.payment_status === "waived_admin"
    ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400"
    : ride.payment_status === "failed"
        ? "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400"
        : "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400"
// after
ride.payment_status === "paid" || ride.payment_status === "waived_admin"
    ? "bg-success/15 text-success"
    : ride.payment_status === "failed"
        ? "bg-destructive/15 text-destructive"
        : "bg-warning/15 text-warning"
```

## Rollback plan
Pure CSS class-string revert — `git revert` this commit restores the prior classes with
no data migration, feature flag, or config involved.

## Verification performed
- `npx eslint` on the file: 0 errors, 49 pre-existing warnings (no new warnings on any
  line this sub-batch edited — spot-checked the diff lines against the eslint output).
- `npx vitest run`: 339/339 tests passing across all 35 test files.
- `npm run build` (Turbopack) not re-run — the pre-existing, diff-unrelated
  `@spinr/shared` "Unknown module type" Turbopack failure was already root-caused
  against unmodified `origin/main` in sub-batch 31/PR #4371; this sub-batch is plain
  Tailwind class-string edits with no import/module changes.

## What was NOT verified
- No visual regression tooling exists in this repo for the admin dashboard (standing
  gap, `ACTION_ITEMS.md`) — token substitutions were reasoned about against previously
  contrast-verified token values, not screenshotted.
- Not tested against a live Supabase/staging deployment — only against the existing
  mocked `vitest` fixtures.
- `company-login/page.tsx`, `company-signup/page.tsx`, `company-portal/page.tsx` were
  reviewed but not edited — their only raw-color match is a decorative brand icon, not
  a genuine signal, so no change was made.
