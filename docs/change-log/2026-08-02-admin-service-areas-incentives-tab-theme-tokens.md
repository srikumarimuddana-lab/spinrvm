# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-02 |
| Author | Claude Code |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | (this PR) |
| Related issue or gap ID | #2785 (admin-dashboard visual-refresh epic), Phase 5; #2816 (91-file hardcoded-color backlog) |

## 1. Issue / gap identified

Continuing `/dashboard/service-areas` remediation (batch 1 — page shell, PR #3119; batches 2-4 — General/Pricing/Documents tabs, PR #3129; batch 5 — Fees tab, PR #3135; all merged; batches 6-7 — Spinr Pass + Cascade tabs, PR #3138, open). This batch covers `IncentivesTab`: the last tab-content component in the file — driver ride-incentive bonuses (per-ride/peak-hours/time-limited/min-distance/area-boost bonuses shown to drivers on the ride-offer screen). Same bug class where it applies: hardcoded neutral `text-gray-*`/`bg-gray-*` with zero dark-theme awareness, alongside a large deliberately-amber-themed "New Incentive" form that is a genuine design choice, not a bug.

## 2. Scope of this PR — batch 8 of N (final batch for this file)

- **This PR**: `IncentivesTab` — heading, the neutral "Cancel" button inside the incentive form, loading/empty states, and the incentive list cards (background/icon/text for the inactive state, name, description, budget line, toggle, delete).
- **This completes `/dashboard/service-areas`'s remediation.** All 8 component-boundary batches (page shell, General, Pricing, Documents, Fees, Spinr Pass, Cascade, Incentives) are now done across PRs #3119, #3129, #3135, and #3138 (this PR).

## 3. Fix / remediation

- `text-gray-800` (heading) → `text-foreground`; `text-gray-500`/`text-gray-400` (subtitle, loading text, empty-state text, vehicle-type/description line, budget line) → `text-muted-foreground`
- The "Cancel" button inside the New Incentive form (`text-gray-500 hover:bg-gray-100`, a neutral element, not part of the form's amber theme) → `text-muted-foreground hover:bg-muted`
- Incentive-card inactive state: `bg-gray-50 border-gray-200 opacity-60` → `bg-muted border-border opacity-60`; active state `bg-white` → `bg-card` (the `border-amber-200` accent on the active state is a deliberate brand marker and stays)
- Icon-circle inactive state: `bg-gray-200`/`text-gray-400` → `bg-muted`/`text-muted-foreground` (active `bg-amber-100`/`text-amber-600` untouched — deliberate)
- Incentive name: `text-gray-900` → `text-foreground`
- Toggle button inactive state: `text-gray-400 hover:bg-gray-100` → `text-muted-foreground hover:bg-muted` (active `text-green-600 hover:bg-green-50` untouched — deliberate "on" indicator)
- Delete button (`text-red-400 hover:bg-red-50 hover:text-red-600`, an intensify-on-hover destructive pattern) → `text-destructive/70 hover:bg-destructive/10 hover:text-destructive`

**No incentive logic touched** — `load`/`handleCreate`/`handleToggle`/`handleDelete` and all `getIncentives`/`createIncentive`/`toggleIncentive`/`deleteIncentive` API calls, plus form state, are unchanged. Verified via `git diff | grep -viE "className"` returning empty.

**Deliberately left untouched — the entire "New Incentive" form (lines ~1991-2051, minus the one Cancel button fixed above)**: this is a self-consistent, deliberately-amber-themed panel (button, panel background/border, all field labels, all input borders and `bg-white` fills) matching the Gift icon's amber branding for this specific feature. This is the same false-positive category as batch 1's "Add Airport Zone" panel — a colored panel with its own `bg-white` inputs, internally consistent regardless of the surrounding page's theme. Also left untouched: the bonus-amount and incentive-type badges (`bg-amber-100 text-amber-700`, `bg-gray-100 text-gray-600`) — self-contained categorical pills, same false-positive category documented since batch 1/#2816.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to this file.** `IncentivesTab` is defined and used only within `service-areas/page.tsx` — grepped, no other importer.
- **No logic touched, verified mechanically**: `git diff <file> | grep -E "^[+-]" | grep -vE "^\+\+\+|^---" | grep -viE "className"` returns empty for this batch's changes — every changed line is a `className` string.
- No prop, state, or incentive CRUD/toggle/delete behavior change.

## 5. User-experience effect

- Internal-admin facing only (`/dashboard/service-areas` → Incentives tab). Heading, loading/empty states, and inactive-state incentive cards now render with theme-aware colors. The deliberately-amber "New Incentive" creation form is visually unchanged (by design). No change to what's creatable, toggleable, or deletable.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/service-areas/page.tsx` | Ported `IncentivesTab`'s neutral (non-amber-themed) elements from hardcoded Tailwind colors to semantic theme tokens | Batch 8 (final) of the `/dashboard/service-areas` #2816 remediation |

## 7. Before / after

```
# Before
<div key={inc.id} className={`flex items-center gap-4 p-4 rounded-xl border ${inc.is_active ? 'bg-white border-amber-200' : 'bg-gray-50 border-gray-200 opacity-60'}`}>
```

```
# After
<div key={inc.id} className={`flex items-center gap-4 p-4 rounded-xl border ${inc.is_active ? 'bg-card border-amber-200' : 'bg-muted border-border opacity-60'}`}>
```
(`border-amber-200` on the active state is unchanged — a deliberate brand accent, not a bug.)

## 8. Rollback plan

- `git revert` is fully safe — pure `className` changes, no data/config/logic touched.

## 9. Verification performed

- [x] `npm run build` — clean, all routes compile.
- [x] `npm run lint` — 0 new warnings in the edited range (lines 1934-2101); the one pre-existing warning at line 1954 (`areaId`-triggered `load()` `useEffect`) is on logic this batch didn't touch.
- [x] `git diff <file> | grep -viE "className"` — empty, confirming the diff is styling-only.
- [x] `git diff --stat` — 26 lines changed (13 insertions / 13 deletions), well under the ~200-line-per-commit guideline.
- [x] `grep`-verified the only remaining hardcoded occurrences in the component's line range are the deliberately-amber-themed form's `bg-white` inputs and the categorical type badge — both known, documented false positives.

## What was NOT verified

- Not live-axe-verified in a browser — reuses token pairings already live-verified in `/dashboard/staff` (PR #2847) and applied identically in every prior batch of this file.
- This is the final batch for `/dashboard/service-areas` — no further tab-content components remain unaddressed in this file. #2816's broader backlog (other files) is tracked separately in the issue.
