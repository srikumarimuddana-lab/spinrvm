# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-03 |
| Author | Claude Code |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | (this PR) |
| Related issue or gap ID | #2785 (admin-dashboard visual-refresh epic), Phase 5; #2816 (91-file hardcoded-color backlog) |

## 1. Issue / gap identified

Found via the post-service-areas #2816 triage — a systemic, cross-file pattern rather than an isolated bug: outline action buttons (Suspend/Ban/Unban/Approve/Reject) styled as `text-{color}-600 border-{color}-200 hover:bg-{color}-50` with **no** `dark:` variant, even in files whose surrounding badges/panels already handle dark mode correctly. In `driver-action-bar.tsx`, the `STATUS_CONFIG` badges (used for the same colors, lines 34-36) already ship the full `dark:` treatment — only the interactive buttons below them were missed.

## 2. Scope of this batch

Two driver-domain files, first of two/three planned batches covering this same systemic pattern (the other files — `corporate-accounts` pages — are a separate domain and follow-up batch):

- `admin-dashboard/src/app/dashboard/drivers/_components/driver-action-bar.tsx` — 8 occurrences (3× orange/Suspend, 4× red/Ban, 1× emerald/Unban)
- `admin-dashboard/src/app/dashboard/drivers/page.tsx` — 2 occurrences (`DocCard`'s Approve/Reject buttons)

## 3. Fix / remediation

Applied the convention established in the prior `create-ride-modal`/`monitoring/ride-panel` batch, but matched against **this exact file's own existing precedent** where one existed: `driver-action-bar.tsx`'s `STATUS_CONFIG` map (lines 34-36) already uses `border-{color}-200 dark:border-{color}-800` for the same colors — used that border-darkness exactly rather than the `-700` used in the ride-panel batch, for intra-file consistency:

- `text-orange-600 border-orange-200 hover:bg-orange-50` → `+ dark:text-orange-400 dark:border-orange-800 dark:hover:bg-orange-900/20` (3 occurrences, `replace_all` after confirming via `grep` all 3 are identical and intended)
- `text-red-600 border-red-200 hover:bg-red-50` → `+ dark:text-red-400 dark:border-red-800 dark:hover:bg-red-900/20` (4 occurrences in `driver-action-bar.tsx` + 1 in `drivers/page.tsx`'s `DocCard`)
- `text-emerald-600 border-emerald-200 hover:bg-emerald-50` → `+ dark:text-emerald-400 dark:border-emerald-800 dark:hover:bg-emerald-900/20` (1 occurrence in each file)

**No logic touched** — the Suspend/Ban/Unban/Approve/Reject click handlers, `docBusy` disabled-state logic, and all API calls are unchanged. Verified via `git diff | grep -viE "className"` returning empty.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated** — `driver-action-bar.tsx` is a component used only within the drivers detail view; `DocCard` is a sub-component local to `drivers/page.tsx`. Grepped for other consumers — none beyond their existing usage.
- No prop, state, or action-handler change; button click behavior (suspend/ban/unban/approve/reject) is unchanged.

## 5. User-experience effect

- Internal-admin facing only (driver detail view's action bar, document-review Approve/Reject buttons). These outline buttons now render legibly in dark mode instead of using a border/hover tint calibrated only for light backgrounds.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/drivers/_components/driver-action-bar.tsx` | Added `dark:` variants to 8 outline action buttons | #2816 remediation |
| `admin-dashboard/src/app/dashboard/drivers/page.tsx` | Added `dark:` variants to `DocCard`'s Approve/Reject buttons | #2816 remediation |

## 7. Before / after

```
# Before
<Button size="sm" variant="outline" className="text-red-600 border-red-200 hover:bg-red-50"
```

```
# After
<Button size="sm" variant="outline" className="text-red-600 dark:text-red-400 border-red-200 dark:border-red-800 hover:bg-red-50 dark:hover:bg-red-900/20"
```

## 8. Rollback plan

- `git revert` is fully safe — pure `className` changes, no data/config/logic touched.

## 9. Verification performed

- [x] `npm run build` — clean.
- [x] `npm run lint` — 0 new warnings in either file, before or after.
- [x] `git diff | grep -viE "className"` — empty, confirming styling-only.
- [x] `git diff --stat` — 20 lines changed across 2 files.
- [x] `grep`-verified all 8+2 button occurrences now carry `dark:` variants, and confirmed via `grep` before using `replace_all` that every match was one of the intended buttons.
- [x] Matched the border-darkness (`dark:border-{color}-800`, not `-700`) to this file's own pre-existing `STATUS_CONFIG` badge convention rather than blindly reusing the previous batch's shade.

## What was NOT verified

- Not live-axe-verified in a browser.
- The remaining `corporate-accounts` pages with the same systemic pattern (`policy`, `subscription`, `members`, `[id]`, `kyb-queue`) are explicitly out of scope for this batch — planned as a separate follow-up.
