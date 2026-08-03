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

Found via the same parallel triage that surfaced the single-spot fixes in #3378. `/dashboard/heatmap`'s map-loading fallback (shown while the MapLibre GL component dynamically loads client-side, and again inline further down the page) uses a hardcoded `bg-gray-100`/`text-gray-400` placeholder box — the same map-loading-fallback pattern already fixed twice in `/dashboard/service-areas` (batches 1 and 4, PRs #3119/#3129).

## 2. Scope of this batch

One file, two identical occurrences of the same fallback markup (the dynamic-import `loading:` callback at the top of the file, and an inline duplicate further down).

## 3. Fix / remediation

- `bg-gray-100 animate-pulse` → `bg-muted animate-pulse` (both occurrences)
- `animate-spin text-gray-400` → `animate-spin text-muted-foreground` (both occurrences)

Used `replace_all` since both occurrences are the exact same fallback markup and both need the identical fix — confirmed via `grep` that only these two lines matched before applying.

**No logic touched** — this is purely the `loading:` fallback UI for `next/dynamic`; the map component itself, data fetching, and filters are untouched. Verified via `git diff | grep -viE "className"` returning empty.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated** — the fallback only renders during the ~1-frame window before MapLibre GL's client bundle loads; grepped, no other file references this exact markup.
- No prop, state, or data-flow change.

## 5. User-experience effect

- Internal-admin facing only (`/dashboard/heatmap`). The brief loading placeholder now matches the app's dark theme instead of flashing a hardcoded light-gray box.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/heatmap/page.tsx` | Map-loading fallback ported to theme tokens | #2816 remediation |

## 7. Before / after

```
# Before
<div className="w-full h-[600px] bg-gray-100 animate-pulse rounded-lg flex items-center justify-center">
    <Loader2 className="h-8 w-8 animate-spin text-gray-400" />
</div>
```

```
# After
<div className="w-full h-[600px] bg-muted animate-pulse rounded-lg flex items-center justify-center">
    <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
</div>
```

## 8. Rollback plan

- `git revert` is fully safe — pure `className` changes, no data/config/logic touched.

## 9. Verification performed

- [x] `npm run build` — clean.
- [x] `npm run lint` — 0 new warnings; all warnings present are pre-existing and on unrelated lines.
- [x] `git diff | grep -viE "className"` — empty, confirming styling-only.
- [x] `git diff --stat` — 8 lines changed (4 insertions / 4 deletions).
- [x] `grep`-verified both occurrences fixed, no remaining `bg-gray-100`/`text-gray-400` in the file.

## What was NOT verified

- Not live-axe-verified in a browser — reuses the exact `bg-muted`/`text-muted-foreground` pairing already fixed and reviewed twice in `/dashboard/service-areas`.
