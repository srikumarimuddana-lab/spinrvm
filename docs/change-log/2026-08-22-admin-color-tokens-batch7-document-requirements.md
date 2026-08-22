# Change Impact & Risk Log — #2816 Batch 7, sub-batch 49: document-requirements stat + delete icon

**Issue/gap identified**: `documents/requirements/page.tsx` used `text-red-600` for the "Required" stat count and `text-red-500` for the delete-icon button, instead of the `--destructive` token — inconsistent with the same page's "Required" table badge, which already used `variant="destructive"`.

**Root cause**: Predates the semantic-token system introduced for #2816.

**Fix/remediation**: Converted both to `text-destructive`. This file is now fully converted — 0 remaining raw-color warnings.

**Risk & impact on existing functionality**: Pure CSS class-name substitution on two elements — no logic, props, or conditional rendering changed. Blast radius: isolated to this one file.

**User experience effect**: Internal-admin-only surface (`/dashboard/documents/requirements`). Visually equivalent in both themes, and now visually consistent with the same page's already-token-based "Required" badge.

**Files modified**:
| File | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/documents/requirements/page.tsx` | "Required" stat count + delete icon → destructive tokens | #2816 |

**Before/after snippet**:
```tsx
// before
<p className="text-2xl font-bold text-red-600">{requirements.filter((r) => r.is_required).length}</p>
...
<Trash2 className="h-3 w-3 text-red-500" />
// after
<p className="text-2xl font-bold text-destructive">{requirements.filter((r) => r.is_required).length}</p>
...
<Trash2 className="h-3 w-3 text-destructive" />
```

**Rollback plan**: `git revert` — pure class-name substitution, no data/migration involved.

**Verification performed**:
- `eslint` on the changed file: 0 errors, 2 warnings (1 unrelated pre-existing `react-hooks/set-state-in-effect`, no raw-color warnings remaining).
- `vitest run`: 339/339 tests pass across all 35 test files.
- `tsc --noEmit`/`npm run build`: not re-run per-batch — the pre-existing, diff-unrelated `@spinr/shared` Turbopack failure in this environment was already root-caused via `git stash` against unmodified `origin/main` in sub-batch 31's PR (#4371).

**What was NOT verified**: No visual regression tooling exists in this repo (standing gap).
