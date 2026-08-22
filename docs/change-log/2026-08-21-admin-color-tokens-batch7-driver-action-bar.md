# Change Impact & Risk Log — #2816 Batch 7, sub-batch 35: driver-action-bar status map + ban reason

**Issue/gap identified**: `drivers/_components/driver-action-bar.tsx`'s `STATUS_CONFIG` (a 5-state driver-lifecycle categorical map: pending/active/needs_review/suspended/banned) had no documented #2816 exclusion, generating unaddressed lint warnings. Separately, the "banned" reason notice used hardcoded `bg-red-200 dark:bg-red-900/40`/`text-red-800 dark:text-red-400` instead of the `--destructive` token.

**Root cause**: `STATUS_CONFIG` predates the semantic-token system and was reviewed once already (sub-batch 9, earlier in this session) as a "zero-conversion" file, but that pass judged the individual action buttons (which are genuinely decorative/tier-differentiating) without adding the formal documentation block the map itself needed — the same gap `driver-timeline.tsx`'s `EVENT_CONFIG` had in sub-batch 34.

**Fix/remediation**:
- Added the `eslint-disable`/`eslint-enable` documentation block around `STATUS_CONFIG`, explaining why 4 hues are needed for 5 states: `needs_review` (amber) and `suspended` (orange) are deliberately distinct shades so neither reads as the same severity tier as the other or as `banned` (red) — a 3-token system can't hold that 4-way distinction. No color values changed.
- Converted the "banned" reason notice (`<Ban/>Reason: {driver.ban_reason}`) to `bg-destructive/15 text-destructive` — a clean single destructive signal, unambiguous since red already means destructive everywhere else in the app.

Left untouched: the "suspended" reason notice (orange, matching the deliberate suspend/needs_review tier distinction documented above — converting only this one to `--warning` would visually collide it with the amber `needs_review` state); all the per-action buttons (Approve/Reactivate solid emerald-600, Suspend outline orange, Ban outline red, Unban outline emerald) — these use fixed Tailwind shades (not the `--success` token), so converting them to `bg-success` would risk breaking dark-mode contrast per the established finding that dark-mode `--success` is only 2.02:1 against white text; the fixed `emerald-600` avoids that failure mode deliberately.

**Risk & impact on existing functionality**: Blast radius: isolated to this one file. `STATUS_CONFIG` and the ban-reason notice are used only within this component; verified via grep this component isn't duplicated elsewhere. No logic, props, or conditional rendering changed — pure comment addition + one class-name substitution.

**User experience effect**: Internal-admin-only surface (driver detail slideout). The ban-reason notice is visually equivalent in both themes (destructive token resolves to the same red family already used for solid destructive UI elsewhere in the same component, e.g. the Ban confirmation button).

**Files modified**:
| File | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/drivers/_components/driver-action-bar.tsx` | Documented categorical `STATUS_CONFIG`; converted ban-reason notice to destructive tokens | #2816 |

**Before/after snippet**:
```tsx
// before
{status === "banned" && driver.ban_reason && (
    <p className="text-xs mt-2 bg-red-200 dark:bg-red-900/40 rounded-lg px-2.5 py-1.5 text-red-800 dark:text-red-400">
        <Ban className="h-3 w-3 inline mr-1" />Reason: {driver.ban_reason}
    </p>
)}
// after
{status === "banned" && driver.ban_reason && (
    <p className="text-xs mt-2 bg-destructive/15 rounded-lg px-2.5 py-1.5 text-destructive">
        <Ban className="h-3 w-3 inline mr-1" />Reason: {driver.ban_reason}
    </p>
)}
```

**Rollback plan**: `git revert` — a comment addition plus one class-name substitution, no data/migration involved.

**Verification performed**:
- `eslint` on the changed file: 0 errors, 26 warnings (all pre-existing residual raw-color warnings on the deliberately-untouched action buttons and the suspended-reason notice, discussed above).
- `vitest run`: 339/339 tests pass across all 35 test files.
- `tsc --noEmit`/`npm run build`: not re-run per-batch — the pre-existing, diff-unrelated `@spinr/shared` Turbopack failure in this environment was already root-caused via `git stash` against unmodified `origin/main` in sub-batch 31's PR (#4371).

**What was NOT verified**: No visual regression tooling exists in this repo (standing gap) — the ban-reason notice's visual equivalence was reasoned about (same token used identically elsewhere in this component), not screenshotted.
