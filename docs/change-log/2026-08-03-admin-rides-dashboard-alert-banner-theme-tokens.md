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

Found via the post-service-areas #2816 triage. `/dashboard/rides` (the ride list page) and `/dashboard` (the main admin landing page) each have a full-width error/warning banner using hardcoded `bg-red-50`/`bg-amber-50` with zero `dark:` handling.

## 2. Scope of this batch

Two files:

- `admin-dashboard/src/app/dashboard/rides/page.tsx` — "Failed to load rides" banner + its "Retry" link
- `admin-dashboard/src/app/dashboard/page.tsx` — "Dashboard data is temporarily unavailable" banner and the "Revenue figures need PostgREST aggregate functions enabled" banner (same amber styling, two occurrences)

## 3. Fix / remediation

Same `dark:` idiom applied in the prior payouts batch (`bg-{color}-50 dark:bg-{color}-900/10`, `border-{color}-200 dark:border-{color}-800`, `text-{color}-700/800 dark:text-{color}-300`):

- `rides/page.tsx`: `border-red-200 bg-red-50 ... text-red-700` (banner + Retry link) → added `dark:border-red-800 dark:bg-red-900/10 ... dark:text-red-300`
- `dashboard/page.tsx`: `border-amber-200 bg-amber-50 ... text-amber-800` (both the data-unavailable banner and the aggregates-disabled banner — same exact styling, fixed with one `replace_all` per class after confirming via `grep` both occurrences were the intended two spots and nothing else in the file matched) → added `dark:border-amber-800 dark:bg-amber-900/10 ... dark:text-amber-300`

**No logic touched** — `loadError`/`reload()` in `rides/page.tsx` and `error`/`aggOff` computation in `dashboard/page.tsx` are unchanged. Verified via `git diff | grep -viE "className"` returning empty.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated** — both banners are page-local markup, not shared components. Grepped for other consumers of the exact class strings before using `replace_all` on `dashboard/page.tsx` — confirmed only the two intended banners matched.
- No prop, state, or data-flow change.

## 5. User-experience effect

- Internal-admin facing only (`/dashboard/rides` and `/dashboard`, the landing page every admin sees first). Both error/warning banners now render legibly in dark mode instead of as a bright box against the dark page background. No change to when these banners appear or what they say.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/rides/page.tsx` | Added `dark:` variants to the load-error banner | #2816 remediation |
| `admin-dashboard/src/app/dashboard/page.tsx` | Added `dark:` variants to both amber banners | #2816 remediation |

## 7. Before / after

```
# Before
<div className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 flex items-center justify-between">
```

```
# After
<div className="rounded-md border border-red-200 dark:border-red-800 bg-red-50 dark:bg-red-900/10 px-4 py-3 text-sm text-red-700 dark:text-red-300 flex items-center justify-between">
```

## 8. Rollback plan

- `git revert` is fully safe — pure `className` changes, no data/config/logic touched.

## 9. Verification performed

- [x] `npm run build` — clean.
- [x] `npm run lint` — 0 new warnings; all warnings present are pre-existing and on unrelated lines.
- [x] `git diff | grep -viE "className"` — empty, confirming styling-only.
- [x] `git diff --stat` — 8 lines changed across 2 files.
- [x] `grep`-verified the `dashboard/page.tsx` `replace_all` only touched the two intended banners.

## What was NOT verified

- Not live-axe-verified in a browser — reuses the same `dark:` idiom already established and applied in the payouts batch and elsewhere in this codebase.
