# Change Impact & Risk Log — Admin color-token migration, batch 7 / sub-batch 72

Part of the #2816 hardcoded-Tailwind-color-token migration (Stage 1). See
`docs/change-log/2026-08-21-admin-color-token-migration-plan.md` for the overall plan.

## Issue/gap identified
`quests/page.tsx` had a decorative-looking but generically-styled progress-bar fill on
a raw `bg-blue-500` instead of the `--primary` token already used for the equivalent
progress element elsewhere in the codebase. `staff/page.tsx`'s "Reset MFA" confirmation
button (a disruptive, security-sensitive action — wipes 2FA and signs the staff member
out everywhere) used a fixed orange fill instead of the standard destructive-action
button pattern used for other high-risk confirm actions in this migration.

## Root cause
These predate the shared tokens (`--primary` was already available; the button just
hadn't been brought in line with the established destructive-confirm-button pattern).

## Fix/remediation
- `quests/page.tsx`: driver-quest-progress bar fill → `bg-primary` (was `bg-blue-500`),
  matching the semantically-equivalent progress bars elsewhere in the codebase.
- `staff/page.tsx`: "Reset MFA" `AlertDialogAction` → standard shadcn destructive
  pattern (`bg-destructive text-destructive-foreground hover:bg-destructive/90`) — this
  is a disruptive, audit-logged security action (wipes MFA + signs out everywhere),
  matching how other high-risk confirm actions are styled throughout this migration
  (e.g. driver-notes.tsx, support/_tabs delete-confirmation buttons).

Left untouched (already-documented categorical exceptions, confirmed by review not
silently skipped):
- `quests/page.tsx`'s `STATUS_COLORS` (active/completed/claimed/expired quest-lifecycle
  map) — already fully documented per-entry from a prior sub-batch.
- `quests/page.tsx`'s amber Trophy icons and reward-amount text — decorative
  quests/incentives gold-reward branding, the same feature-branding exclusion applied
  to this surface in prior sub-batches (e.g. service-areas.tsx).
- `staff/page.tsx`'s `ROLE_COLORS` (5-state admin-role badge map) — already fully
  documented as a categorical exception from a prior sub-batch.

## Risk & impact on existing functionality
`quests/page.tsx`'s progress bar and `staff/page.tsx`'s MFA-reset button are each
leaf-page-local elements (both files are route pages, not shared components). Grepped
for other importers of either file: none — both are `app/dashboard/*/page.tsx` route
entry points. No shared-component blast radius. No props, state shape, or exported
symbols changed — pure Tailwind class-string substitutions.

## User experience effect
The progress-bar fill changes hue from a fixed blue to the app's primary accent color
(a visually near-equivalent, already-used-elsewhere token). The "Reset MFA" button
changes from orange to the standard destructive red — a more accurate signal for a
disruptive, irreversible-until-re-enrollment security action, consistent with how
every other high-risk confirm action in the admin portal is styled. No layout, copy,
or functional behavior change to either.

## Files modified
| File | What changed | Why |
|---|---|---|
| `src/app/dashboard/quests/page.tsx` | Driver-quest progress-bar fill → `--primary` | #2816 token migration |
| `src/app/dashboard/staff/page.tsx` | "Reset MFA" confirm button → standard destructive pattern | #2816 token migration |

## Before/after snippet
```tsx
// staff/page.tsx "Reset MFA" button — before
<AlertDialogAction onClick={confirmMfaReset} className="bg-orange-600 hover:bg-orange-700">Reset MFA</AlertDialogAction>
// after
<AlertDialogAction onClick={confirmMfaReset} className="bg-destructive text-destructive-foreground hover:bg-destructive/90">Reset MFA</AlertDialogAction>
```

## Rollback plan
Pure CSS class-string revert — `git revert` this commit restores the prior classes with
no data migration, feature flag, or config involved.

## Verification performed
- `npx eslint` on both files: 0 errors, 9 pre-existing warnings (all unrelated to this
  sub-batch's edits — react-hooks/a11y warnings pre-dating this change).
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
- The "Reset MFA" button's re-classification from orange to destructive-red is a
  judgment call (there is no explicit prior "security action" token convention beyond
  the delete-confirmation pattern this mirrors) — flagged here for visibility rather
  than asserted as unambiguous.
