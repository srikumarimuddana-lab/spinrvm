# Change Impact & Risk Log — Admin color-token migration, batch 7 / sub-batch 60

Part of the #2816 hardcoded-Tailwind-color-token migration (Stage 1). See
`docs/change-log/2026-08-21-admin-color-token-migration-plan.md` for the overall plan.

## Issue/gap identified
- `driver-stats-cards.tsx`'s 10-tile stat grid (total/online/active/pending/
  needs_review/suspended/banned/total_rides/earnings/avg_rating) was undocumented as
  an intentional mixed exception.
- `document-reviewer.tsx`'s `statusTone` map (approved/rejected/pending — a genuine
  3-state document-review signal), the rejection-reason display box, the reject-mode
  selected button, the approve/reject-mode panel backgrounds, and the "Confirm
  rejection" button all used fixed Tailwind shades instead of the shared
  `--success`/`--warning`/`--destructive` tokens.

## Root cause
Same as prior sub-batches: these components predate the shared semantic tokens.
`driver-stats-cards.tsx`'s tile set was never flagged with a suppression comment.

## Fix/remediation
- `driver-stats-cards.tsx`: the entire `StatCard` list wrapped in
  `eslint-disable`/`eslint-enable no-restricted-syntax` — documentation only, no color
  values changed. It's a mixed set: total/online/total_rides/earnings/avg_rating are
  neutral counts (the established money-category-differentiation exclusion, same
  pattern as `dashboard/page.tsx`'s `STAT_COLOR_CLASSES`), while active/pending/
  suspended/banned/needs_review mirror the already-established driver-lifecycle-status
  categorical exclusion (`driver-action-bar.tsx`'s `STATUS_CONFIG`, `drivers/page.tsx`'s
  inline Badge ternary) — kept together as one hand-picked set rather than partially
  converted, for consistency with how that same conceptual state set was handled
  elsewhere.
- `document-reviewer.tsx`:
  - `statusTone` (approved/rejected/pending) → `bg-success/15 text-success` /
    `bg-destructive/15 text-destructive` / `bg-warning/15 text-warning` — a genuine
    3-state signal.
  - Rejection-reason display box → `bg-destructive/10 border-destructive/30
    text-destructive`.
  - Reject-mode selected button (`bg-red-600 hover:bg-red-700 text-white`) → the
    standard `bg-destructive hover:bg-destructive/90 text-destructive-foreground`
    pattern.
  - Approve-mode panel background/border → `bg-success/10 border-success/40`.
  - Reject-mode panel background/border → `bg-destructive/10 border-destructive/40`.
  - "Confirm rejection" button → the same standard destructive pattern.

Left untouched (established contrast-risk exclusion): the reject-mode conversions
above are all *outline/tinted* backgrounds or destructive-token solid buttons (already
verified safe elsewhere in this migration), but the two *approve*-mode solid white-text
buttons (`bg-emerald-600 hover:bg-emerald-700 text-white` — the mode toggle and
"Confirm approval") are left untouched: converting to `bg-success` would introduce the
dark-mode WCAG AA contrast failure (`--success` #30d158 is 2.02:1 against white text)
that the fixed emerald shade currently avoids. Also left untouched: the blue `Bell`
"notify driver" toggle icon — a small decorative icon accent next to a label.

## Risk & impact on existing functionality
Both files are standalone leaf components under `drivers/_components/` (each used by
a single parent driver-detail surface) — no shared-component blast radius. No props,
state shape, or exported symbols changed.

## User experience effect
Purely color-token substitutions to visually equivalent (already-approved,
contrast-verified) tokens for the reject-side conversions. No layout, copy, or
behavior change; the approve-side solid buttons are deliberately left as-is to avoid
introducing a contrast regression. Admin-portal-facing only.

## Files modified
| File | What changed | Why |
|---|---|---|
| `src/app/dashboard/drivers/_components/driver-stats-cards.tsx` | Entire stat-tile set documented as an intentional mixed exception | #2816 documentation, no color values changed |
| `src/app/dashboard/drivers/_components/document-reviewer.tsx` | `statusTone` map, rejection-reason box, reject-mode button/panel, "Confirm rejection" button → `--destructive`/`--success`/`--warning` tokens | #2816 token migration |

## Before/after snippet
```tsx
// document-reviewer.tsx statusTone — before
case "approved": return "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300";
case "rejected": return "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-300";
case "pending": return "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300";
// after
case "approved": return "bg-success/15 text-success";
case "rejected": return "bg-destructive/15 text-destructive";
case "pending": return "bg-warning/15 text-warning";
```
```tsx
// reject-mode button — before
className={mode === "reject" ? "flex-1 bg-red-600 hover:bg-red-700 text-white" : "flex-1"}
// after
className={mode === "reject" ? "flex-1 bg-destructive hover:bg-destructive/90 text-destructive-foreground" : "flex-1"}
```

## Rollback plan
Pure CSS class-string revert (plus removing the `eslint-disable`/`eslint-enable` block
around `driver-stats-cards.tsx`'s tile set, which changes no runtime behavior) —
`git revert` this commit restores the prior classes with no data migration, feature
flag, or config involved.

## Verification performed
- `npx eslint` on both edited files: 0 errors. Remaining warnings are the two
  deliberately-untouched solid `bg-emerald-600` approve buttons (contrast-risk
  exclusion), the decorative Bell icon, and one unrelated pre-existing
  `react-hooks/set-state-in-effect` warning — no new `no-restricted-syntax` warnings
  on any converted line.
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
