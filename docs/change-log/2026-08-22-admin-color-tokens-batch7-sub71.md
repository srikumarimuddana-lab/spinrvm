# Change Impact & Risk Log — Admin color-token migration, batch 7 / sub-batch 71

Part of the #2816 hardcoded-Tailwind-color-token migration (Stage 1). See
`docs/change-log/2026-08-21-admin-color-token-migration-plan.md` for the overall plan.

## Issue/gap identified
`bulk-operations/page.tsx` had two "commit succeeded" summary card borders and a
"needs review" match-type badge still on fixed Tailwind shades instead of the shared
semantic tokens.

## Root cause
These sections predate the shared `--success`/`--warning` tokens. The success-card
border was inconsistent with its own already-tokenized `CheckCircle2` icon
(`text-success`) inside the same card.

## Fix/remediation
- Both "commit succeeded" summary cards (`committedSummary`, CSV validate flow and
  rider-import flow) → `border-success/40`, matching the already-tokenized success icon
  inside each.
- Phone-match-type badge: `protected_skip` ("Skipped — needs review") → `bg-warning/15
  text-warning` — this is a caution requiring manual review, not a hard failure, so
  warning fits better than destructive.

Left untouched (categorical/no-token-equivalent, each now documented with an inline
`eslint-disable-next-line`, confirmed by review not silently skipped): the same badge's
`driver` (orange) and rider (blue, the else branch) variants — these distinguish match
*type* (driver vs rider), not severity.

Also reviewed and left untouched:
- `audit-logs/page.tsx`'s `ACTION_CONFIG` — already a fully-documented categorical
  exception (~50-entry audit-event-type map) from a prior sub-batch; nothing to do.
- `venues/page.tsx`'s pickup-point row highlight (amber-when-selected) and numbered
  marker badge (amber-selected / sky-blue-unselected) — a decorative UI
  selection-state highlight for the map-editing point list, not a status signal.

## Risk & impact on existing functionality
All edits are within `app/dashboard/bulk-operations/page.tsx`. Grepped for other
importers: it's a leaf route page (`app/dashboard/bulk-operations/page.tsx`), not
imported elsewhere. No shared-component blast radius. No props, state shape, or
exported symbols changed — pure Tailwind class-string substitutions.

## User experience effect
Visually equivalent color substitutions (bg/text/border pairs map to the same hue
family at equivalent opacity). No layout, copy, or behavior change. Admin-portal-facing
only, on the bulk-operations tooling page.

## Files modified
| File | What changed | Why |
|---|---|---|
| `src/app/dashboard/bulk-operations/page.tsx` | Two success-summary card borders, "needs review" match-type badge → `--success`/`--warning`; documented driver/rider match-type badge exceptions | #2816 token migration |

## Before/after snippet
```tsx
// protected_skip match-type badge — before
it.match_type === "protected_skip"
    ? "bg-red-100 text-red-800 dark:bg-red-900 dark:text-red-200"
    : ...
// after
it.match_type === "protected_skip"
    ? "bg-warning/15 text-warning"
    : ...
```

## Rollback plan
Pure CSS class-string revert — `git revert` this commit restores the prior classes with
no data migration, feature flag, or config involved.

## Verification performed
- `npx eslint` on the file: 0 errors, 2 pre-existing warnings (both unrelated to this
  sub-batch's edits — a raw-color warning at line 209 not touched here, and an unused
  export).
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
- `audit-logs/page.tsx` and `venues/page.tsx` were reviewed but not edited — their
  matches are an already-documented categorical exception and a decorative
  selection-state highlight respectively, not genuine signals.
