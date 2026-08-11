# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-11 |
| Author | Claude (agent) |
| Surface(s) | admin-dashboard (`/company-portal` — corporate self-service surface) |
| Domain (Sentry tag) | corporate, admin |
| PR / commit link | f9b5ad9, 2e471a3, 8721e80, 37acef1, 607b314, 25ce8b8 (batch F addendum) |
| Related issue or gap ID | #2816 (adjacent style-consistency follow-on, not itself a filed bug) |

## 1. Issue / gap identified

**This is explicitly a style-consistency pass, not an accessibility bug
fix.** Across 13 files in `/company-portal` (the corporate self-service
portal — distinct from the internal `/dashboard` admin surface), error
and success feedback messages used hardcoded pastel boxes
(`bg-red-50 text-red-700`, `bg-emerald-50 text-emerald-800`,
`bg-amber-50 text-amber-800`) with no `dark:` variant, plus two small
"Building2" logo-icon badges (`bg-emerald-50` wrapper) in the portal
header and company-picker list.

None of these are WCAG contrast failures — a light pastel background
paired with dark text is internally well-contrasted regardless of the
surrounding page's theme, the same "self-contained, fine as-is" reasoning
#2816's own triage established for badges/pills. The issue is purely
visual: these boxes render as bright, out-of-place rectangles against
`company-portal`'s dark theme (confirmed in an earlier session that this
surface inherits the app-wide `ThemeProvider`, `defaultTheme="dark"` —
it is not a standalone/fixed-light page like `/track/[rideId]`).

## 2. Root cause

`/company-portal` was built independently of `/dashboard`'s theme-token
migration (the #2816 remediation effort spanning #2847, #3119-3138,
#3378, #3534, #3538, #3540) and never received an equivalent pass. Every
page in the portal repeats the same hardcoded error/success-box idiom,
suggesting it was copy-pasted from a shared starting template before
dark-mode theming was introduced app-wide.

## 3. Fix / remediation

Two token strategies, matching established conventions already shipped
elsewhere in `admin-dashboard`:

- **Error boxes** → `bg-destructive/10 text-destructive` (+ `border-destructive/30`
  where a border was already present), matching the full-box convention
  already used in `dashboard/faqs/page.tsx:202` and
  `dashboard/settings/page.tsx:1199`.
- **Success/warning boxes and icon badges** → `bg-emerald-50 dark:bg-emerald-900/20`
  / `bg-amber-50 dark:bg-amber-900/10`, `text-emerald-800 dark:text-emerald-300`
  / `text-amber-800 dark:text-amber-300`, matching the full-box amber
  convention at `dashboard/page.tsx:95,132` and the emerald icon-badge
  convention at `dashboard/drivers/_components/driver-timeline.tsx`.

Five batches (≤3 files each, matching this repo's commit-size
convention):
- Batch A: `overview`, `activity`, `book` (3 files, 3 error-box spots)
- Batch B: `sections`, `billing`, `bookings` (3 files, 3 error-box spots)
- Batch C: `allowance-requests`, `members`, `verification` (3 files, 5 spots)
- Batch D: `settings`, `allowances`, `layout` (3 files, 6 spots, incl. header logo badge)
- Batch E: `company-portal/page.tsx` root (1 file, 2 spots, incl. list-row logo badge)

**Deliberately left unchanged**: `bookings/page.tsx:33`'s
`completed: "bg-emerald-50 text-emerald-700"` status-badge map entry —
a small self-contained pill alongside sibling `bg-violet-100`/`bg-red-100`
badges in the same map, the same "fine as-is" category as every badge
left alone throughout the #2816 effort.

## 4. Risk & impact on existing functionality

- `className`-only changes across 13 files, 22 spots; no logic, state,
  validation, or markup structure touched in any batch. Confirmed via
  per-batch `git diff` review.
- Blast radius: **isolated to `/company-portal`**, a self-contained route
  tree under `admin-dashboard/src/app/company-portal/`. No file here is
  imported by `/dashboard` (the internal admin surface) or vice versa —
  grepped for cross-imports between the two trees, found none relevant to
  the touched files.
- `text-destructive`/`bg-destructive/10` and the emerald/amber `dark:`
  pairings are patterns already used dozens of times elsewhere in
  `admin-dashboard`; no new consumer risk, pure alignment with existing
  tokens.
- Every touched spot is a standalone error/feedback/icon element; none
  share a component across the 13 files (each portal page independently
  renders its own error/feedback JSX inline).

## 5. User-experience effect

- **Corporate-admin-facing** (company self-service portal — company
  owners/billing/ops/section-managers, per this repo's corporate role
  taxonomy). This is the one surface in this whole remediation effort
  that isn't internal-`/dashboard`-only.
- Visual-only change: error/success/warning boxes and the two logo-icon
  badges now render as dark-theme-appropriate boxes (dark red/emerald/
  amber tints) instead of bright light-mode pastels, when the portal is
  viewed in dark mode (the app's default theme). No change in light mode.
  No copy, validation, or interaction changed — same trigger conditions
  (`error`/`feedback`/`notice` state) as before.
- Not a mid-session behavior change to an already-open form — purely a
  color shift on next render of already-existing conditional UI.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `company-portal/[id]/overview/page.tsx` | error box → destructive token | Style consistency |
| `company-portal/[id]/activity/page.tsx` | error box → destructive token | Style consistency |
| `company-portal/[id]/book/page.tsx` | error box → destructive token | Style consistency |
| `company-portal/[id]/sections/page.tsx` | error box → destructive token | Style consistency |
| `company-portal/[id]/billing/page.tsx` | error box → destructive token | Style consistency |
| `company-portal/[id]/bookings/page.tsx` | error box → destructive token (badge map entry left as-is) | Style consistency |
| `company-portal/[id]/allowance-requests/page.tsx` | error box → destructive token | Style consistency |
| `company-portal/[id]/members/page.tsx` | error box → destructive token; ok/warning feedback box → emerald/amber `dark:` pairing | Style consistency |
| `company-portal/[id]/verification/page.tsx` | 2 error boxes → destructive token; notice box → emerald `dark:` pairing | Style consistency |
| `company-portal/[id]/settings/page.tsx` | error box → destructive token; feedback box → emerald `dark:` pairing | Style consistency |
| `company-portal/[id]/allowances/page.tsx` | error box → destructive token; feedback box → emerald `dark:` pairing | Style consistency |
| `company-portal/[id]/layout.tsx` | error box → destructive token; header logo icon badge → emerald `dark:` pairing | Style consistency |
| `company-portal/page.tsx` | error box → destructive token; list-row logo icon badge → emerald `dark:` pairing | Style consistency |

## 7. Before / after

```tsx
// Before
<p className="rounded bg-red-50 p-3 text-sm text-red-700">{error}</p>
<p className="rounded bg-emerald-50 p-2 text-xs text-emerald-800">{feedback}</p>
<div className="rounded-md bg-emerald-50 p-2"><Building2 className="h-5 w-5 text-emerald-600" /></div>

// After
<p className="rounded bg-destructive/10 p-3 text-sm text-destructive">{error}</p>
<p className="rounded bg-emerald-50 dark:bg-emerald-900/20 p-2 text-xs text-emerald-800 dark:text-emerald-300">{feedback}</p>
<div className="rounded-md bg-emerald-50 dark:bg-emerald-900/20 p-2"><Building2 className="h-5 w-5 text-emerald-600 dark:text-emerald-400" /></div>
```

## 8. Rollback plan

`git revert` on any/all of the 5 batch commits is sufficient — pure
styling diffs, no data/state touched, no migration, no flag. Each batch
commits independently, so a partial revert (e.g. only batch D) is
possible without affecting the others.

## 9. Verification performed

- [x] `npx tsc --noEmit` — no new errors in any of the 13 touched files,
      checked after each batch
- [x] `npx eslint` on all touched files — 0 new errors after every batch
      (pre-existing warnings only, unrelated to this change)
- [x] `npm run build` — real production build, completed clean after the
      final batch, covering all 13 routes
- [ ] Manual repro in staging — not performed (no staging access this
      session); reasoned from established token/pairing precedent already
      shipped and presumably visually verified elsewhere in this app
- [x] Blast-radius grep performed: confirmed `/company-portal` and
      `/dashboard` share no components relevant to the touched spots;
      confirmed the one deliberately-untouched badge (`bookings.tsx:33`)
      is genuinely self-contained, matching sibling badges in the same map

## 10. Sign-off

- [x] Rollback plan is concrete and testable (5 independent `git revert`s)
- [x] Blast radius is stated: isolated to `/company-portal`, 13 files, 22 spots
- [x] No silent behavior change — explicitly framed as visual-only from
      the start (Section 1); UX effect field states the exact trigger
      conditions are unchanged

## 11. Addendum — batch F (`company-login`, `company-signup`)

Found in a follow-up sweep: `admin-dashboard/src/app/company-login/page.tsx`
and `admin-dashboard/src/app/company-signup/page.tsx` — the public
sibling entry pages to `/company-portal` (same product surface, same
Building2-icon-in-a-badge header + `bg-red-50` error box) — were outside
the original 13-file batch because they live at the top-level `app/`
route rather than under `app/company-portal/`. Confirmed
theme-participating via existing `bg-background` usage in both files
(not a standalone/fixed-light page). Same fix, same rationale, same
tokens as the rest of this pass (`bg-destructive/10 text-destructive` for
the error box, `bg-emerald-50 dark:bg-emerald-900/20` /
`text-emerald-600 dark:text-emerald-400` for the logo badge) — 2 files,
4 spots, commit `25ce8b8`.

Verification: `npx tsc --noEmit` (0 new errors), `npx eslint` (0 new
warnings — cleanest result of this entire pass, no pre-existing warnings
in either file), `npm run build` (clean production build). Same "not
independently visually verified, reasoned from established precedent"
caveat as the rest of this log applies.

## What was NOT verified

Not screenshotted in either theme — no staging/authenticated corporate-
portal session available from this session. Every token/pairing choice
was justified by direct comparison against an already-shipped,
presumably-reviewed precedent elsewhere in `admin-dashboard`, not by an
independent visual check of these specific 22 spots post-fix. No visual
regression tooling exists in this repo for admin-dashboard or the
company-portal surface (standing gap, see `ACTION_ITEMS.md`).
