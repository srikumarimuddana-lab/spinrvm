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

Continuing `/dashboard/service-areas` remediation (batch 1 — page shell, PR #3119; batches 2-4 — General/Pricing/Documents tabs, PR #3129; batch 5 — Fees tab, PR #3135; all merged; batch 6 — Spinr Pass tab, PR #3138, open). This batch covers `CascadeEditor`: the dispatch-cascade-rules tab (vehicle-type upgrade fallback rules used by dispatch when the exact requested vehicle type has no available driver). Same bug class: hardcoded `text-gray-*`, `bg-white`/`bg-gray-50`, `bg-red-500`/`accent-red-500` with zero dark-theme awareness.

## 2. Scope of this PR — batch 7 of N

- **This PR**: `CascadeEditor` only (heading/description, empty states, per-rule cards, Add/Save action bar).
- **Not yet done**: `IncentivesTab` — the last tab-content component in the file (comes after `CascadeEditor` in file order, not before as originally assumed — corrected the batch sequence accordingly), planned as batch 8, the final batch for this file.

## 3. Fix / remediation

Same substitution rules as batches 1-6:

- `text-gray-800` (heading) → `text-foreground`; `text-gray-500`/`text-gray-400`/`text-gray-300` (description, empty-state text/icon, "no other vehicle types" hint, cascade-order summary, arrow icon) → `text-muted-foreground`
- "No cascade rules" empty state: `bg-gray-50 rounded-xl border-2 border-dashed border-gray-200` → `bg-muted rounded-xl border-2 border-dashed border-border` (matches the exact precedent established in batch 4's `DocumentsEditor` empty state)
- Rule card: `bg-white` → `bg-card`
- Checkbox accent: `accent-red-500` → `accent-primary`
- Remove-rule icon button (`text-gray-300 hover:text-red-500`, destructive) → `text-muted-foreground hover:text-destructive`
- "+ Add cascade rule" CTA link (`text-red-500 hover:text-red-700`) → `text-primary hover:text-primary/80`, same "add" pattern established in batches 3/4/6
- "Save Cascade Rules" button: the `saved` (green, success) state is a deliberate colored indicator and untouched; `dirty ? 'bg-red-500 text-white hover:bg-red-600' : 'bg-gray-100 text-gray-400'` → `dirty ? 'bg-primary text-primary-foreground hover:bg-primary/90' : 'bg-muted text-muted-foreground'`, same dirty/clean Save-button pattern used in every prior batch that has one

**No dispatch-cascade logic touched** — this component only edits the *configuration* consumed by dispatch (which vehicle types cascade to which), not dispatch matching itself. `addRule`/`removeRule`/`setFrom`/`toggleTo`/`handleSave` and all `rules`/`dirty`/`saving`/`saved` state are unchanged. Verified via `git diff | grep -viE "className"` returning empty.

**Left untouched**: the "No vehicle types configured for this area" empty state (`bg-amber-50`/`border-amber-200`/`text-amber-700`/`text-amber-600`/`text-amber-300`) — a deliberate colored warning panel, same false-positive category documented since batch 1/#2816.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to this file.** `CascadeEditor` is defined and used only within `service-areas/page.tsx` — grepped, no other importer. This component edits `cascade_map` configuration that `services/dispatch_service.py` reads at match time, but no dispatch code was touched — only this admin UI's `className` strings.
- **No logic touched, verified mechanically**: `git diff <file> | grep -E "^[+-]" | grep -vE "^\+\+\+|^---" | grep -viE "className"` returns empty for this batch's changes — every changed line is a `className` string.
- No prop, state, or cascade-rule CRUD/save behavior change.

## 5. User-experience effect

- Internal-admin facing only (`/dashboard/service-areas` → Dispatch Cascade tab). Heading, empty states, rule cards, and action buttons now render with theme-aware colors. No change to what rules exist, what's editable, or save behavior — and no change whatsoever to how dispatch actually applies cascade rules to a booking.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/service-areas/page.tsx` | Ported `CascadeEditor` from hardcoded Tailwind colors to semantic theme tokens | Batch 7 of the `/dashboard/service-areas` #2816 remediation |

## 7. Before / after

```
# Before
<div key={idx} className="rounded-xl border bg-white p-4">
...
<button onClick={() => removeRule(idx)} className="pt-6 text-gray-300 hover:text-red-500" title="Remove rule">
```

```
# After
<div key={idx} className="rounded-xl border bg-card p-4">
...
<button onClick={() => removeRule(idx)} className="pt-6 text-muted-foreground hover:text-destructive" title="Remove rule">
```

## 8. Rollback plan

- `git revert` is fully safe — pure `className` changes, no data/config/logic touched.

## 9. Verification performed

- [x] `npm run build` — clean, all routes compile.
- [x] `npm run lint` — 0 new warnings in the edited range (lines 1766-1933); the one pre-existing warning at line 1780 (`cascadeMap`-sync `useEffect`) is on logic this batch didn't touch.
- [x] `git diff <file> | grep -viE "className"` — empty, confirming the diff is styling-only.
- [x] `git diff --stat` — 28 lines changed (14 insertions / 14 deletions), well under the ~200-line-per-commit guideline.
- [x] `grep`-verified no remaining `text-gray-*`/`bg-gray-*`/`bg-white`/`bg-red-*`/`text-red-*`/`accent-red-*` occurrences in the component's line range after edits (amber warning panel intentionally excluded from this check as a known-deferred false positive).

## What was NOT verified

- Not live-axe-verified in a browser — reuses token pairings already live-verified in `/dashboard/staff` (PR #2847) and applied identically in every prior batch of this file.
- `IncentivesTab` (batch 8, the final component in this file) is explicitly out of scope for this PR — not silently dropped, see section 2.
