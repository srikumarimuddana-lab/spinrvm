# Change Impact & Risk Log — #2816 Batch 7, sub-batch 48: settings-page deliverability + MFA status

**Issue/gap identified**: `settings/page.tsx` used `text-red-600` for the email-deliverability "Failed" count (conditional on failure rate > 1%) and `text-green-600 dark:text-green-400` for the "MFA is enabled on your account" confirmation, instead of `--destructive`/`--success` tokens.

**Root cause**: Predates the semantic-token system introduced for #2816.

**Fix/remediation**: Converted both to their corresponding semantic tokens: the Failed-count figure → `text-destructive`, the MFA-enabled confirmation → `text-success`. This file is now fully converted — 0 remaining raw-color warnings.

**Risk & impact on existing functionality**: Pure CSS class-name substitution on two elements — no logic, props, or conditional rendering changed. Blast radius: isolated to this one file (`/dashboard/settings`, a large multi-tab settings page covering integrations, email, operations, company, and security — only these two specific spots had raw colors; the rest of the file already used semantic tokens or neutral classes).

**User experience effect**: Internal-admin-only surface. Visually equivalent in both themes.

**Files modified**:
| File | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/settings/page.tsx` | Email-deliverability Failed count + MFA-enabled confirmation → destructive/success tokens | #2816 |

**Before/after snippet**:
```tsx
// before
<p className={`text-lg font-bold ${deliverability.failure_rate > 0.01 ? "text-red-600" : ""}`}>{deliverability.by_status?.failed ?? 0}</p>
...
<div className="flex items-center gap-2 text-sm text-green-600 dark:text-green-400">
    <ShieldCheck className="h-4 w-4" />
    MFA is enabled on your account.
</div>
// after
<p className={`text-lg font-bold ${deliverability.failure_rate > 0.01 ? "text-destructive" : ""}`}>{deliverability.by_status?.failed ?? 0}</p>
...
<div className="flex items-center gap-2 text-sm text-success">
    <ShieldCheck className="h-4 w-4" />
    MFA is enabled on your account.
</div>
```

**Rollback plan**: `git revert` — pure class-name substitution, no data/migration involved.

**Verification performed**:
- `eslint` on the changed file: 0 errors, 4 warnings (all pre-existing unrelated `react/no-unescaped-entities` warnings, no raw-color warnings remaining).
- `vitest run`: 339/339 tests pass across all 35 test files.
- `tsc --noEmit`/`npm run build`: not re-run per-batch — the pre-existing, diff-unrelated `@spinr/shared` Turbopack failure in this environment was already root-caused via `git stash` against unmodified `origin/main` in sub-batch 31's PR (#4371).

**What was NOT verified**: No visual regression tooling exists in this repo (standing gap).
