# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-01 |
| Author | Claude Code |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | (this PR) |
| Related issue or gap ID | #2785 (admin-dashboard visual-refresh epic), Phase 5; #2816 (91-file hardcoded-color backlog) |

## 1. Issue / gap identified

`/dashboard/service-areas` is the largest confirmed file in #2816's backlog — 2,101 lines, 203 raw hardcoded-color occurrences, and the same severe bug class as the original `/dashboard/staff` fix (PR #2847): entirely hardcoded `bg-white` cards + `text-gray-700/800/900` headings/labels, zero dark-theme awareness on a page that's expanded (multi-tab settings editor) for potentially hours at a time by ops staff.

## 2. Scope of this PR — batch 1 of N

Given the file's size (far exceeding any single reviewable PR), this is the first of several planned batches, split by component/section boundary rather than attempted in one pass:

- **This PR**: the outer page shell — header, "Create Service Area" form, the area list/card wrapper (icon, name, badges row, vehicle/plan counts), and the tab-bar selector. This is the part every visit to the page renders, regardless of which tab an operator has open.
- **Not yet done**: the tab *content* components (`GeneralTabForm` — includes surge pricing UI, `VehiclePricingEditor`, `DocumentsEditor`, `AreaFeesEditor`, `SpinrPassAreaTab`, `IncentivesTab`, `CascadeEditor`) — each a large, self-contained function later in the same file, planned as separate follow-up PRs.

## 3. Fix / remediation

Same substitution rules established in PR #2847/#3119, applied only within the batch-1 range (page shell, lines ~226–568 of the file):

- `text-gray-900/800/700` → `text-foreground`; `text-gray-500/400` → `text-muted-foreground` (headings, subtitles, counts)
- `bg-white` (page/card context) → `bg-card`; `bg-gray-50/100` (tab-bar strip, empty states, map-loading fallback) → `bg-muted`
- `bg-red-500`/`hover:bg-red-600` (primary CTA buttons: New Area, Create, active-tab indicator) → `bg-primary`/`hover:bg-primary/90` + `text-primary-foreground`
- `accent-red-500` → `accent-primary`
- The one bare validation-hint red (`text-red-500` on "select a preset or draw on the map") → `text-destructive`, since it's a genuine validation/error indicator, not a brand-CTA color — a direct, low-risk semantic-token mapping.
- The active-tab selector (`bg-white text-red-500 border-t-2 border-red-500` vs `text-gray-500 hover:text-gray-700`) → `bg-card text-primary border-t-2 border-primary` vs `text-muted-foreground hover:text-foreground` — this is functionally the same "selected state" pattern as a button, not a categorical badge.

**Deliberately left untouched in this range** (matching the false-positive categories documented in #2816 and PR #3119):

- The `INACTIVE`/`AIRPORT`/`N airport zones` badges (multi-hue categorical scheme: gray/blue/violet) — self-contained light-bg+dark-text pills, internally consistent regardless of page theme, same category as `/dashboard/staff`'s `ROLE_COLORS` and other badges already surveyed.
- The entire "Add Airport Zone" sub-panel (blue-50/blue-200 scoped, including its own `bg-white` inputs, `bg-gray-100` map-loading fallback, `bg-gray-100`/`text-gray-600` Cancel button, and sub-region cards' `bg-gray-200`/`text-gray-600` INACTIVE badge) — a self-consistent colored panel, same category as overlay/badge false positives already documented.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to this one file's outer shell.** Grepped for other consumers of this page's JSX/classes — none; this is a page component, not a shared one.
- **No logic touched.** Every edit is a `className` string swap. Diff confirmed via `git diff --stat`: 33 insertions / 33 deletions, 66 lines total — no line outside a `className` attribute changed. This matters specifically here because the file also contains surge-pricing business logic (`GeneralTabForm`, not touched in this batch) — confirming the diff is styling-only rules out any risk to that logic in this PR.
- No prop, data-flow, or interaction change.

## 5. User-experience effect

- Internal-admin facing only (`/dashboard/service-areas`, an ops-configuration page). The always-visible page shell (header, New Area button, area cards, tab selector) now renders with theme-aware colors instead of hardcoded light-mode ones. Tab *content* (General/Pricing/Fees/etc.) is unchanged in this PR — still hardcoded, to be addressed in follow-up batches.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/service-areas/page.tsx` | Ported the page shell (header, create form, area-card wrapper, tab bar) from hardcoded Tailwind colors to semantic theme tokens | Batch 1 of the `/dashboard/service-areas` #2816 remediation |

## 7. Before / after

```
# Before
<h1 className="text-2xl font-bold text-gray-900">Service Areas</h1>
<button onClick={() => setShowCreate(true)} className="flex items-center gap-2 bg-red-500 text-white px-5 py-2.5 rounded-xl font-semibold hover:bg-red-600">
```

```
# After
<h1 className="text-2xl font-bold text-foreground">Service Areas</h1>
<button onClick={() => setShowCreate(true)} className="flex items-center gap-2 bg-primary text-primary-foreground px-5 py-2.5 rounded-xl font-semibold hover:bg-primary/90">
```

## 8. Rollback plan

- `git revert` is fully safe — pure `className` changes, no data/config/logic touched.

## 9. Verification performed

- [x] `npm run build` — clean, all routes compile.
- [x] `npm run lint` — 0 new warnings from the changed file (all warnings present — `toast` unused var, `load`/hooks-before-declaration, setState-in-effect — are pre-existing, on lines outside this batch's edits, e.g. line 1954's `useEffect` is inside a not-yet-touched later component).
- [x] `git diff --stat` confirms 66 total changed lines, well under the ~200-line-per-commit guideline, and confirms every change is a `className` swap (no logic line touched).
- [x] Read through the full 2,101-line file before editing anything, to plan the section-by-section decomposition and confirm which parts are genuinely broken vs. self-contained badges/panels (same methodology as PR #3119's survey).

## What was NOT verified

- Not live-axe-verified in a browser — this batch reuses token pairings already live-verified in `/dashboard/staff` (PR #2847) and `/dashboard/forecast` (PR #3119) in the same contexts (page heading on page background, card on page background, primary CTA button, muted tab-bar strip), not new pairings, so the incremental risk is low enough that a fresh live check wasn't run for this batch specifically.
- The remaining ~170 occurrences in this same file (tab-content components) are explicitly out of scope for this PR — not silently dropped, see section 2.
