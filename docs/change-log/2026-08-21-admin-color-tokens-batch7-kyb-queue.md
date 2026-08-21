# Change Impact & Risk Log — #2816 Batch 7, sub-batch 38: KYB queue error banner + reject confirm

**Issue/gap identified**: `corporate-accounts/kyb-queue/page.tsx` used hardcoded `border-red-200 bg-red-50 text-red-700` for its load-error banner and `bg-red-600 hover:bg-red-700` for the reject-confirmation dialog button, instead of the `--destructive` token.

**Root cause**: Predates the semantic-token system introduced for #2816.

**Fix/remediation**: Converted the error banner to `border-destructive/30 bg-destructive/10 text-destructive`, and the reject-confirmation `AlertDialogAction` to the standard shadcn destructive-button pattern (`bg-destructive text-destructive-foreground hover:bg-destructive/90`) already used for destructive confirmations elsewhere in the app (e.g. `service-areas/page.tsx`'s delete confirmation).

Left untouched: the solid-fill "Approve" button (`bg-emerald-600 hover:bg-emerald-700`, no `dark:` variant — a fixed shade rather than the `--success` token, consistent with the established policy of not converting fixed-shade solid buttons to `bg-success` given the known dark-mode `--success`-vs-white-text contrast risk), and the blue "Preview" document link (informational navigation affordance, not a signal).

**Risk & impact on existing functionality**: Pure CSS class-name substitution on two elements — no logic, props, or conditional rendering changed. `--destructive`/`--destructive-foreground` are pre-existing tokens already used identically elsewhere (e.g. the "Reject" outline button in this same file already used `text-destructive`). Blast radius: isolated to this one file.

**User experience effect**: Internal-admin-only surface (`/dashboard/corporate-accounts/kyb-queue`). Visually equivalent in both themes.

**Files modified**:
| File | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/corporate-accounts/kyb-queue/page.tsx` | Error banner and reject-confirm button → destructive tokens | #2816 |

**Before/after snippet**:
```tsx
// before
{error && (
    <div className="rounded-md border border-red-200 bg-red-50 px-4 py-2 text-sm text-red-700">
        {error}
    </div>
)}
...
<AlertDialogAction onClick={confirmReject} className="bg-red-600 hover:bg-red-700">
// after
{error && (
    <div className="rounded-md border border-destructive/30 bg-destructive/10 px-4 py-2 text-sm text-destructive">
        {error}
    </div>
)}
...
<AlertDialogAction onClick={confirmReject} className="bg-destructive text-destructive-foreground hover:bg-destructive/90">
```

**Rollback plan**: `git revert` — pure class-name substitution, no data/migration involved.

**Verification performed**:
- `eslint` on the changed file: 0 errors, 3 warnings (2 pre-existing on the deliberately-untouched Approve button and Preview link, 1 unrelated `react-hooks/set-state-in-effect`).
- `vitest run`: 339/339 tests pass across all 35 test files.
- `tsc --noEmit`/`npm run build`: not re-run per-batch — the pre-existing, diff-unrelated `@spinr/shared` Turbopack failure in this environment was already root-caused via `git stash` against unmodified `origin/main` in sub-batch 31's PR (#4371).

**What was NOT verified**: No visual regression tooling exists in this repo (standing gap).
