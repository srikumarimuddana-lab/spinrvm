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

Continuing `/dashboard/service-areas` remediation (batch 1 — page shell, PR #3119, merged; batch 2 — General tab, batch 3 — Vehicle Pricing, both PR #3129). This batch covers `DocumentsEditor`: the per-area required-documents card grid on the Documents tab. Same bug class: hardcoded `bg-white` cards, `text-gray-400/500/800` labels, and a brand-red (`red-*`) "editing" highlight/focus-ring treatment with zero dark-theme awareness.

## 2. Scope of this PR — batch 4 of N

- **This PR**: `DocumentsEditor` only (header, empty state, document card grid — header/icon/labels/reorder buttons, editing-mode expanded form, Add/Save action bar).
- **Not yet done**: `AreaFeesEditor`+`FeeEditForm`, `SpinrPassAreaTab`, `IncentivesTab`, `CascadeEditor` — later tab-content components in the same file, planned as further follow-up batches.

## 3. Fix / remediation

Same substitution rules as batches 1-3, plus one new pattern specific to this component — a "currently editing" card highlight that used brand-red (`ring-red-200`/`border-red-300`/`bg-red-50`) as a selected-state indicator:

- `text-gray-800` (heading, card label) → `text-foreground`; `text-gray-500`/`text-gray-400`/`text-gray-300` (subtitles, counts, key text, disabled reorder icons) → `text-muted-foreground`
- Empty-state panel: `bg-gray-50`/`border-gray-200` → `bg-muted`/`border-border`
- Card wrapper: `bg-white`/`border-gray-200` (idle) → `bg-card`/`border-border`; `ring-red-200`/`border-red-300` (editing) → `ring-primary/30`/`border-primary/50` — mapped to the `primary` token rather than dropped, matching the existing "selected state → primary" idiom already used elsewhere in this codebase (`staff/page.tsx:326`, `vehicle-types/page.tsx:478`, `drivers/page.tsx:767`) for exactly this "currently active/being-edited" pattern
- Card header background: `bg-gray-50`(idle)/`bg-red-50`(editing) → `bg-muted`/`bg-primary/10`
- Optional-icon-circle background/icon color (non-required state only — required state's `bg-emerald-100`/`text-emerald-600` untouched): `bg-gray-100`/`text-gray-400` → `bg-muted`/`text-muted-foreground`
- Expanded edit-panel background: `bg-white` → `bg-card`; field labels `text-gray-500` → `text-muted-foreground`; input focus ring `focus:ring-red-200 focus:border-red-300` → `focus:ring-primary/20 focus:border-primary` (matches the existing focus-ring idiom already used in `drivers/_components/driver-notes.tsx` and `rides/_components/ride-list.tsx`)
- Checkbox accents: `accent-red-500` → `accent-primary` (3 checkboxes: has_expiry, required, requires_back_side)
- "Remove" text button (persistently red, a genuine destructive action): `text-red-500 hover:text-red-700` → `text-destructive hover:text-destructive/80`
- "Done" button: `bg-gray-100 text-gray-700 hover:bg-gray-200` → `bg-muted text-foreground hover:bg-muted/70` (reuses the `bg-muted text-foreground` Cancel-button pairing already established in batch 1, line 289 of this file)
- Idle-state "Edit" link (non-destructive action; hover uses brand-red as an accent, not a warning): `text-gray-400 hover:text-red-500` → `text-muted-foreground hover:text-primary`
- Idle-state "Remove" icon button (destructive, same semantics as the "Remove" text button above): `text-gray-300 hover:text-red-500` → `text-muted-foreground hover:text-destructive`
- "+ Add document type" CTA link (same "add" pattern already fixed for "+ Add vehicle type" in batch 3): `text-red-500 hover:text-red-700` → `text-primary hover:text-primary/80`
- "Save Documents" button (same dirty/clean Save-button pattern as batches 2-3): `bg-red-500 text-white hover:bg-red-600` / `bg-gray-100 text-gray-400` → `bg-primary text-primary-foreground hover:bg-primary/90` / `bg-muted text-muted-foreground`

**No document-list logic touched** — `update()`, `addDoc()`, `removeDoc()`, `moveDoc()`, `requiredCount`/`expiryCount`, and the reorder/dirty-tracking state are all unchanged. Verified via `git diff | grep -viE "className"` returning empty.

**Deliberately left untouched**: the "Optional" (`bg-gray-100 text-gray-500`) and "No Expiry" (`bg-gray-100 text-gray-400`) status pills — self-contained categorical badges (light-bg + dark-ish-text pairs, internally consistent regardless of page theme), same false-positive category as the "Required"/"Has Expiry"/"Both Sides" pills beside them (which already use colored `emerald-100`/`amber-100`/`blue-100` and were never hardcoded-neutral to begin with) and as documented in #2816/prior batches.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to this file.** `DocumentsEditor` is defined and used only within `service-areas/page.tsx` — grepped, no other importer.
- **No logic touched, verified mechanically**: `git diff <file> | grep -E "^[+-]" | grep -vE "^\+\+\+|^---" | grep -viE "className"` returns empty for this batch's changes — every changed line is a `className` string.
- No prop, state, or document-list behavior change; add/remove/reorder/save flow is unchanged.

## 5. User-experience effect

- Internal-admin facing only (`/dashboard/service-areas` → Documents tab). Card backgrounds, labels, the "currently editing" highlight, focus rings, and action buttons now render with theme-aware colors. The "editing" highlight changes from a brand-red tint to a primary-brand tint — visually similar (same brand hue in this app, since `--color-primary` is the app's red), but now theme-aware instead of a hardcoded light-mode-only red-50/red-200/red-300. No change to what fields exist, validation, or save behavior.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/service-areas/page.tsx` | Ported `DocumentsEditor` from hardcoded Tailwind colors to semantic theme tokens | Batch 4 of the `/dashboard/service-areas` #2816 remediation |

## 7. Before / after

```
# Before
<div key={i} className={`rounded-xl border overflow-hidden transition-all ${isEditing ? 'ring-2 ring-red-200 border-red-300 shadow-md' : 'bg-white hover:shadow-sm border-gray-200'}`}>
  <div className={`px-4 py-3 flex items-center gap-3 ${isEditing ? 'bg-red-50' : 'bg-gray-50'}`}>
```

```
# After
<div key={i} className={`rounded-xl border overflow-hidden transition-all ${isEditing ? 'ring-2 ring-primary/30 border-primary/50 shadow-md' : 'bg-card hover:shadow-sm border-border'}`}>
  <div className={`px-4 py-3 flex items-center gap-3 ${isEditing ? 'bg-primary/10' : 'bg-muted'}`}>
```

## 8. Rollback plan

- `git revert` is fully safe — pure `className` changes, no data/config/logic touched.

## 9. Verification performed

- [x] `npm run build` — clean, all routes compile.
- [x] `npm run lint` — 0 warnings anywhere in the edited range (lines 1038-1206), before or after.
- [x] `git diff <file> | grep -viE "className"` — empty, confirming the diff is styling-only.
- [x] `git diff --stat` — 60 lines changed (30 insertions / 30 deletions), within the ~200-line-per-commit guideline.
- [x] `grep`-verified only the two deliberately-deferred categorical badges (`Optional`, `No Expiry`) remain hardcoded in the component's line range after edits.
- [x] Cross-checked the new `ring-primary/30 border-primary/50` / `focus:ring-primary/20 focus:border-primary` idioms against existing usages elsewhere in admin-dashboard (`staff/page.tsx`, `vehicle-types/page.tsx`, `drivers/page.tsx`, `driver-notes.tsx`, `ride-list.tsx`) before applying, rather than inventing a new pattern — these are established "selected/focused state" tokens in this codebase.

## What was NOT verified

- Not live-axe-verified in a browser — reuses token pairings already live-verified in `/dashboard/staff` (PR #2847) and, for the new `ring-primary`/`focus:ring-primary` pairings, pairings already in production use elsewhere in admin-dashboard (see verification list above), not novel combinations.
- The remaining tab-content components (`AreaFeesEditor`+`FeeEditForm`, `SpinrPassAreaTab`, `IncentivesTab`, `CascadeEditor`) are explicitly out of scope for this PR — not silently dropped, see section 2.
