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

Continuing `/dashboard/service-areas` remediation (batch 1 — page shell, PR #3119; batches 2-4 — General/Pricing/Documents tabs, PR #3129; batch 5 — Fees tab, PR #3135; all merged). This batch covers `SpinrPassAreaTab`: the Spinr Pass tab (enable/require kill switches, subscription plan CRUD, subscriber list). Same bug class: hardcoded `text-gray-*`, `bg-white`/`bg-gray-50`, `bg-red-500`/`accent-red-500`/`text-red-500` with zero dark-theme awareness.

## 2. Scope of this PR — batch 6 of N

- **This PR**: `SpinrPassAreaTab` only (kill-switch panels, plan create/edit form, plan cards, subscribers table, delete-plan confirm dialog).
- **Not yet done**: `IncentivesTab`, `CascadeEditor` — the last two tab-content components in the same file, planned as further follow-up batches (final batches for this file).

## 3. Fix / remediation

Same substitution rules as batches 1-5:

- Kill-switch panels: the *enabled/required* colored states (`bg-green-50`/`bg-amber-50` + matching border) are deliberate semantic indicators and untouched; the *disabled/optional* neutral state (`bg-gray-50 border-gray-200`) → `bg-muted border-border` — same "large panel, not a badge" reasoning as prior batches (this fills significant page real estate, unlike the small self-contained pills left alone elsewhere).
- `text-gray-800`/`text-gray-900` (headings, plan name) → `text-foreground`; `text-gray-500`/`text-gray-400`/`text-gray-300` (subtitles, meta text, empty-state text/icon) → `text-muted-foreground`
- `bg-red-500 text-white hover:bg-red-600` ("New Plan", "Create Plan"/"Save" buttons) → `bg-primary text-primary-foreground hover:bg-primary/90`
- `bg-white` (create/edit form container, plan cards, subscribers table) → `bg-card`
- "Rides Per Day" chip buttons (`bg-red-500 text-white border-red-500` selected / `bg-white border-gray-200` idle) → `bg-primary text-primary-foreground border-primary` / `bg-card border-border` — same "selected chip → primary" idiom used in batch 4's editing-card highlight
- `accent-red-500` (Active checkbox) → `accent-primary`
- `bg-gray-100 text-gray-600` (form Cancel button) → `bg-muted text-foreground` (established Cancel pairing)
- Plan-card `ToggleLeft` off-state icon (`text-gray-300`) → `text-muted-foreground` (established `ToggleLeft` convention from batch 2)
- Plan price (`text-red-500`, brand-color emphasis) → `text-primary` — consistent with every other brand-red-as-CTA/accent instance mapped to `primary` across all prior batches
- `text-gray-600` (rides/day body text, Edit button text) → `text-foreground` — this file's established split (500/400/300 → muted, 600/700/800/900 → foreground)
- Plan-card Edit/Delete buttons: Edit (`text-gray-600 hover:bg-gray-50`, non-destructive) → `text-foreground hover:bg-muted`; Delete (`text-red-500 hover:bg-red-50`, persistently-red destructive action) → `text-destructive hover:bg-destructive/10`
- Subscribers table: container `bg-white` → `bg-card`; header row `bg-gray-50` → `bg-muted`; header labels `text-gray-600` → **`text-foreground`, not `text-muted-foreground`** — required by the small-text-on-`bg-muted` WCAG rule established in PR #2847 (`text-muted-foreground` on `bg-muted` measures ~4.39:1, below AA for these `text-xs` labels); "Expires" cell text (`text-gray-500`, sits on the plain row background, not `bg-muted`) → `text-muted-foreground`, no WCAG conflict there
- "Load subscribers" link (`text-red-500`, an "add/action" CTA link) → `text-primary`, matching the "+ Add..." link convention from batches 3-4
- `AlertDialogAction`'s "Delete" confirm button (`bg-red-600 hover:bg-red-700`) → `bg-destructive hover:bg-destructive/90`, same fix already applied to the identical pattern in batch 5 (page-shell + fee-delete dialogs)

**No plan/subscription logic touched** — `handleSubmit`/`handleEdit`/`handleDeletePlan`/`confirmPlanDelete`/`handleTogglePlan`/`loadSubs` and all `createSubscriptionPlan`/`updateSubscriptionPlan`/`deleteSubscriptionPlan`/`getDriverSubscriptions` API calls are unchanged. Verified via `git diff | grep -viE "className|ToggleLeft"` returning empty.

**Left untouched**: the subscriber-status badge (`bg-green-100 text-green-700` / `bg-gray-100 text-gray-600`) — self-contained categorical pill, same false-positive category documented since batch 1/#2816.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to this file.** `SpinrPassAreaTab` is defined and used only within `service-areas/page.tsx` — grepped, no other importer.
- **No logic touched, verified mechanically**: `git diff <file> | grep -E "^[+-]" | grep -vE "^\+\+\+|^---" | grep -viE "className|ToggleLeft"` returns empty for this batch's changes — every changed line is a `className` string or the `ToggleLeft` icon's color prop.
- No prop, state, or plan/subscription-CRUD behavior change.

## 5. User-experience effect

- Internal-admin facing only (`/dashboard/service-areas` → Spinr Pass tab). Kill-switch panels, plan form, plan cards, and subscribers table now render with theme-aware colors. The plan price and "Load subscribers" link shift from brand-red to the `primary` token (same hue in light mode); the "Delete" confirm buttons shift to `destructive` (also the same hue). No change to what's editable, deletable, or how subscription plans behave.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/service-areas/page.tsx` | Ported `SpinrPassAreaTab` from hardcoded Tailwind colors to semantic theme tokens | Batch 6 of the `/dashboard/service-areas` #2816 remediation |

## 7. Before / after

```
# Before
<thead className="bg-gray-50 text-left">
  <tr><th className="px-4 py-2 font-semibold text-gray-600 text-xs">Driver</th>...
```

```
# After
<thead className="bg-muted text-left">
  <tr><th className="px-4 py-2 font-semibold text-foreground text-xs">Driver</th>...
```
(`text-foreground`, not `text-muted-foreground`, to avoid the small-text-on-`bg-muted` AA failure already documented in this file's prior batches.)

## 8. Rollback plan

- `git revert` is fully safe — pure `className`/icon-color changes, no data/config/logic touched.

## 9. Verification performed

- [x] `npm run build` — clean, all routes compile.
- [x] `npm run lint` — 0 new warnings in the edited range (lines 1518-1752); the one warning at line 1780 is in `CascadeEditor`, a different, not-yet-touched component.
- [x] `git diff <file> | grep -viE "className|ToggleLeft"` — empty, confirming the diff is styling-only.
- [x] `git diff --stat` — 80 lines changed (40 insertions / 40 deletions), within the ~200-line-per-commit guideline.
- [x] `grep`-verified only the one deliberately-deferred categorical status badge remains hardcoded in the component's line range after edits.
- [x] Applied the small-text-on-`bg-muted` WCAG rule (established in PR #2847, reused in batch 4) to the new subscribers-table header pairing rather than defaulting to `text-muted-foreground`.

## What was NOT verified

- Not live-axe-verified in a browser — reuses token pairings already live-verified in `/dashboard/staff` (PR #2847) and applied identically in prior batches, including the WCAG-aware muted-background table-header pairing.
- The remaining tab-content components (`IncentivesTab`, `CascadeEditor`) are explicitly out of scope for this PR — not silently dropped, see section 2.
