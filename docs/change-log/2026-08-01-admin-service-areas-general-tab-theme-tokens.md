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

Continuing `/dashboard/service-areas` remediation (batch 1 — page shell — merged in #3119). This batch covers the "General" tab's content: `GeneralTabForm` (surge-pricing config, driver-matching toggles, service-area-boundary map) plus its shared field-helper components `FieldInput`, `FieldTextarea`, `FieldToggle`. Same bug class as batch 1: hardcoded `text-gray-500/700`, `bg-white`, `bg-gray-100`, `bg-red-500` with zero dark-theme awareness, on a settings panel operators can have open for extended periods.

## 2. Scope of this PR — batch 2 of N

- **This PR**: `GeneralTabForm` (Active toggle, Demand Heatmap, Surge Pricing block, Driver Matching / ETA Ranking, Service Area Boundary map, Save button) and its helper components `FieldInput`, `FieldTextarea`, `FieldToggle`.
- **Not yet done**: `VehiclePricingEditor`, `DocumentsEditor`, `AreaFeesEditor`+`FeeEditForm`, `SpinrPassAreaTab`, `IncentivesTab`, `CascadeEditor` — later tab-content components in the same file, planned as further follow-up batches.

## 3. Fix / remediation

Same substitution rules as batch 1, applied only within this batch's range (`GeneralTabForm` + field helpers):

- `text-gray-500` (field labels, helper text, off-state icons) → `text-muted-foreground`; `text-gray-700` (checkbox label) → `text-foreground`; `text-gray-900`/`text-gray-800` (section headings) → `text-foreground`
- `bg-gray-100`/`text-gray-400` (map-loading fallback) → `bg-muted`/`text-muted-foreground`
- `bg-red-500 text-white` (inline "Save" buttons on `FieldInput`/`FieldTextarea`, main "Save General Settings" button) → `bg-primary text-primary-foreground` (+ `hover:bg-primary/90` where present)
- `<ToggleLeft className="h-6 w-6 text-gray-300" />` (off-state icon, used 4× across Active/Demand-Heatmap/Surge/`FieldToggle`) → `text-muted-foreground`
- Global `replace_all` for `"block text-xs font-semibold text-gray-500 mb-1"` (31 identical instances file-wide — this exact field-label pattern is used uniformly across every tab, not just this batch's range, and each site pairs it with the same `bg-card`/page-background context, so it was safe to fix in one pass rather than re-doing it per batch) → `"...text-muted-foreground mb-1"`

**No surge-pricing, driver-matching, or map logic touched** — this section contains the app's surge-cap/justification business logic (`surge_enabled`, `surge_multiplier`, `needsJustification` for >2.5×), so this was verified as a hard requirement, not just a preference (see section 9).

**Deliberately left untouched in this batch**: the amber-bordered surge-justification `textarea`'s `bg-white` background — inside a self-consistent `border-amber-300 bg-amber-50` warning panel, same false-positive category (self-contained colored panel) documented in batch 1 and #2816.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to this file.** `GeneralTabForm`, `FieldInput`, `FieldTextarea`, `FieldToggle` are all defined and used only within `service-areas/page.tsx` — grepped the file, confirmed no other page imports them.
- **No logic touched, verified mechanically, not just by eye**: `git diff <file> | grep -E "^[+-]" | grep -vE "^\+\+\+|^---" | grep -viE "className|Toggle"` returns empty — every changed line is either a `className` string or the `ToggleLeft` icon's color prop. This is the same check used in batch 1, applied here specifically because this batch's component contains the surge-pricing cap/justification logic that CLAUDE.md flags as a sensitive area — confirming the diff is styling-only rules out any risk to that logic.
- No prop, state, or data-flow change; `surge_enabled`/`surge_multiplier`/`needsJustification` conditionals are unchanged.

## 5. User-experience effect

- Internal-admin facing only (`/dashboard/service-areas` → General tab). Field labels, section headings, toggle icons, map-loading fallback, and Save buttons in the General tab now render with theme-aware colors. No change to what data is shown, what's editable, or surge-pricing behavior/limits.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/service-areas/page.tsx` | Ported `GeneralTabForm` + `FieldInput`/`FieldTextarea`/`FieldToggle` from hardcoded Tailwind colors to semantic theme tokens | Batch 2 of the `/dashboard/service-areas` #2816 remediation |

## 7. Before / after

```
# Before
<button type="button" onClick={handleSave} className="px-3 py-1 bg-red-500 text-white text-xs rounded-lg font-semibold">
...
<label className="block text-xs font-semibold text-gray-500 mb-1">{label}</label>
```

```
# After
<button type="button" onClick={handleSave} className="px-3 py-1 bg-primary text-primary-foreground text-xs rounded-lg font-semibold">
...
<label className="block text-xs font-semibold text-muted-foreground mb-1">{label}</label>
```

## 8. Rollback plan

- `git revert` is fully safe — pure `className`/icon-color changes, no data, config, or surge/driver-matching logic touched.

## 9. Verification performed

- [x] `npm run build` — clean, all routes compile (including `/dashboard/service-areas`).
- [x] `npm run lint` — 0 new warnings from the changed lines. The file has pre-existing warnings (`setState`-in-effect on `FieldInput`/`FieldTextarea`'s value-sync effects, lines 875/891; similar warnings later in `VehiclePricingEditor`/other not-yet-touched components) — all on logic this batch didn't touch, confirmed by reading each flagged line.
- [x] `git diff <file> | grep -viE "className|Toggle"` — empty, confirming the diff is styling-only. Run specifically because this component contains surge-pricing cap/justification business logic.
- [x] `git diff --stat` — 104 lines changed (52 insertions / 52 deletions), within the ~200-line-per-commit guideline.
- [x] Verified the 31-instance `replace_all` target string is used identically (same field-label pattern, same `bg-card` context) at every occurrence before applying file-wide, and confirmed the 4-instance `ToggleLeft` `replace_all` occurrences all fall within this batch's line range before applying.

## What was NOT verified

- Not live-axe-verified in a browser — reuses token pairings already live-verified in `/dashboard/staff` (PR #2847) and applied identically in batch 1 (label/heading text, primary CTA button, muted icon/background), not new pairings.
- The remaining tab-content components (`VehiclePricingEditor`, `DocumentsEditor`, `AreaFeesEditor`+`FeeEditForm`, `SpinrPassAreaTab`, `IncentivesTab`, `CascadeEditor`) are explicitly out of scope for this PR — not silently dropped, see section 2.
