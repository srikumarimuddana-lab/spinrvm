# Change Impact & Risk Log — #2816 Batch 7 sub-batch 12: corporate/company policy pages, driver map, driver registration, venues

## Issue/gap identified
Five more admin-dashboard files under the #2816 hardcoded-Tailwind-color migration still used raw
Tailwind color utilities (`emerald`/`green`/`yellow`/`gray`/`amber`/`red`) for single-signal
success/warning/destructive indicators instead of the `--success`/`--warning`/`--destructive`
semantic tokens, so they don't automatically track dark-mode/theme changes to those tokens.

## Root cause
These files predate the token migration and were written against literal Tailwind palette
classes, the original pattern used across the codebase before the semantic tokens existed.

## Fix/remediation
Converted single-signal color usages to semantic tokens, file by file:
- `corporate-accounts/[id]/policy/page.tsx` (internal admin): "Configured"/"Not set" policy-status
  badges → `bg-success/15 text-success` / `bg-warning/15 text-warning`; "✓ Policy saved" confirmation
  text → `text-success`.
- `company-portal/[id]/policy/page.tsx` (**corporate-customer-facing**): identical
  Configured/Not-set badge (was `bg-emerald-100 text-emerald-800` / `bg-gray-200 text-gray-700`) →
  `bg-success/15 text-success` / `bg-muted text-muted-foreground`; save-feedback text → `text-success`.
- `components/driver-map.tsx`: legend "Online" count dot (`bg-emerald-500`) → `bg-success`. Left the
  "Offline" dot (`bg-zinc-400`) as a neutral/decorative color (not a success/warning/destructive
  signal) — same reasoning as the module's own online/offline hex marker colors used directly in
  MapLibre paint expressions, which are out of scope for a Tailwind-class migration.
- `register/driver/page.tsx` (**public driver signup form**): "Application Submitted!" success
  confirmation icon+wrapper (`bg-green-100` / `text-green-600`) → `bg-success/15` / `text-success`.
- `dashboard/venues/page.tsx`: inactive-venue warning note border (`border-amber-500`) →
  `border-warning`; two identical "Delete" icon-button patterns (`text-red-600 hover:bg-red-50
  dark:hover:bg-red-900/20`) → `text-destructive hover:bg-destructive/10`.

Left untouched (verified, no change needed):
- `venues/page.tsx` numbered pickup-point marker button: solid-fill white-text buttons
  (`bg-amber-500 text-white` selected / `bg-sky-500 text-white hover:bg-sky-600` unselected) — same
  contrast-risk exclusion as every other solid-fill white-text button in this migration (dark-mode
  `--success`/`--warning` fail WCAG AA against white text).
- `venues/page.tsx` selected-row highlight (`bg-amber-50 dark:bg-amber-900/20 ring-1 ring-amber-400`)
  — a selection-state highlight, not a success/warning/destructive signal; already dark-aware.

## Risk & impact on existing functionality
All five changes are colour-only class swaps on isolated JSX elements — no logic, props, or data
flow changed. Blast radius:
- The two policy-status Badge patterns (`corporate-accounts/[id]/policy/page.tsx` and its
  company-portal counterpart) are each defined and used only in their own file; not shared components.
- `driver-map.tsx` is imported by `dashboard/drivers/page.tsx` and `dashboard/service-areas/page.tsx`
  (grepped both) — only the legend swatch class changed, not the map/marker rendering logic, so both
  callers are unaffected beyond the intended color update.
- `register/driver/page.tsx` and `venues/page.tsx` changes are each local to their own file.

## User experience effect
- `company-portal/[id]/policy/page.tsx` is corporate-customer-facing — the Configured/Not-set badge
  and save-confirmation text now render with the theme-consistent success/muted tokens instead of
  fixed emerald/gray, matching the rest of the portal's dark-mode support.
- `register/driver/page.tsx` is the public driver-signup flow — the success screen's checkmark now
  uses the same `--success` token as the rest of the app.
- All other changes are internal-admin-only and purely cosmetic (badge/icon/border color), not
  visible mid-session to anyone already using the affected screen differently than before.

## Files modified
| File | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/corporate-accounts/[id]/policy/page.tsx` | Configured/Not-set badges + save-confirmation text → success/warning tokens | #2816 |
| `admin-dashboard/src/app/company-portal/[id]/policy/page.tsx` | Same badge/text conversion (corporate-customer-facing) | #2816 |
| `admin-dashboard/src/components/driver-map.tsx` | Online legend dot → `bg-success` | #2816 |
| `admin-dashboard/src/app/register/driver/page.tsx` | Success-confirmation icon/wrapper → success token | #2816 |
| `admin-dashboard/src/app/dashboard/venues/page.tsx` | Inactive-warning border + 2 delete-button patterns → warning/destructive tokens | #2816 |

## Before/after snippet
```tsx
// corporate-accounts/[id]/policy/page.tsx — before
<Badge variant="secondary" className="bg-emerald-100 text-emerald-800">Configured</Badge>
<Badge variant="secondary" className="bg-yellow-100 text-yellow-800">Not set</Badge>
// after
<Badge variant="secondary" className="bg-success/15 text-success">Configured</Badge>
<Badge variant="secondary" className="bg-warning/15 text-warning">Not set</Badge>
```
```tsx
// venues/page.tsx — before
<button ... className="text-red-600 hover:bg-red-50 dark:hover:bg-red-900/20 rounded-lg p-2">
// after
<button ... className="text-destructive hover:bg-destructive/10 rounded-lg p-2">
```

## Rollback plan
Pure CSS class revert — `git revert` this commit restores the prior literal Tailwind classes with
no data migration, feature flag, or config involved.

## Verification performed
- `npx eslint` on all 5 changed files: 0 errors, 7 warnings (all pre-existing `react-hooks/set-state-in-effect`
  advisories unrelated to this change, plus the 3 deliberately-left raw-color warnings on the
  solid-fill button and selection-highlight lines in `venues/page.tsx` and the offline dot in
  `driver-map.tsx`, matching the documented exclusions above).
- `npx tsc --noEmit`: clean.
- `npx vitest run`: 35 files / 339 tests passed.
- `npm run build`: **production build completed successfully** (not just dev server/tsc — full
  Next.js build ran to completion, including `/dashboard/venues`, `/dashboard/corporate-accounts/[id]/policy`,
  `/register/driver` routes).

## What was NOT verified
No visual-regression tooling exists in this repo (standing gap, tracked in `ACTION_ITEMS.md`) — the
color-token swaps were reasoned about against the existing `--success`/`--warning`/`--destructive`
token definitions in `globals.css`, not screenshotted. Not tested against a live Supabase-backed
corporate-portal session; only static code review of the conditional badge/text logic.
