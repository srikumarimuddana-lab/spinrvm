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

A parallel triage of the ~93 remaining files in #2816's backlog (6 agents, ~93 files, cross-checked against the established false-positive taxonomy) found that the vast majority are false positives (self-contained badges, overlay UI, standalone pages, or already `dark:`-handled). Of the few genuine issues found, three were single-line inconsistencies: a file otherwise fully converts a given accent color to a `dark:`-paired variant everywhere it's used, except one spot that was missed.

## 2. Scope of this batch

Three unrelated files, one line each, batched together only because each fix is a trivial one-line addition of a `dark:` variant (or `bg-muted` swap) matching a pattern the same file already uses correctly elsewhere:

- `admin-dashboard/src/app/dashboard/quests/page.tsx` — participant progress-bar track
- `admin-dashboard/src/app/dashboard/rides/_components/ride-list.tsx` — "+tip" amount text
- `admin-dashboard/src/app/dashboard/promotions/page.tsx` — "Discount Applied" usage-history cell

## 3. Fix / remediation

- `quests/page.tsx`: progress-bar track `bg-gray-200` → `bg-muted` (the fill `bg-blue-500` is a deliberate accent, untouched)
- `ride-list.tsx`: `text-emerald-600` → `text-emerald-600 dark:text-emerald-400` — the sibling "Time" stat two rows below already uses this exact pairing; this was a missed spot, not a systemic gap
- `promotions/page.tsx`: `text-emerald-600` → `text-emerald-600 dark:text-emerald-400` — the "Free Ride" badge earlier in the same file already uses this exact pairing

**No logic touched** — verified via `git diff | grep -viE "className"` returning empty.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated**, one line per file, no shared component touched.
- No prop, state, or data-flow change.

## 5. User-experience effect

- Internal-admin facing only. Three small text/UI elements now render legibly in dark mode where they previously used a color with no dark-mode pairing (barely visible or over-bright against the dark page background).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/quests/page.tsx` | Progress-bar track color → `bg-muted` | #2816 remediation |
| `admin-dashboard/src/app/dashboard/rides/_components/ride-list.tsx` | Added missing `dark:text-emerald-400` pairing | #2816 remediation |
| `admin-dashboard/src/app/dashboard/promotions/page.tsx` | Added missing `dark:text-emerald-400` pairing | #2816 remediation |

## 7. Before / after

```
# Before (ride-list.tsx)
<p className="text-[10px] font-semibold text-emerald-600 mt-0.5">+{formatCurrency(ride.tip_amount)} tip</p>
# After
<p className="text-[10px] font-semibold text-emerald-600 dark:text-emerald-400 mt-0.5">+{formatCurrency(ride.tip_amount)} tip</p>
```

## 8. Rollback plan

- `git revert` is fully safe — pure `className` changes, no data/config/logic touched.

## 9. Verification performed

- [x] `npm run build` — clean, all routes compile.
- [x] `npm run lint` — 0 new warnings; all warnings present are pre-existing and on unrelated lines (confirmed by line number).
- [x] `git diff | grep -viE "className"` — empty, confirming styling-only.
- [x] `git diff --stat` — 3 lines changed across 3 files.

## What was NOT verified

- Not live-axe-verified in a browser — each fix reuses a `dark:` pairing already proven correct elsewhere in the same file, not a new pairing.
