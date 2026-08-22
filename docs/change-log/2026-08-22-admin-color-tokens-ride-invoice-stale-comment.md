# Change Impact & Risk Log — ride-invoice.tsx stale eslint-disable cleanup

Follow-up to the #2816 hardcoded-Tailwind-color-token migration final sweep
(`docs/change-log/2026-08-22-admin-color-tokens-batch7-final-sweep.md`).

## Issue/gap identified
`ride-invoice.tsx` line 470 carried an `eslint-disable-next-line no-restricted-syntax`
comment above the "Download PDF" button, but that button already uses
`border-success/40 text-success` semantic tokens — no raw-color literal remained on the
line, so the suppression comment was dead and eslint flagged it as an "unused
eslint-disable directive."

## Root cause
An earlier sub-batch (prior to the final parallel sweep) converted this button's raw
colors to tokens but left the documenting comment in place instead of removing it once
the conversion made the suppression unnecessary.

## Fix/remediation
Removed the single dead comment line. No className or logic changed.

## Risk & impact on existing functionality
Single-line comment deletion in one file (`rides/_components/ride-invoice.tsx`); no
other files reference this comment. Zero blast radius.

## User experience effect
None — comment-only removal, no visual or behavioral change.

## Files modified
| File | What changed | Why |
|---|---|---|
| `src/app/dashboard/rides/_components/ride-invoice.tsx` | Removed 1 stale/unused `eslint-disable-next-line` comment | Cleanup, #2816 |

## Before/after snippet
```tsx
// before
{/* eslint-disable-next-line no-restricted-syntax -- decorative secondary-button accent (outline vs the primary "Send" button above), not a success/warning/destructive signal (#2816) */}
<button onClick={handleDownload} disabled={downloading}
// after
<button onClick={handleDownload} disabled={downloading}
```

## Rollback plan
`git revert` — single comment line, no data/config/migration involved.

## Verification performed
- `npx eslint src/app/dashboard/rides/_components/ride-invoice.tsx`: 0 warnings, 0
  errors (previously 1 "unused eslint-disable directive" warning).
- `npx vitest run`: 339/339 tests passing across all 35 test files.
- `npm run build` not re-run — single comment-line deletion, no import/module changes.

## What was NOT verified
- No visual regression tooling exists for the admin dashboard (standing gap,
  `ACTION_ITEMS.md`) — not applicable, no className changed.
