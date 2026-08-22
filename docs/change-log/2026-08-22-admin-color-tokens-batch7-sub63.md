# Change Impact & Risk Log — Admin color-token migration, batch 7 / sub-batch 63 (partial pass, cont.)

Part of the #2816 hardcoded-Tailwind-color-token migration (Stage 1). See
`docs/change-log/2026-08-21-admin-color-token-migration-plan.md` for the overall plan,
and sub-batches 61/62 for prior partial passes on this same file.

Continues the partial pass on `drivers/page.tsx` — this sub-batch covers the Payouts
tab: `PAYOUT_STATUS_STYLE`, `PayoutMetric`'s tone system, the on-hold failed-payouts
banner, the payout-method "Linked"/"No method linked" and verification badges, and the
Tax & Identity (KYC) status badge. Remaining sections (doc-requirement badges,
doc-review approve/reject buttons, ride-status history map, KYC requirements banners,
referral badges) are still deferred to a following sub-batch.

## Issue/gap identified
- `PAYOUT_STATUS_STYLE` (completed/pending/processing/failed — a genuine 4-state
  payout signal) used fixed shades instead of tokens, and `PayoutMetric`'s
  emerald/amber/red tone system (used for "Pending payout" and "Total paid out"
  metric cards) did the same.
- The on-hold failed-payouts warning banner, the payout-method linked/not-linked
  badges, the Stripe Connect verification text, and the Tax & Identity KYC status
  badge all used fixed amber/red/emerald shades instead of `--warning`/
  `--destructive`/`--success`.

## Root cause
Same as prior sub-batches: these sections predate the shared semantic tokens.

## Fix/remediation
- `PAYOUT_STATUS_STYLE`: `completed`→`bg-success/15 text-success`, `pending`→
  `bg-warning/15 text-warning`, `failed`→`bg-destructive/15 text-destructive`.
  `processing` is left as a hand-picked blue and the whole map wrapped in
  `eslint-disable`/`eslint-enable` — no semantic token exists for a neutral
  "in progress" state, the same reasoning already applied to `queue-stats.tsx`
  (sub-batch 55) and `referral-pairs.tsx` (sub-batch 53).
- `PayoutMetric` tone styles: `emerald`→`bg-success/10 border-success/30
  text-success`, `amber`→`bg-warning/10 border-warning/30 text-warning`, `red`→
  `bg-destructive/10 border-destructive/30 text-destructive`.
- On-hold banner → `border-destructive/30 bg-destructive/10`/`text-destructive`.
- Payout-method "Linked"/"No method linked" badges → `bg-success/15 text-success` /
  `bg-destructive/15 text-destructive`.
- Stripe Connect verification text (`is_verified`) → `text-success`/`text-warning`.
- Tax & Identity KYC status badge (payouts_enabled/details_submitted/requirements
  due) → `bg-success/15 text-success` / `bg-warning/15 text-warning` /
  `bg-destructive/15 text-destructive`.

## Risk & impact on existing functionality
All edits are within `drivers/page.tsx`, not imported elsewhere — no shared-component
blast radius. `PayoutMetric` and `PAYOUT_STATUS_STYLE` are local to this file (not
exported), so no external consumer is affected. No props, state shape, or exported
symbols changed.

## User experience effect
Purely color-token substitutions to visually equivalent (already-approved,
contrast-verified) tokens. No layout, copy, or behavior change. Admin-portal-facing
only.

## Files modified
| File | What changed | Why |
|---|---|---|
| `src/app/dashboard/drivers/page.tsx` | `PAYOUT_STATUS_STYLE`, `PayoutMetric` tones, on-hold banner, payout-method badges, KYC status badge → `--success`/`--warning`/`--destructive` (processing documented as an exception) | #2816 token migration + documentation (partial pass, continued) |

## Before/after snippet
```tsx
// PAYOUT_STATUS_STYLE — before
completed:  { bg: "bg-emerald-100 dark:bg-emerald-900/30", text: "text-emerald-700 dark:text-emerald-300", label: "Paid" },
pending:    { bg: "bg-amber-100 dark:bg-amber-900/30",     text: "text-amber-700 dark:text-amber-300",     label: "Pending" },
failed:     { bg: "bg-red-100 dark:bg-red-900/30",         text: "text-red-700 dark:text-red-300",         label: "Failed" },
// after
completed:  { bg: "bg-success/15", text: "text-success", label: "Paid" },
pending:    { bg: "bg-warning/15", text: "text-warning", label: "Pending" },
failed:     { bg: "bg-destructive/15", text: "text-destructive", label: "Failed" },
```

## Rollback plan
Pure CSS class-string revert (plus removing the `eslint-disable`/`eslint-enable`
block, which changes no runtime behavior) — `git revert` this commit restores the
prior classes with no data migration, feature flag, or config involved.

## Verification performed
- `npx eslint` on the file: 0 errors. Remaining warnings are in sections either
  already fixed in still-open sub-batch 61/62 PRs (which this fresh checkout doesn't
  yet include) or explicitly still deferred (listed above); no new warnings on any
  line this sub-batch edited.
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
- This remains a partial pass — remaining sections are tracked as continuing backlog,
  not silently dropped. This PR and sub-batches 61/62's PRs touch disjoint lines of
  the same file and should merge cleanly independently.
