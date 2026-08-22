# Change Impact & Risk Log — #2816 Batch 7 sub-batch 16: support tickets priority map, legal documents, referral analytics, decals

## Issue/gap identified
Four more admin-dashboard files still used raw Tailwind color utilities for single-signal
warning/destructive indicators, or needed an explicit categorical-exclusion comment, per the
#2816 migration.

## Root cause
Pre-dates the token migration; written against literal Tailwind palette classes.

## Fix/remediation
- `dashboard/support/_tabs/tickets.tsx`: `P_COLORS` (low/medium/high/urgent priority ladder, 4
  distinct severities including a "medium" that has no clean 3-token mapping) documented with an
  `eslint-disable-next-line` comment — a categorical exclusion, consistent with the file's own
  existing contrast-fix history noted in the surrounding comment block. Left the solid-fill red
  "Delete" `AlertDialogAction` untouched (contrast-risk exclusion).
- `dashboard/support/_tabs/legal-documents.tsx`: "· unsaved changes" indicator
  (`text-amber-600 dark:text-amber-400`) → `text-warning`. Left the rider/driver 2-state audience
  badge (`sky`/`emerald`) untouched — a categorical audience differentiation, not a
  success/warning/destructive signal.
- `components/referral-analytics.tsx`: top-level fetch-error banner (`text-red-600
  dark:text-red-400`) → `text-destructive`; the "Failed claims" section header `XCircle` icon
  (`text-red-500`) → `text-destructive`, a genuine failure-state header distinct from the
  decorative multi-column KPI stat row (10 stats, each with its own accent color purely to
  differentiate columns — left untouched, same reasoning as the money-category-differentiation
  exclusion used elsewhere in this migration) and the decorative `TrendingUp` chart-section icon
  (left as-is, a static label icon, not a live up/down signal).
- `dashboard/drivers/decals/page.tsx`: a `CheckCircle` "Done" status icon (`text-emerald-500`) →
  `text-success`.

Verified, no change needed: `dashboard/support-tickets/page.tsx` — its `statusClass()` categorical
status map was already fully converted/documented in an earlier pass (only "open" carries a raw
color, already wrapped in its own `eslint-disable-next-line` with a stated reason); its
`StatCard`'s generic `accent` prop (`text-blue-600`) is a non-signal highlight used across
differently-meaning callers, not a success/warning/destructive indicator, so it was left as-is —
no edits made to this file.

## Risk & impact on existing functionality
Color-only class swaps; no logic, props, or data flow changed. Each converted element is local to
its own file. `P_COLORS` and the audience badge are local `Record`/inline maps in their own
components, not shared.

## User experience effect
All four files are internal-admin-only screens (support ticket priority list, legal-document
editor, referral analytics dashboard, driver decal tracking). Purely cosmetic — the underlying
priority/status logic and labels are unchanged.

## Files modified
| File | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/support/_tabs/tickets.tsx` | `P_COLORS` documented as an intentional categorical exclusion | #2816 |
| `admin-dashboard/src/app/dashboard/support/_tabs/legal-documents.tsx` | "Unsaved changes" indicator → warning token | #2816 |
| `admin-dashboard/src/components/referral-analytics.tsx` | Fetch-error banner + "Failed claims" header icon → destructive token | #2816 |
| `admin-dashboard/src/app/dashboard/drivers/decals/page.tsx` | "Done" status icon → success token | #2816 |

## Before/after snippet
```tsx
// legal-documents.tsx — before
{dirty && <span className="ml-2 text-amber-600 dark:text-amber-400">· unsaved changes</span>}
// after
{dirty && <span className="ml-2 text-warning">· unsaved changes</span>}
```

## Rollback plan
Pure CSS class revert (plus one added lint-suppression comment) — `git revert` this commit; no
data migration, flag, or config change.

## Verification performed
- `npx eslint` on all 4 changed files: 0 errors, 22 warnings (pre-existing unrelated
  `react-hooks/set-state-in-effect` advisories, plus the deliberately-left raw-color warnings on
  the multi-column KPI stat row and the solid-fill delete button).
- `npx tsc --noEmit`: clean.
- `npx vitest run`: 35 files / 339 tests passed.
- `npm run build`: **production build completed successfully**.

## What was NOT verified
No visual-regression tooling exists in this repo (standing gap). Colors were reasoned about
against the existing token definitions, not screenshotted. Not tested against a live
Supabase-backed admin session.
