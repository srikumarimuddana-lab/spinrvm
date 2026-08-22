# Change Impact & Risk Log — Admin color-token migration, batch 7 / sub-batch 68 (partial pass, cont.)

Part of the #2816 hardcoded-Tailwind-color-token migration (Stage 1). See
`docs/change-log/2026-08-21-admin-color-token-migration-plan.md` for the overall plan,
and sub-batches 61-65 for prior partial passes on this same file (still open as
PRs #4402-#4406 at the time of this sub-batch).

This sub-batch deliberately scoped to lines **not** already covered by the still-open
sub-batch 61-65 PRs (the Rides tab's row-cap notice, `QuickStat`'s amber sub-tone, and
the `DocCard` component's rejection-reason box and Approve/Reject outline buttons) to
avoid producing duplicate diffs on lines those PRs already touch. The Payout-method/
Tax & Identity badges and KYC banners visible in this fresh `origin/main` checkout are
already fixed in sub-batch 63/65's PRs and were intentionally left alone here.

## Issue/gap identified
- The Rides tab's "showing N of total M" row-cap notice used a fixed amber shade.
- `QuickStat`'s `subTone="amber"` branch used a fixed amber shade.
- `DocCard`'s rejection-reason display box and its outline-style Approve/Reject
  buttons used fixed red/emerald shades.

## Root cause
Same as prior sub-batches: these sections predate the shared semantic tokens.

## Fix/remediation
- Rides tab row-cap notice → `text-warning`.
- `QuickStat` amber sub-tone → `text-warning`.
- `DocCard` rejection-reason box → `text-destructive bg-destructive/10`.
- `DocCard` Approve/Reject outline buttons → `text-success border-success/30
  hover:bg-success/10` / `text-destructive border-destructive/30
  hover:bg-destructive/10` — outline style, so the dark-mode `--success` contrast risk
  that blocks solid-fill buttons elsewhere does not apply here.

Left untouched (established contrast-risk exclusion, all three branches kept
consistent): `DocCard`'s top-right status badge — a solid-fill white-text ternary
(`bg-emerald-500`/`bg-red-500`/`bg-amber-500`, all `text-white`) for approved/
rejected/pending. Converting only the destructive/warning branches while leaving the
emerald branch as a fixed shade (required by the established dark-mode contrast-risk
exclusion) would leave the badge's three states styled inconsistently with each other;
left as one hand-picked set rather than a partial conversion.

## Risk & impact on existing functionality
All edits are within `drivers/page.tsx`, not imported elsewhere — no shared-component
blast radius. No props, state shape, or exported symbols changed.

## User experience effect
Purely color-token substitutions to visually equivalent (already-approved,
contrast-verified) tokens. No layout, copy, or behavior change. Admin-portal-facing
only.

## Files modified
| File | What changed | Why |
|---|---|---|
| `src/app/dashboard/drivers/page.tsx` | Rides-tab row-cap notice, `QuickStat` amber sub-tone, `DocCard` rejection box and Approve/Reject outline buttons → `--warning`/`--destructive`/`--success` | #2816 token migration (partial pass, continued) |

## Before/after snippet
```tsx
// DocCard Approve/Reject buttons — before
<Button variant="outline" size="xs" className="flex-1 text-emerald-600 dark:text-emerald-400 border-emerald-200 dark:border-emerald-800 hover:bg-emerald-50 dark:hover:bg-emerald-900/20" ...>Approve</Button>
<Button variant="outline" size="xs" className="flex-1 text-red-600 dark:text-red-400 border-red-200 dark:border-red-800 hover:bg-red-50 dark:hover:bg-red-900/20" ...>Reject</Button>
// after
<Button variant="outline" size="xs" className="flex-1 text-success border-success/30 hover:bg-success/10" ...>Approve</Button>
<Button variant="outline" size="xs" className="flex-1 text-destructive border-destructive/30 hover:bg-destructive/10" ...>Reject</Button>
```

## Rollback plan
Pure CSS class-string revert — `git revert` this commit restores the prior classes
with no data migration, feature flag, or config involved.

## Verification performed
- `npx eslint` on the file: 0 errors. Remaining warnings include: (a) the confirmed
  contrast-risk `DocCard` status badge left as-is (2 lines), (b) sections already
  fixed in still-open sub-batch 61-65 PRs (not yet in this fresh checkout), and (c)
  unrelated pre-existing `react-hooks` warnings — no new warnings on any line this
  sub-batch edited.
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
- This remains a partial pass — deliberately scoped away from lines already covered
  by sub-batches 61-65's still-open PRs, to avoid duplicate diffs. Once those PRs
  merge, a final sweep of this file should confirm no genuine signals remain
  unconverted.
