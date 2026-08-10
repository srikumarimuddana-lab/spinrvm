# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-10 |
| Author | Claude (agent) |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin, corporate |
| PR / commit link | ca0630c |
| Related issue or gap ID | #2816 |

## 1. Issue / gap identified

Three files with the same class of bug found across this remediation
effort: a dialog error paragraph in `documents/requirements/page.tsx`
using raw `text-red-500` with no `dark:` variant (a real contrast bug,
same shape as the `subscriptions/page.tsx` fix in #3534), and two
near-identical "policy editor" pages —
`dashboard/corporate-accounts/[id]/policy/page.tsx` (internal admin view)
and `company-portal/[id]/policy/page.tsx` (corporate self-service view) —
both rendering their success/error footer text with bare
`text-emerald-600`/`text-emerald-700`/`text-red-600` and no `dark:`
pairing or semantic token.

## 2. Root cause

`documents/requirements/page.tsx`: same pre-existing `text-red-500`
(#ef4444) contrast failure documented in `globals.css`'s comment on
`--destructive` (3.76:1 in dark mode, below WCAG AA's 4.5:1) — this spot
was never migrated to the token when `--destructive` was introduced.

The two policy pages: these are parallel implementations of the same
"save policy" footer (admin-side and corporate-self-service-side), each
independently missing the `dark:` pairing that the rest of the app's
emerald-accent usages already carry (e.g.
`corporate-accounts/[id]/members/page.tsx:326` uses
`text-emerald-700 dark:text-emerald-300` for the identical "success"
pattern). Being two separate files that were never explicitly linked in
review, the gap existed in both without either serving as a fixed
reference for the other.

## 3. Fix / remediation

- `documents/requirements/page.tsx`: `text-red-500` → `text-destructive`
  (real contrast fix, same as #3534's `subscriptions/page.tsx` change).
- `corporate-accounts/[id]/policy/page.tsx` and `company-portal/[id]/policy/page.tsx`:
  `text-red-600` → `text-destructive` (no-op color swap, consistency only);
  `text-emerald-600`/`text-emerald-700` → `text-emerald-700 dark:text-emerald-300`
  (matches the established pairing used in `corporate-accounts/[id]/members/page.tsx`
  and elsewhere).

## 4. Risk & impact on existing functionality

- `className`-only change across 3 files, 5 lines; no logic, state, or
  markup structure touched. Confirmed via per-hunk review.
- Blast radius: isolated. Each spot is a standalone success/error display
  block; `corporate-accounts/[id]/policy/page.tsx` and
  `company-portal/[id]/policy/page.tsx` do not import from or share a
  component with each other despite the UI similarity — they were
  independently authored views of the same underlying policy resource, so
  fixing one has no effect on the other's runtime behavior.
- `text-destructive` and the `emerald-700/emerald-300` pairing are both
  patterns already read/used by dozens of other files in this app; no new
  consumer risk introduced.

## 5. User-experience effect

- `documents/requirements/page.tsx`: internal-admin-facing only (driver
  document-requirement config dialog). Error text now meets WCAG AA
  contrast in dark mode (3.76:1 → 4.83:1); no change in light mode.
- `corporate-accounts/[id]/policy/page.tsx`: internal-admin-facing
  (corporate ride policy editor, admin side). The `text-red-600` swap is a
  visual no-op; the emerald "✓ Policy saved" success message now dims
  correctly in dark mode instead of staying full-saturation.
- `company-portal/[id]/policy/page.tsx`: **corporate-admin-facing**
  (company self-service portal, not internal admin) — same visual effect:
  no-op red swap, emerald feedback text now dims correctly in dark mode.
  This is the one file in this batch outside the internal `/dashboard`
  admin surface; confirmed it inherits the same root `ThemeProvider`
  (`admin-dashboard/src/app/layout.tsx`, `defaultTheme="dark"`) as every
  other page in this app — it is not a standalone/fixed-light surface like
  `/track/[rideId]`.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/documents/requirements/page.tsx` | `text-red-500` → `text-destructive` (line 163) | Fix documented dark-mode contrast failure |
| `admin-dashboard/src/app/dashboard/corporate-accounts/[id]/policy/page.tsx` | `text-emerald-600` → `text-emerald-700 dark:text-emerald-300` (line 349); `text-red-600` → `text-destructive` (line 354) | Dark-mode pairing + token consistency |
| `admin-dashboard/src/app/company-portal/[id]/policy/page.tsx` | `text-emerald-700` → `text-emerald-700 dark:text-emerald-300` (line 278); `text-red-600` → `text-destructive` (line 280) | Dark-mode pairing + token consistency |

## 7. Before / after

```tsx
// Before
{error && <p className="text-sm text-red-500">{error}</p>}

<span className="text-sm text-emerald-600 font-medium">✓ Policy saved</span>
<span className="text-sm text-red-600">{error}</span>

<span className="text-xs text-emerald-700">{feedback}</span>
<span className="text-xs text-red-600">{error}</span>

// After
{error && <p className="text-sm text-destructive">{error}</p>}

<span className="text-sm text-emerald-700 dark:text-emerald-300 font-medium">✓ Policy saved</span>
<span className="text-sm text-destructive">{error}</span>

<span className="text-xs text-emerald-700 dark:text-emerald-300">{feedback}</span>
<span className="text-xs text-destructive">{error}</span>
```

## 8. Rollback plan

`git revert` is sufficient — pure styling diff, no data/state touched, no
migration, no flag.

## 9. Verification performed

- [x] `npx tsc --noEmit` — no new errors in the 3 touched files
- [x] `npx eslint` on the 3 touched files — 0 new warnings (pre-existing
      warnings only, unrelated to this change)
- [x] `npm run build` — real production build, completed clean; all three
      routes (`/dashboard/documents/requirements`,
      `/dashboard/corporate-accounts/[id]/policy`,
      `/company-portal/[id]/policy`) compiled
- [ ] Manual repro in staging — not performed (no staging access this
      session)
- [x] Blast-radius grep performed: confirmed the two policy pages share no
      component despite UI similarity; confirmed `company-portal` inherits
      the app-wide `ThemeProvider` (not a standalone-light surface)

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`)
- [x] Blast radius is stated: isolated, 3 files, 5 lines, no shared
      component between the two policy pages
- [x] No silent behavior change — one real contrast fix (documented
      before/after ratios above), rest are visually-identical token swaps
      or dark-mode-only color changes, both called out explicitly

## What was NOT verified

Not screenshotted in either theme — no staging/authenticated admin or
corporate-portal session available from this session. The
`text-destructive` no-op claims rest on comparing literal hex values, not
a pixel diff. The `emerald-700 dark:emerald-300` pairing was reasoned by
matching an already-shipped identical pairing elsewhere in the app, not
independently contrast-checked against these two pages' actual rendered
background at runtime. No visual regression tooling exists in this repo
for admin-dashboard or the company-portal surface (standing gap, see
`ACTION_ITEMS.md`).
