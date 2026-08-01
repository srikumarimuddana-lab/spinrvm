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

Continuing `/dashboard/service-areas` remediation (batch 1 — page shell, PR #3119; batches 2-4 — General/Pricing/Documents tabs, PR #3129, both merged). This batch covers `AreaFeesEditor` + `FeeEditForm`: the Fees tab (per-area custom fees, tax config, cancellation fees, referral rewards) plus the fee edit sub-form. Same bug class: hardcoded `text-gray-*`, `bg-white`/`bg-gray-50`, `bg-red-500`/`accent-red-500` with zero dark-theme awareness.

While reading this section I also found the exact same `bg-red-600 hover:bg-red-700` AlertDialogAction "Delete" pattern used for this tab's fee-delete confirmation (line 1428) **and** noticed the page-shell's own area-delete confirmation dialog (line 563) uses the identical hardcoded pattern — that one falls inside batch 1's stated line range (~226-568) but wasn't listed as fixed or deferred in batch 1's Change Impact Log, i.e. it was an inadvertent miss, not a deliberate false-positive call. Fixing both in this batch since it's the same one-line, already-established `destructive`-token substitution — flagging it here rather than silently leaving the gap or letting it look like scope creep.

## 2. Scope of this PR — batch 5 of N

- **This PR**: `AreaFeesEditor` (Area Fees cards, Tax Configuration, Cancellation Fees, Referral Rewards) + `FeeEditForm` (fee edit sub-form's Save/Cancel buttons). Plus the one-line batch-1 gap fix noted above (line 563's delete-area confirm button).
- **Not yet done**: `SpinrPassAreaTab`, `IncentivesTab`, `CascadeEditor` — later tab-content components in the same file, planned as further follow-up batches. Note: `SpinrPassAreaTab`'s own delete-plan confirm dialog (line ~1746) has the same `bg-red-600 hover:bg-red-700` pattern — left for that batch, not silently dropped.

## 3. Fix / remediation

Same substitution rules as batches 1-4:

- `text-gray-800`/`text-gray-900` (section headings, fee name, fee amount) → `text-foreground`
- `text-gray-500`/`text-gray-400`/`text-gray-300` (subtitles, loading text, fee meta, hint text) → `text-muted-foreground`
- `bg-white` (fee-active card, tax-config card) → `bg-card`; `bg-gray-50` (empty state, fee-inactive card) → `bg-muted`
- `bg-red-500 text-white hover:bg-red-600` ("Add Fee" button, `FeeEditForm`'s "Save" button) → `bg-primary text-primary-foreground hover:bg-primary/90`
- `accent-red-500` (tax-mode radio buttons) → `accent-primary`
- `bg-gray-100 text-gray-600` (`FeeEditForm`'s "Cancel" button) → `bg-muted text-foreground` (reuses the established Cancel-button pairing from batch 1)
- Per-fee-card action buttons: Edit (`text-gray-400 hover:text-red-500`, non-destructive) → `text-muted-foreground hover:text-primary`; Disable/Enable (`text-gray-400 hover:text-gray-600`) → `text-muted-foreground hover:text-foreground`; Delete (`text-gray-300 hover:text-red-500`, destructive) → `text-muted-foreground hover:text-destructive` — same semantics established in batch 4 (non-destructive accents → `primary`, destructive actions → `destructive`)
- `AlertDialogAction`'s "Delete" confirm button (`bg-red-600 hover:bg-red-700`, at both line 563 — the page-shell's area-delete dialog, a batch-1 gap — and line 1428, this batch's fee-delete dialog) → `bg-destructive hover:bg-destructive/90`. The button's text color comes from `buttonVariants()`'s default (`text-primary-foreground`, confirmed by reading `components/ui/alert-dialog.tsx`) and isn't overridden by the passed `className`, so no text-color change was needed.

**No fee/tax/cancellation/referral logic touched** — `handleCreate`/`handleUpdate`/`handleDelete`/`confirmFeeDelete` and all `createAreaFee`/`updateAreaFee`/`deleteAreaFee` API calls, plus `FeeEditForm`'s local `form` state, are unchanged. Verified via `git diff | grep -viE "className"` returning empty.

**Left untouched**: `FeeEditForm`'s field labels — already used `text-muted-foreground` prior to this batch (verified by reading the component before editing; not something this session fixed, but noting it since it stood out against the rest of the file's still-hardcoded labels).

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to this file**, with one exception noted transparently: the line-563 fix touches the page-shell's delete-confirmation dialog (`ServiceAreasPage`'s own `confirmDelete`), not `AreaFeesEditor` — grepped, confirmed `AlertDialogAction`/`confirmDelete` here are local to this file, no other importer of this exact JSX.
- **No logic touched, verified mechanically**: `git diff <file> | grep -E "^[+-]" | grep -vE "^\+\+\+|^---" | grep -viE "className"` returns empty for this batch's changes — every changed line is a `className` string.
- No prop, state, or fee/tax/cancellation-fee/referral-reward calculation change; all handlers and API call signatures are unchanged.

## 5. User-experience effect

- Internal-admin facing only (`/dashboard/service-areas` → Fees tab, plus the area-delete confirmation dialog reachable from the page shell). Fee cards, tax-config panel, section headings, and all buttons/badges now render with theme-aware colors. Both "Delete" confirmation buttons (delete-area, delete-fee) now use the semantic `destructive` token instead of a hardcoded red — visually near-identical (the app's `--color-destructive` is a similar red hue) but theme-aware. No change to what's deletable, what confirmation is required, or delete behavior.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/service-areas/page.tsx` | Ported `AreaFeesEditor` + `FeeEditForm` from hardcoded Tailwind colors to semantic theme tokens; fixed a batch-1 gap (page-shell delete-confirm button) found while touching the same pattern | Batch 5 of the `/dashboard/service-areas` #2816 remediation |

## 7. Before / after

```
# Before
<div key={fee.id} className={`rounded-xl border p-4 ${fee.is_active ? 'bg-white' : 'bg-gray-50 opacity-60'}`}>
  <p className="font-bold text-gray-800">{fee.fee_name || fee.fee_type || 'Fee'}</p>
...
<AlertDialogAction onClick={confirmFeeDelete} className="bg-red-600 hover:bg-red-700">Delete</AlertDialogAction>
```

```
# After
<div key={fee.id} className={`rounded-xl border p-4 ${fee.is_active ? 'bg-card' : 'bg-muted opacity-60'}`}>
  <p className="font-bold text-foreground">{fee.fee_name || fee.fee_type || 'Fee'}</p>
...
<AlertDialogAction onClick={confirmFeeDelete} className="bg-destructive hover:bg-destructive/90">Delete</AlertDialogAction>
```

## 8. Rollback plan

- `git revert` is fully safe — pure `className` changes, no data/config/logic touched.

## 9. Verification performed

- [x] `npm run build` — clean, all routes compile.
- [x] `npm run lint` — 0 warnings anywhere in the edited ranges (line 563, and 1210-1517), before or after.
- [x] `git diff <file> | grep -viE "className"` — empty, confirming the diff is styling-only.
- [x] `git diff --stat` — 58 lines changed (29 insertions / 29 deletions), within the ~200-line-per-commit guideline.
- [x] `grep`-verified no remaining `text-gray-*`/`bg-gray-*`/`bg-white`/`bg-red-*`/`text-red-*`/`accent-red-*` occurrences in the component's line range after edits.
- [x] Read `components/ui/alert-dialog.tsx` to confirm the `AlertDialogAction` text color comes from `buttonVariants()`'s default and isn't affected by the `bg-destructive` swap.

## What was NOT verified

- Not live-axe-verified in a browser — reuses token pairings already live-verified in `/dashboard/staff` (PR #2847) and applied identically in prior batches; `bg-destructive`/`hover:bg-destructive/90` reuses the same `destructive` token already used for `text-destructive` in batches 3-4.
- `SpinrPassAreaTab`'s identical `bg-red-600 hover:bg-red-700` delete-confirm button (line ~1746) was noticed but intentionally left for that component's own batch — not silently dropped, see section 2.
- The remaining tab-content components (`SpinrPassAreaTab`, `IncentivesTab`, `CascadeEditor`) are explicitly out of scope for this PR.
