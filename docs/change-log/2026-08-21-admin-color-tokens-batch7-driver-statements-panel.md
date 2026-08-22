# Change Impact & Risk Log — #2816 Batch 7 sub-batch 26: driver statements panel

## Issue/gap identified
`drivers/_components/driver-statements-panel.tsx` still used raw Tailwind color utilities for its
statement-delivery status map and a validation-error message instead of the semantic tokens.

## Root cause
Pre-dates the token migration; written against literal Tailwind palette classes.

## Fix/remediation
- `STATUS_STYLE` (sent/claimed/failed/skipped_no_email/skipped_inactive — `skipped_inactive` was
  already on the muted token): `sent` → `bg-success/15 text-success`; `failed` →
  `bg-destructive/15 text-destructive`; `skipped_no_email` → `bg-warning/15 text-warning`.
  `claimed` ("in progress", neither good nor bad) kept its raw blue with a new
  `eslint-disable-next-line` documenting why — the same treatment already applied to
  "driver_notified"/"investigating"/"running" states elsewhere in this migration.
- The date-range validation message (`text-red-600 dark:text-red-400`) → `text-destructive`.

Verified, no change needed (all already fully converted/documented in earlier passes):
`support/_tabs/lost-and-found.tsx`, `support-tickets/tickets/page.tsx`,
`support-tickets/trends/page.tsx`, `components/auto-payouts-panel.tsx` — each file's only raw-color
match is either an already-`eslint-disable`-documented "in progress" state, a generic non-signal
accent prop, or a solid-fill destructive button (contrast-risk exclusion); no edits made to any of
these four files.

## Risk & impact on existing functionality
Color-only class swaps (plus one added lint-suppression comment) — no logic, props, or data flow
changed. `STATUS_STYLE` is local to `driver-statements-panel.tsx`, not shared.

## User experience effect
Internal-admin-only screen (driver earnings-statement delivery tracking). Purely cosmetic — the
underlying delivery-status classification and date-range validation logic are unchanged.

## Files modified
| File | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/drivers/_components/driver-statements-panel.tsx` | `STATUS_STYLE` (sent/failed/skipped_no_email) + validation message → success/destructive/warning tokens | #2816 |

## Before/after snippet
```tsx
// driver-statements-panel.tsx STATUS_STYLE — before
sent: { label: "Emailed", cls: "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300" },
failed: { label: "Failed", cls: "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-300" },
skipped_no_email: { label: "No email on file", cls: "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300" },
// after
sent: { label: "Emailed", cls: "bg-success/15 text-success" },
failed: { label: "Failed", cls: "bg-destructive/15 text-destructive" },
skipped_no_email: { label: "No email on file", cls: "bg-warning/15 text-warning" },
```

## Rollback plan
Pure CSS class revert (plus one added lint-suppression comment) — `git revert` this commit; no
data migration, flag, or config change.

## Verification performed
- `npx eslint` on the changed file: 0 errors, 1 warning (a pre-existing unrelated advisory).
- `npx tsc --noEmit`: clean.
- `npx vitest run`: 35 files / 339 tests passed.
- `npm run build`: **production build completed successfully**.

## What was NOT verified
No visual-regression tooling exists in this repo (standing gap). Colors were reasoned about
against the existing token definitions, not screenshotted. Not tested against a live
Supabase-backed admin session.
