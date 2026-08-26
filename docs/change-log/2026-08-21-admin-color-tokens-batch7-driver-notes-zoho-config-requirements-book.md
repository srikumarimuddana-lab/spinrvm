# Change Impact & Risk Log — #2816 Batch 7 sub-batch 14: driver notes, Zoho config, document requirements, company booking

## Issue/gap identified
Four more admin-dashboard files still used raw Tailwind color utilities for single-signal
success/warning/destructive indicators instead of the semantic tokens.

## Root cause
Pre-dates the token migration; written against literal Tailwind palette classes.

## Fix/remediation
- `drivers/_components/driver-notes.tsx`: `CATEGORIES` (general/warning/document/status_change/complaint,
  5 distinct note types) documented with a block `eslint-disable`/`eslint-enable` comment — same
  "categorical map, too many states for 3 tokens" treatment used elsewhere. Converted the per-note
  delete-icon hover state (`hover:text-red-500` → `hover:text-destructive`). Left the solid-fill red
  "Delete" `AlertDialogAction` untouched (contrast-risk exclusion).
- `support-tickets/_components/zoho-config-card.tsx`: connected/not-connected status badges
  (`bg-emerald-100 text-emerald-800` / `bg-amber-100 text-amber-800`) → `bg-success/15 text-success` /
  `bg-warning/15 text-warning`; 3 identical "(saved)" credential-field indicators
  (`text-emerald-600 dark:text-emerald-400`) → `text-success`. Left the signature-preview box
  (`bg-white dark:bg-zinc-950`) untouched — a neutral rendering surface for arbitrary
  `dangerouslySetInnerHTML` content, not a signal color, and outside the raw-color-utility regex
  the migration lint rule targets.
- `dashboard/documents/requirements/page.tsx`: converted the per-row delete icon
  (`text-red-500` → `text-destructive`). Left the "Required" stat-card number (`text-red-600`) as a
  decorative Required-vs-Optional categorical differentiation, not a success/warning/destructive
  signal — same reasoning as the multi-column money/KPI differentiation exclusion used elsewhere
  in this migration (the "Optional" sibling stat already uses `text-muted-foreground`, i.e. the
  pair is a category label, not a pass/fail state).
- `company-portal/[id]/book/page.tsx` (**corporate-customer-facing**): address-confirmation text
  (`text-emerald-700`, previously missing any `dark:` pairing) → `text-success`; booking-success
  `CheckCircle2` icon (`text-emerald-600`) → `text-success`.

## Risk & impact on existing functionality
Color-only class swaps; no logic, props, or data flow changed.
- `CATEGORIES` in `driver-notes.tsx` is local to that file, not shared.
- `zoho-config-card.tsx` is the Zoho Help Desk integration settings card; confirmed it is used only
  from `support-tickets/page.tsx`'s settings tab (grepped) — no other importer.
- `documents/requirements/page.tsx` and `company-portal/[id]/book/page.tsx` changes are each local
  to their own file.

## User experience effect
- `company-portal/[id]/book/page.tsx` is corporate-customer-facing — the confirmed-address text
  and booking-success icon now use the theme-consistent `--success` token instead of a
  dark-mode-unaware literal color (previously this text had no `dark:` variant at all, so it would
  have rendered with poor contrast in dark mode — this is a real fix, not just a token swap).
- All other changes are internal-admin-only and purely cosmetic.

## Files modified
| File | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/drivers/_components/driver-notes.tsx` | `CATEGORIES` documented as exclusion; delete-icon hover → destructive token | #2816 |
| `admin-dashboard/src/app/dashboard/support-tickets/_components/zoho-config-card.tsx` | Connected/not-connected badges + 3 "(saved)" indicators → success/warning tokens | #2816 |
| `admin-dashboard/src/app/dashboard/documents/requirements/page.tsx` | Delete icon → destructive token | #2816 |
| `admin-dashboard/src/app/company-portal/[id]/book/page.tsx` | Address-confirmation text + success icon → success token (fixes a missing dark-mode variant) | #2816 |

## Before/after snippet
```tsx
// company-portal/[id]/book/page.tsx — before (no dark: variant at all)
{value && <p className="mt-1 truncate text-xs text-emerald-700">✓ {value.address}</p>}
// after
{value && <p className="mt-1 truncate text-xs text-success">✓ {value.address}</p>}
```
```tsx
// zoho-config-card.tsx — before
<Badge className="ml-2 bg-emerald-100 text-emerald-800 hover:bg-emerald-100">
// after
<Badge className="ml-2 bg-success/15 text-success hover:bg-success/15">
```

## Rollback plan
Pure CSS class revert — `git revert` this commit; no data migration, flag, or config change.

## Verification performed
- `npx eslint` on all 4 changed files: 0 errors, 15 warnings (all pre-existing unrelated
  `react-hooks/set-state-in-effect`/`exhaustive-deps` advisories, plus the 2 deliberately-left
  raw-color warnings on the solid-fill delete buttons and the "Required" stat).
- `npx tsc --noEmit`: clean.
- `npx vitest run`: 35 files / 339 tests passed.
- `npm run build`: **production build completed successfully**.

## What was NOT verified
No visual-regression tooling exists in this repo (standing gap). Colors were reasoned about
against the existing token definitions, not screenshotted. Not tested against a live
Supabase-backed corporate-portal booking session; only static review of the conditional logic.
