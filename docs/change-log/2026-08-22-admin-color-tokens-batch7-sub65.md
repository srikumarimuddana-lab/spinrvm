# Change Impact & Risk Log — Admin color-token migration, batch 7 / sub-batch 65 (partial pass, cont.)

Part of the #2816 hardcoded-Tailwind-color-token migration (Stage 1). See
`docs/change-log/2026-08-21-admin-color-token-migration-plan.md` for the overall plan,
and sub-batches 61-64 for prior partial passes on this same file.

Continues the partial pass on `drivers/page.tsx` — this sub-batch covers the KYC
requirements-due/past-due banner, the payouts-disabled error banner, the
SIN-revealed notice, and the referral summary/referee-list qualified/pending
indicators. ~104 raw-color matches remain elsewhere in this large file after this
sub-batch, tracked as continuing backlog.

## Issue/gap identified
- The KYC "items needed from driver" requirements banner and the SIN-revealed
  security notice used fixed amber shades instead of `--warning`.
- The "Payouts disabled" error banner used a fixed red shade instead of
  `--destructive`.
- The referral tab's "Rewarded"/"Pending" summary figures, the "Reward earned" text,
  and the per-referee "Earned"/"In progress" badge (all a genuine qualified/pending
  2-state signal) used fixed emerald/amber shades.

## Root cause
Same as prior sub-batches: these sections predate the shared semantic tokens.

## Fix/remediation
- KYC requirements-due banner and SIN-revealed notice → `border-warning/30
  bg-warning/10`, `text-warning`, `text-warning/80`, `text-warning/60`.
- Payouts-disabled banner → `border-destructive/30 bg-destructive/10
  text-destructive`.
- Referral "Rewarded" figure, "Reward earned" text, and the qualified-state badge →
  `text-success` / `bg-success/15 text-success`.
- Referral "Pending" figure and the in-progress-state badge → `text-warning` /
  `bg-warning/15 text-warning`.

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
| `src/app/dashboard/drivers/page.tsx` | KYC requirements/SIN-revealed banners → `--warning`; payouts-disabled banner → `--destructive`; referral qualified/pending indicators → `--success`/`--warning` | #2816 token migration (partial pass, continued) |

## Before/after snippet
```tsx
// referral qualified/pending badge — before
<span className={`... ${r.qualified ? "bg-emerald-100 dark:bg-emerald-900/30 text-emerald-700 dark:text-emerald-300" : "bg-amber-100 dark:bg-amber-900/30 text-amber-700 dark:text-amber-300"}`}>
// after
<span className={`... ${r.qualified ? "bg-success/15 text-success" : "bg-warning/15 text-warning"}`}>
```

## Rollback plan
Pure CSS class-string revert — `git revert` this commit restores the prior classes
with no data migration, feature flag, or config involved.

## Verification performed
- `npx eslint` on the file: 0 errors. 180 remaining `no-restricted-syntax` warnings
  are in sections either already fixed in still-open sub-batch 61-64 PRs (which this
  fresh checkout doesn't yet include) or not yet reached by this migration; no new
  warnings on any line this sub-batch edited.
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
- This remains a partial pass — ~104 raw-color matches remain elsewhere in this large
  file (vehicle-history, other detail tabs not yet swept), tracked as continuing
  backlog for this migration, not silently dropped. This PR and sub-batches 61-64's
  PRs touch disjoint lines of the same file and should merge cleanly independently.
