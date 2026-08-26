# Change Impact & Risk Log — Admin color-token migration, batch 7 / sub-batch 78

Part of the #2816 hardcoded-Tailwind-color-token migration (Stage 1). See
`docs/change-log/2026-08-21-admin-color-token-migration-plan.md` for the overall plan.

## Issue/gap identified
Three `no-restricted-syntax` raw-Tailwind-color lint warnings had no documenting
`eslint-disable-next-line` comment yet:
- `document-reviewer.tsx`: two solid-fill white-text "Approve"/"Confirm approval" buttons
  (`bg-emerald-600 ... text-white`), and the notify-toggle `Bell` icon tint.
- `bulk-operations/page.tsx`: a decorative section-header accent (`text-sky-600`) on the
  "Already mapped — review to update" heading.

## Root cause
Same two categories seen throughout this migration: (1) solid-fill white-text
success-tier buttons cannot be converted — `--success` fails WCAG AA contrast against
white text in dark mode (2.02:1), confirmed in earlier sub-batches; (2) decorative
icon/heading accents that aren't success/warning/destructive signals. Both were left
unconverted correctly in prior review passes but never got the inline documentation
comment, so the lint rule kept flagging them as unreviewed.

## Fix/remediation
- `document-reviewer.tsx` lines 375/398 (solid-fill Approve buttons): documented as
  no-token-equivalent contrast-risk exceptions, no color change.
- `document-reviewer.tsx` line 427 (notify Bell icon tint): documented as decorative,
  not a status signal, no color change.
- `bulk-operations/page.tsx` line 209 (section-header accent): documented as
  decorative, no color change.

No genuine convertible success/warning/destructive signal was found in either file —
this sub-batch is documentation-only, no visual or logic change.

## Risk & impact on existing functionality
Comment-only diff — no className values changed anywhere in this commit. Grepped for
other importers: `document-reviewer.tsx` is used only by the driver-detail documents
tab; `bulk-operations/page.tsx` is a standalone route page with no other importers.
Zero blast radius beyond the two files touched.

## User experience effect
None — no visual change. Internal-admin-only surfaces (driver document review,
bulk operations).

## Files modified
| File | What changed | Why |
|---|---|---|
| `src/app/dashboard/drivers/_components/document-reviewer.tsx` | Documented 2 no-token-equivalent buttons + 1 decorative icon tint | #2816 token migration |
| `src/app/dashboard/bulk-operations/page.tsx` | Documented 1 decorative heading accent | #2816 token migration |

## Before/after snippet
```tsx
// document-reviewer.tsx — before
className={mode === "approve" ? "flex-1 bg-emerald-600 hover:bg-emerald-700 text-white" : "flex-1"}
// after
// eslint-disable-next-line no-restricted-syntax -- solid-fill white-text button; --success fails WCAG AA contrast against white text in dark mode, no safe token conversion (#2816)
className={mode === "approve" ? "flex-1 bg-emerald-600 hover:bg-emerald-700 text-white" : "flex-1"}
```

## Rollback plan
Pure comment addition — `git revert` this commit restores the prior (undocumented but
functionally identical) files with no data migration, feature flag, or config involved.

## Verification performed
- `npx eslint` on all 3 touched files: 0 errors; the 3 targeted `no-restricted-syntax`
  raw-color warnings are gone (now suppressed with documented reasons); remaining
  warnings are pre-existing, unrelated `react-hooks` warnings.
- `npx vitest run`: 339/339 tests passing across all 35 test files.
- `npm run build` (Turbopack) not re-run — pure comment-only diff, no import/module
  changes; the pre-existing, diff-unrelated `@spinr/shared` Turbopack failure was
  already root-caused against unmodified `origin/main` in sub-batch 31/PR #4371.

## What was NOT verified
- No visual regression tooling exists in this repo for the admin dashboard (standing
  gap, `ACTION_ITEMS.md`) — not applicable here since no className changed.
- Not tested against a live Supabase/staging deployment.
