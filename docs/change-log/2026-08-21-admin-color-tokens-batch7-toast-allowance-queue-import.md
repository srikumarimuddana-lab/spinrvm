# Change Impact & Risk Log — #2816 Batch 7 sub-batch 19: toast primitive, allowance dialog, driver queue, data-transfer import

## Issue/gap identified
Four more admin-dashboard files still used raw Tailwind color utilities for single-signal
success/warning/destructive indicators (one inside a shared base UI primitive) instead of the
semantic tokens.

## Root cause
Pre-dates the token migration; written against literal Tailwind palette classes.

## Fix/remediation
- `components/ui/toast.tsx` (shared shadcn/ui primitive): the destructive-variant close button's
  hover/focus colors (`text-red-300`, `hover:text-red-50`, `focus:ring-red-400`,
  `focus:ring-offset-red-600`) → `text-destructive-foreground/70`,
  `hover:text-destructive-foreground`, `focus:ring-destructive-foreground/50`,
  `focus:ring-offset-destructive`. These were always meant to be shades of the toast's own
  `destructive` variant (`bg-destructive text-destructive-foreground`, defined two blocks above in
  the same file) — the raw reds were an approximation of that palette rather than a distinct
  color choice, so this is a like-for-like token substitution, not a new design decision.
- `dashboard/corporate-accounts/[id]/members/allowance-dialog.tsx`: an inline error message
  (`text-red-600`, no prior `dark:` variant) → `text-destructive`.
- `dashboard/drivers/queue/page.tsx`: `slaTone()` (queue wait-time severity, a genuine 3-tier
  ladder: ≥24h / ≥4h / under) → `bg-destructive/15 text-destructive border-destructive/30` /
  `bg-warning/15 text-warning border-warning/30` / `bg-success/15 text-success border-success/30`;
  the "missing docs" count (red) and its "0, all clear" counterpart (emerald) → destructive/success
  tokens; the outline-style (not solid-fill) "Reject photo" button → `text-destructive
  border-destructive/30 hover:bg-destructive/10`. Left untouched: the pending/other 2-state status
  Badge (blue/amber, no clean signal fit — matches the existing `statusClass()` "open" exclusion
  precedent elsewhere), the violet "pending photo review" marker Badge (categorical, not a
  signal), and the solid-fill emerald "Approve photo" button (contrast-risk exclusion).
- `dashboard/data-transfer/ImportTab.tsx`: a `CheckCircle2` success icon and an `AlertTriangle`
  warning icon (both used to label import-result rows) → `text-success` / `text-warning`.

Verified, no change needed: `components/ui/rich-text-editor.tsx` — its only match is a rendered
`<a>` link style (`[&_a]:text-blue-600`), informational content styling rather than a signal,
matching the same precedent already applied to other rendered-content link colors in this
migration; no edits made.

## Risk & impact on existing functionality
- `components/ui/toast.tsx` is the base toast primitive used by every `useToast()` call across the
  entire admin dashboard (grepped: dozens of call sites). The change touches only the
  destructive-variant close button's hover/focus colors — the `default` variant, the toast body,
  the action button, and every non-destructive toast are completely unaffected. The new classes
  reference the same `--destructive`/`--destructive-foreground` tokens the variant itself already
  uses two lines above, so the close button stays visually coherent with the toast it's on.
- `slaTone()` and the missing-docs indicator in `drivers/queue/page.tsx` are local to that file.
- `allowance-dialog.tsx` and `ImportTab.tsx` changes are each local to their own file.

## User experience effect
All four files are internal-admin-only surfaces (toast notifications shown throughout the admin
dashboard, corporate allowance dialog, driver approval queue, bulk-data-import tab). The toast
close-button change is the widest-reaching single edit in this migration so far by call-site
count, but is purely a hover/focus color adjustment on an already-`destructive`-styled toast —
not visible unless a destructive toast's close button is actually hovered/focused, and even then
only its color changes, not its position, icon, or behavior.

## Files modified
| File | What changed | Why |
|---|---|---|
| `admin-dashboard/src/components/ui/toast.tsx` | Destructive-variant close-button hover/focus colors → destructive-foreground tokens | #2816 |
| `admin-dashboard/src/app/dashboard/corporate-accounts/[id]/members/allowance-dialog.tsx` | Error message → destructive token | #2816 |
| `admin-dashboard/src/app/dashboard/drivers/queue/page.tsx` | `slaTone()` 3-tier ladder + missing-docs indicator + reject button → destructive/warning/success tokens | #2816 |
| `admin-dashboard/src/app/dashboard/data-transfer/ImportTab.tsx` | Result-row success/warning icons → tokens | #2816 |

## Before/after snippet
```tsx
// toast.tsx ToastClose — before
"... group-[.destructive]:text-red-300 group-[.destructive]:hover:text-red-50 group-[.destructive]:focus:ring-red-400 group-[.destructive]:focus:ring-offset-red-600"
// after
"... group-[.destructive]:text-destructive-foreground/70 group-[.destructive]:hover:text-destructive-foreground group-[.destructive]:focus:ring-destructive-foreground/50 group-[.destructive]:focus:ring-offset-destructive"
```
```tsx
// drivers/queue/page.tsx slaTone() — before
if (seconds >= 86400) return "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-300 border-red-200 dark:border-red-800";
// after
if (seconds >= 86400) return "bg-destructive/15 text-destructive border-destructive/30";
```

## Rollback plan
Pure CSS class revert — `git revert` this commit; no data migration, flag, or config change. The
toast.tsx change specifically reverts to the exact prior literal classes with no other side effect.

## Verification performed
- `npx eslint` on all 4 changed files: 0 errors, 5 warnings (a pre-existing unrelated
  `react-hooks/set-state-in-effect` advisory, plus the 3 deliberately-left raw-color warnings on
  the pending/photo-review categorical badges and the solid-fill Approve button).
- `npx tsc --noEmit`: clean.
- `npx vitest run`: 35 files / 339 tests passed (no toast-specific test regressed).
- `npm run build`: **production build completed successfully**.

## What was NOT verified
No visual-regression tooling exists in this repo (standing gap) — the toast close-button color
change is the kind of visually-subtle, widely-reached edit that tooling would normally catch, and
it was reasoned about (matching the variant's own existing token pair) rather than screenshotted.
Not tested by actually triggering a destructive toast in a running browser session — only static
review of the class list and the variant definition it derives from.
