# Change Impact & Risk Log — #2816 Batch 7, sub-batch 46: staff-page role map documentation + delete confirm

**Issue/gap identified**: `staff/page.tsx`'s `ROLE_COLORS` (a 5-role categorical map: super_admin/operations/support/finance/custom) had no documented #2816 exclusion. The delete-confirmation dialog action used `bg-red-600 hover:bg-red-700` instead of the `--destructive` token.

**Root cause**: `ROLE_COLORS` predates the semantic-token system and was never given the categorical-exclusion documentation established for similar maps elsewhere (`driver-action-bar.tsx`'s `STATUS_CONFIG`, `audit-logs/page.tsx`'s `ACTION_CONFIG`). The delete button predates the token system too.

**Fix/remediation**:
- Documented `ROLE_COLORS` with an `eslint-disable`/`eslint-enable` block explaining it's a role-identity map, not a severity signal — a 3-token system can't distinguish 5 distinct staff roles.
- Converted the delete-confirmation button to the standard shadcn destructive pattern (`bg-destructive text-destructive-foreground hover:bg-destructive/90`).

Left untouched: the Reset MFA confirmation button (`bg-orange-600 hover:bg-orange-700`) — a solid-fill security-action button with no established "solid warning button" token pattern elsewhere in this codebase yet; converting it without a verified token equivalent risked an unreviewed contrast change, so it's deferred rather than guessed at. Also verified `support-tickets/page.tsx` and `support-tickets/trends/page.tsx` need no further work — both duplicate the already-documented "open" status exclusion and a decorative featured-stat accent, with no additional matches.

**Risk & impact on existing functionality**: Documentation-only change to `ROLE_COLORS` (no color values altered) plus one class-name substitution on the delete button. No logic, props, or conditional rendering changed. Blast radius: isolated to this one file.

**User experience effect**: Internal-admin-only surface (`/dashboard/staff`, super-admin only). Visually equivalent in both themes for the delete button; zero visual change for `ROLE_COLORS` (comment-only).

**Files modified**:
| File | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/staff/page.tsx` | Documented categorical `ROLE_COLORS`; converted delete-confirm button → destructive tokens | #2816 |

**Before/after snippet**:
```tsx
// before
<AlertDialogAction onClick={confirmDelete} className="bg-red-600 hover:bg-red-700">Delete</AlertDialogAction>
// after
<AlertDialogAction onClick={confirmDelete} className="bg-destructive text-destructive-foreground hover:bg-destructive/90">Delete</AlertDialogAction>
```

**Rollback plan**: `git revert` — a comment addition plus one class-name substitution, no data/migration involved.

**Verification performed**:
- `eslint` on the changed file: 0 errors, 4 warnings (1 pre-existing on the deliberately-untouched Reset MFA button, 3 unrelated pre-existing warnings: `react-hooks/exhaustive-deps`, `react-hooks/immutability`, `jsx-a11y/label-has-associated-control`).
- `vitest run`: 339/339 tests pass across all 35 test files.
- `tsc --noEmit`/`npm run build`: not re-run per-batch — the pre-existing, diff-unrelated `@spinr/shared` Turbopack failure in this environment was already root-caused via `git stash` against unmodified `origin/main` in sub-batch 31's PR (#4371).

**What was NOT verified**: No visual regression tooling exists in this repo (standing gap).
