# Change Impact & Risk Log — Admin color-token migration, batch 7 / sub-batch 54

Part of the #2816 hardcoded-Tailwind-color-token migration (Stage 1). See
`docs/change-log/2026-08-21-admin-color-token-migration-plan.md` for the overall plan.

## Issue/gap identified
Four Support-tab files (`flags.tsx`, `tickets.tsx`, `complaints.tsx`, `legal-documents.tsx`)
had solid-fill destructive delete-confirmation buttons using a fixed `bg-red-600
hover:bg-red-700` instead of the standard shadcn `bg-destructive`/`text-destructive-foreground`
pattern already used elsewhere in the migration, an undocumented (but already
WCAG-verified) 4-state ticket-priority color map, and a genuine unsaved-changes warning
indicator using a fixed amber shade.

## Root cause
Same as prior sub-batches: these delete-confirmation dialogs and status indicators
predate the shared semantic tokens. `tickets.tsx`'s `P_COLORS` map was contrast-verified
against real rendered badges (see its existing code comment) but was never wrapped in an
`eslint-disable` block explaining why it stays hand-picked.

## Fix/remediation
- `flags.tsx`, `tickets.tsx`, `complaints.tsx`: delete-confirmation `AlertDialogAction`
  buttons converted from `bg-red-600 hover:bg-red-700` to the standard
  `bg-destructive text-destructive-foreground hover:bg-destructive/90` pattern (same
  conversion used repeatedly elsewhere in this migration: kyb-queue, vehicle-types,
  staff page).
- `tickets.tsx`: `P_COLORS` (ticket priority: low/medium/high/urgent) wrapped in
  `eslint-disable`/`eslint-enable no-restricted-syntax` — documentation only, no color
  values changed, since the existing code comment already establishes these were
  contrast-verified via axe against real rendered badges.
- `legal-documents.tsx`: the "· unsaved changes" indicator converted from
  `text-amber-600 dark:text-amber-400` to `text-warning` — a genuine warning signal
  (unsaved edits), not decorative.

Left untouched (established exclusions, consistent with prior sub-batches):
- `flags.tsx` line 104: `Users`/`Car` icon colors (blue/emerald) differentiating
  rider vs. driver flag-target type in a table row — decorative entity-type
  differentiation, not a status signal.
- `flags.tsx`/`complaints.tsx` dialog-title icons (`Flag`, `FileWarning`, amber) —
  decorative icon accents next to a heading.
- `complaints.tsx`'s `S_CFG` status map (open/investigating/resolved/dismissed) — already
  fully converted to `bg-warning/15`/`bg-success/15`/`bg-muted` tokens with an existing
  `eslint-disable-next-line` on the undocumented "investigating" state, from earlier work;
  no changes needed.
- `complaints.tsx` line 155: `bg-emerald-600 hover:bg-emerald-700` "Resolve" button —
  the established contrast-risk exclusion: a solid-fill white-text button using
  `bg-success` would introduce the dark-mode WCAG AA failure (`--success` #30d158 is
  2.02:1 against white text) that the fixed emerald shade currently avoids.
- `legal-documents.tsx`'s `A_CFG` rider/driver audience-tag map (sky/emerald, 2 states) —
  a plain audience label, not a status signal; comparable in kind to `flags.tsx`'s
  rider/driver icon differentiation.

## Risk & impact on existing functionality
All edits are local to individual leaf tab components under `support/_tabs/` (each
rendered only by the parent Support & Issues page's tab router) — no shared-component
blast radius. The delete-button conversions change only the button's class string, not
its `onClick` handler or any state. The `P_COLORS` eslint-disable wrap is a documentation
change with zero runtime effect (identical class strings, still evaluated the same way).

## User experience effect
Purely color-token substitutions to visually equivalent (already-approved,
contrast-verified) tokens. No layout, copy, or behavior change. Admin-portal-facing only
(Support & Issues tabs).

## Files modified
| File | What changed | Why |
|---|---|---|
| `src/app/dashboard/support/_tabs/flags.tsx` | Delete-confirmation button → `bg-destructive text-destructive-foreground hover:bg-destructive/90` | #2816 token migration |
| `src/app/dashboard/support/_tabs/tickets.tsx` | Delete-confirmation button → same destructive pattern; `P_COLORS` documented with `eslint-disable`/`eslint-enable` | #2816 token migration + documentation |
| `src/app/dashboard/support/_tabs/complaints.tsx` | Delete-confirmation button → same destructive pattern | #2816 token migration |
| `src/app/dashboard/support/_tabs/legal-documents.tsx` | "unsaved changes" indicator → `text-warning` | #2816 token migration (genuine warning signal) |

## Before/after snippet
```tsx
// delete-confirmation button — before (all three files)
<AlertDialogAction ... className="bg-red-600 hover:bg-red-700">Delete</AlertDialogAction>
// after
<AlertDialogAction ... className="bg-destructive text-destructive-foreground hover:bg-destructive/90">Delete</AlertDialogAction>
```
```tsx
// legal-documents.tsx — before
{dirty && <span className="ml-2 text-amber-600 dark:text-amber-400">· unsaved changes</span>}
// after
{dirty && <span className="ml-2 text-warning">· unsaved changes</span>}
```

## Rollback plan
Pure CSS class-string revert (plus removing the `eslint-disable` comment in
`tickets.tsx`, which changes no runtime behavior) — `git revert` this commit restores
the prior classes with no data migration, feature flag, or config involved.

## Verification performed
- `npx eslint` on all four edited files: 0 errors. Remaining warnings are all
  pre-existing/expected: the decorative icon and audience-tag exclusions documented
  above, and unrelated pre-existing `react-hooks` warnings (`set-state-in-effect`)
  already present on these files before this change.
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
