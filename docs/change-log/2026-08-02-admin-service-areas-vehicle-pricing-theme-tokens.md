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

Continuing `/dashboard/service-areas` remediation (batch 1 — page shell — PR #3119, merged; batch 2 — General tab — PR #3129). This batch covers `VehiclePricingEditor`: the per-vehicle-type fare table (base fare, per-km, per-min, min fare, booking fee) on the Pricing tab. Same bug class: hardcoded `text-gray-500`, `bg-white`, `bg-gray-100`, `bg-red-500`/`text-red-500` with zero dark-theme awareness.

## 2. Scope of this PR — batch 3 of N

- **This PR**: `VehiclePricingEditor` only (table header, vehicle-type `<select>`, delete-row icon, "+ Add vehicle type" link, "Save Pricing" button).
- **Not yet done**: `DocumentsEditor`, `AreaFeesEditor`+`FeeEditForm`, `SpinrPassAreaTab`, `IncentivesTab`, `CascadeEditor` — later tab-content components in the same file, planned as further follow-up batches.

## 3. Fix / remediation

- `text-gray-500` (table header row) → `text-muted-foreground`
- `bg-white` (vehicle-type `<select>`) → `bg-card`
- `text-gray-400 hover:text-red-500` (delete-row trash icon) → `text-muted-foreground hover:text-destructive` — this is a destructive-action icon (removes a pricing row), so the hover state maps to the `destructive` semantic token rather than a bare red, consistent with how the rest of the app expresses "this hovers into a delete/dangerous state."
- `text-red-500 font-semibold hover:underline` ("+ Add vehicle type" link, a primary add-action) → `text-primary font-semibold hover:underline`
- `bg-red-500 text-white hover:bg-red-600` / `bg-gray-100 text-gray-400` (dirty/clean "Save Pricing" button, same dirty-state pattern already fixed in batch 2's General-tab Save button) → `bg-primary text-primary-foreground hover:bg-primary/90` / `bg-muted text-muted-foreground`

**No pricing calculation logic touched** — `update()`, `addRow()`, `removeRow()`, `takenNames()`, and the `parseFloat`/dirty-tracking state are all unchanged. Verified via `git diff | grep -viE "className"` returning empty (no `Toggle`-style icon prop changes in this batch, so the check is simpler than batch 2's).

**Left untouched**: the `border` class on the `<select>`/`<input>` elements (generic Tailwind default border token, not a hardcoded `border-gray-*`) and the `text-amber-600` "No vehicle types defined yet" hint (a deliberate colored warning indicator, not a neutral-gray bug).

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to this file.** `VehiclePricingEditor` is defined and used only within `service-areas/page.tsx` — grepped, no other importer.
- **No logic touched, verified mechanically**: `git diff <file> | grep -E "^[+-]" | grep -vE "^\+\+\+|^---" | grep -viE "className"` returns empty for this batch's changes — every changed line is a `className` string.
- No prop, state, or fare-calculation change; row add/remove/edit/save behavior is unchanged.

## 5. User-experience effect

- Internal-admin facing only (`/dashboard/service-areas` → Pricing tab). Table header, vehicle-type dropdown background, delete icon, add-row link, and Save button now render with theme-aware colors. No change to what pricing fields exist, validation, or save behavior.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/service-areas/page.tsx` | Ported `VehiclePricingEditor` from hardcoded Tailwind colors to semantic theme tokens | Batch 3 of the `/dashboard/service-areas` #2816 remediation |

## 7. Before / after

```
# Before
<tr className="text-left text-xs text-gray-500 border-b">
...
<button onClick={() => removeRow(i)} className="text-gray-400 hover:text-red-500"><Trash2 className="h-4 w-4" /></button>
```

```
# After
<tr className="text-left text-xs text-muted-foreground border-b">
...
<button onClick={() => removeRow(i)} className="text-muted-foreground hover:text-destructive"><Trash2 className="h-4 w-4" /></button>
```

## 8. Rollback plan

- `git revert` is fully safe — pure `className` changes, no data/config/logic touched.

## 9. Verification performed

- [x] `npm run build` — clean, all routes compile.
- [x] `npm run lint` — 0 new warnings in the edited range (lines 926-1034). The one pre-existing warning in this component (`setState`-in-effect on the `pricing`-sync `useEffect`, line 935) is on logic this batch didn't touch.
- [x] `git diff <file> | grep -viE "className"` — empty, confirming the diff is styling-only.
- [x] `git diff --stat` — 10 lines changed (5 insertions / 5 deletions), well under the ~200-line-per-commit guideline.
- [x] `grep`-verified no remaining `text-gray-*`/`bg-gray-*`/`bg-white`/`border-gray-*`/`bg-red-*`/`text-red-*` occurrences in the component's line range after edits.

## What was NOT verified

- Not live-axe-verified in a browser — reuses token pairings already live-verified in `/dashboard/staff` (PR #2847) and applied identically in batches 1-2 (muted table header, card-background select, primary CTA button, destructive-hover icon).
- The remaining tab-content components (`DocumentsEditor`, `AreaFeesEditor`+`FeeEditForm`, `SpinrPassAreaTab`, `IncentivesTab`, `CascadeEditor`) are explicitly out of scope for this PR — not silently dropped, see section 2.
