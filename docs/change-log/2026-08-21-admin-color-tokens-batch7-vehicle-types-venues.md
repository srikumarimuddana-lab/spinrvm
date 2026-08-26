# Change Impact & Risk Log — #2816 Batch 7, sub-batch 41: vehicle-types delete confirm + venues delete buttons

**Issue/gap identified**: `vehicle-types/page.tsx`'s delete-confirmation dialog action used `bg-red-600 hover:bg-red-700` instead of the `--destructive` token; `venues/page.tsx`'s two delete-icon buttons (pickup-point removal, venue removal) used `text-red-600 hover:bg-red-50 dark:hover:bg-red-900/20` instead of `--destructive` tokens.

**Root cause**: Predates the semantic-token system introduced for #2816.

**Fix/remediation**:
- `vehicle-types/page.tsx`: converted the delete-confirmation `AlertDialogAction` to the standard shadcn destructive-button pattern (`bg-destructive text-destructive-foreground hover:bg-destructive/90`), matching other converted delete confirmations in this codebase (e.g. `service-areas/page.tsx`, `corporate-accounts/kyb-queue/page.tsx`).
- `venues/page.tsx`: converted both delete-icon buttons to `text-destructive hover:bg-destructive/10`.

Left untouched in `venues/page.tsx`: the pickup-point selection-highlight theme (amber row background, amber/sky point-number badges) — this is a "currently selected item" UI state, not a success/warning/destructive signal, consistent with prior sub-batches' handling of selection/active-state theming (e.g. tab-underline accents).

**Risk & impact on existing functionality**: Pure CSS class-name substitution — no logic, props, or conditional rendering changed. Blast radius: isolated to these two files; both delete actions already call the same `handleDelete`/`remove` functions unchanged.

**User experience effect**: Internal-admin-only surfaces (`/dashboard/vehicle-types`, `/dashboard/venues`). Visually equivalent in both themes.

**Files modified**:
| File | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/vehicle-types/page.tsx` | Delete-confirmation button → destructive tokens | #2816 |
| `admin-dashboard/src/app/dashboard/venues/page.tsx` | Two delete-icon buttons → destructive tokens | #2816 |

**Before/after snippet**:
```tsx
// before (vehicle-types/page.tsx)
<AlertDialogAction onClick={confirmDelete} className="bg-red-600 hover:bg-red-700">Delete</AlertDialogAction>
// after
<AlertDialogAction onClick={confirmDelete} className="bg-destructive text-destructive-foreground hover:bg-destructive/90">Delete</AlertDialogAction>
```

**Rollback plan**: `git revert` — pure class-name substitution, no data/migration involved.

**Verification performed**:
- `eslint` on both files: 0 errors, 12 warnings (all pre-existing on the deliberately-untouched selection-highlight theme in `venues/page.tsx` plus one unrelated `react-hooks/set-state-in-effect`).
- `vitest run`: 339/339 tests pass across all 35 test files.
- `tsc --noEmit`/`npm run build`: not re-run per-batch — the pre-existing, diff-unrelated `@spinr/shared` Turbopack failure in this environment was already root-caused via `git stash` against unmodified `origin/main` in sub-batch 31's PR (#4371).

**What was NOT verified**: No visual regression tooling exists in this repo (standing gap).
