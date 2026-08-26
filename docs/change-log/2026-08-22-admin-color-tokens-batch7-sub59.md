# Change Impact & Risk Log — Admin color-token migration, batch 7 / sub-batch 59

Part of the #2816 hardcoded-Tailwind-color-token migration (Stage 1). See
`docs/change-log/2026-08-21-admin-color-token-migration-plan.md` for the overall plan.

## Issue/gap identified
- `driver-notes.tsx`'s `CATEGORIES` map (5 note-category badges) was undocumented as
  an intentional categorical exception; its delete-note icon hover color and
  delete-confirmation button used fixed red shades.
- `area-stats-table.tsx`'s per-service-area online/verified/unverified driver counts
  used fixed emerald/green/amber shades instead of `--success`/`--warning`.
- `document-upload-dialog.tsx`'s "map every file" warning used a fixed amber shade.

## Root cause
Same as prior sub-batches: these components predate the shared semantic tokens, and
`driver-notes.tsx`'s categorical map was never flagged with a suppression comment
explaining why it stays hand-picked.

## Fix/remediation
- `driver-notes.tsx`: `CATEGORIES` (general/warning/document/status_change/complaint)
  wrapped in `eslint-disable`/`eslint-enable no-restricted-syntax` — documentation
  only, no color values changed, since this badges a note *category* (several of
  which, like "document" or "general", carry no good/bad sentiment), not a live
  status. Delete-note icon hover (`hover:text-red-500` → `hover:text-destructive`)
  and the delete-confirmation button (`bg-red-600 hover:bg-red-700` → the standard
  `bg-destructive text-destructive-foreground hover:bg-destructive/90` pattern)
  converted — both are genuine destructive-action affordances.
- `area-stats-table.tsx`: `online`/`verified` counts (both positive-state counts) →
  `text-success`; `unverified` count (a caution count) → `text-warning`.
- `document-upload-dialog.tsx`: warning text → `text-warning`.

Left untouched (established exclusion): `area-stats-table.tsx`'s `total_earnings`
column (`text-emerald-600`) — a single money figure differentiating that column from
neighboring count columns, not a paired good/bad signal, consistent with the
established money-category-differentiation exclusion.

## Risk & impact on existing functionality
All three edited files are standalone leaf components under `drivers/_components/`
(no shared-component blast radius beyond their existing single parent driver-detail
consumers). No props, state shape, or exported symbols changed.

## User experience effect
Purely color-token substitutions to visually equivalent (already-approved,
contrast-verified) tokens. No layout, copy, or behavior change. Admin-portal-facing
only.

## Files modified
| File | What changed | Why |
|---|---|---|
| `src/app/dashboard/drivers/_components/driver-notes.tsx` | `CATEGORIES` documented as a categorical exception; delete-icon hover and delete-confirmation button → destructive tokens | #2816 token migration + documentation |
| `src/app/dashboard/drivers/_components/area-stats-table.tsx` | online/verified → `text-success`; unverified → `text-warning` | #2816 token migration |
| `src/app/dashboard/drivers/_components/document-upload-dialog.tsx` | warning text → `text-warning` | #2816 token migration |

## Before/after snippet
```tsx
// area-stats-table.tsx — before
<span className="text-emerald-600 font-medium">{area.online}</span>
...
<span className="text-green-600 font-medium">{area.verified}</span>
...
<span className="text-amber-600 font-medium">{area.unverified}</span>
// after
<span className="text-success font-medium">{area.online}</span>
...
<span className="text-success font-medium">{area.verified}</span>
...
<span className="text-warning font-medium">{area.unverified}</span>
```

## Rollback plan
Pure CSS class-string revert (plus removing the `eslint-disable`/`eslint-enable` block
around `CATEGORIES`, which changes no runtime behavior) — `git revert` this commit
restores the prior classes with no data migration, feature flag, or config involved.

## Verification performed
- `npx eslint` on all three edited files: 0 errors. The one remaining warning
  (`area-stats-table.tsx`'s `total_earnings` column) is the established, deliberately
  untouched money-differentiation exclusion; the rest are unrelated pre-existing
  `react-hooks` warnings — no new `no-restricted-syntax` warnings introduced by this
  change.
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
