# Change Impact & Risk Log — #2816 Batch 7, sub-batch 43: ai-console promo savings + data-transfer notices

**Issue/gap identified**: `ai-console/page.tsx` used `text-green-600` for promo-savings text (×2) instead of `--success`; `data-transfer/ImportTab.tsx` used `text-green-600`/`text-amber-600` for a can-commit/cannot-commit status icon pair instead of `--success`/`--warning`; `data-transfer/SgiFormsTab.tsx` used a full hardcoded amber notification box (background, border, text, table borders) for its "still filed with regulator" removal-queue warning instead of `--warning` tokens.

**Root cause**: Predates the semantic-token system introduced for #2816.

**Fix/remediation**: Converted all of the above to their corresponding semantic tokens — both `ai-console` promo-savings spans → `text-success`; `ImportTab.tsx`'s can-commit icon pair → `text-success`/`text-warning`; `SgiFormsTab.tsx`'s entire removal-queue warning box (outer border/background, icon, heading/body text, inner table borders, per-row "no linked account" text, and the unresolvable-count footer) → `--warning` tokens throughout, since it's one cohesive warning notification rather than several independent signals.

**Risk & impact on existing functionality**: Pure CSS class-name substitution — no logic, props, or conditional rendering changed in any of the three files. Blast radius: isolated to these files; each is a self-contained tab/page component with no shared styling consumers.

**User experience effect**: Internal-admin-only surfaces (`/dashboard/ai-console`, `/dashboard/data-transfer`). Visually equivalent in both themes.

**Files modified**:
| File | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/ai-console/page.tsx` | Promo-savings text (×2) → success tokens | #2816 |
| `admin-dashboard/src/app/dashboard/data-transfer/ImportTab.tsx` | Can-commit/cannot-commit icon pair → success/warning tokens | #2816 |
| `admin-dashboard/src/app/dashboard/data-transfer/SgiFormsTab.tsx` | Removal-queue warning box (full notification) → warning tokens | #2816 |

**Before/after snippet**:
```tsx
// before (ImportTab.tsx)
{report.can_commit ? (
    <CheckCircle2 className="h-4 w-4 text-green-600" />
) : (
    <AlertTriangle className="h-4 w-4 text-amber-600" />
)}
// after
{report.can_commit ? (
    <CheckCircle2 className="h-4 w-4 text-success" />
) : (
    <AlertTriangle className="h-4 w-4 text-warning" />
)}
```

**Rollback plan**: `git revert` — pure class-name substitution, no data/migration involved.

**Verification performed**:
- `eslint` on all three files: 0 errors, 0 remaining raw-color warnings (fully converted); 2 unrelated pre-existing `react-hooks/set-state-in-effect` warnings.
- `vitest run`: 339/339 tests pass across all 35 test files.
- `tsc --noEmit`/`npm run build`: not re-run per-batch — the pre-existing, diff-unrelated `@spinr/shared` Turbopack failure in this environment was already root-caused via `git stash` against unmodified `origin/main` in sub-batch 31's PR (#4371).

**What was NOT verified**: No visual regression tooling exists in this repo (standing gap).
