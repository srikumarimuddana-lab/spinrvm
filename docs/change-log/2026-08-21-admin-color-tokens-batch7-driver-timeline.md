# Change Impact & Risk Log — #2816 Batch 7, sub-batch 34: driver-timeline event map documentation

**Issue/gap identified**: `drivers/_components/driver-timeline.tsx`'s `EVENT_CONFIG` (a 19-entry categorical map of driver-activity-event types → icon/color/background/border-pipe-color) used hardcoded Tailwind color utilities without a documented #2816 exclusion comment, so `eslint` flagged all 19 entries as unaddressed raw-color warnings.

**Root cause**: The map predates the #2816 lint rule; unlike `audit-logs/page.tsx`'s analogous `ACTION_CONFIG` (already documented in an earlier pass), this one was never given the `eslint-disable`/`eslint-enable` documentation block explaining why it's a legitimate categorical exclusion rather than an unconverted signal.

**Fix/remediation**: Added the same documentation pattern already established for `audit-logs/page.tsx`'s `ACTION_CONFIG` and `lib/utils.ts`'s `statusColor()`: a comment explaining that this is a categorical event-type map (19 distinct types across 8 hues — registered, document uploaded/approved/rejected, approve/verify/reject, suspend/ban/unban/reactivate, status override, profile/vehicle updated, went online/offline, note added, subscription started/cancelled, ride completed) that a 3-token semantic system cannot express, wrapped in `/* eslint-disable no-restricted-syntax -- ... (#2816) */` / `/* eslint-enable no-restricted-syntax */`. No color values were changed — this is a documentation-only fix, consistent with the established convention that a genuine categorical map gets documented, not forced into 3 tokens it doesn't fit.

Also verified: the `meta.new_status` ternary further down in the same file (rendering old→new status-change badges) already uses `--success`/`--warning`/`--destructive` tokens correctly from an earlier pass — no change needed there.

**Risk & impact on existing functionality**: Zero behavior change — no class value was altered, only a comment block added around an existing object literal. Blast radius: isolated to this one file; `EVENT_CONFIG` is not exported or imported elsewhere (verified via grep — it's a module-local `const` used only within this component's render).

**User experience effect**: None. Purely a lint-suppression/documentation change; the rendered timeline is pixel-identical before and after.

**Files modified**:
| File | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/drivers/_components/driver-timeline.tsx` | Added `eslint-disable`/`eslint-enable` documentation block around the categorical `EVENT_CONFIG` map | #2816 |

**Before/after snippet**:
```tsx
// before
const EVENT_CONFIG: Record<string, { icon: any; color: string; bg: string; pipeColor: string }> = {
    registered: { icon: UserPlus, color: "text-blue-600", bg: "bg-blue-100 dark:bg-blue-900/30", pipeColor: "border-blue-300" },
    ...
};
// after
// Categorical driver-activity-event-type map (19 distinct event types across
// 8 hues) — not a #2816 migration target. ...
/* eslint-disable no-restricted-syntax -- categorical driver-activity-event-type map, see comment above (#2816) */
const EVENT_CONFIG: Record<string, { icon: any; color: string; bg: string; pipeColor: string }> = {
    registered: { icon: UserPlus, color: "text-blue-600", bg: "bg-blue-100 dark:bg-blue-900/30", pipeColor: "border-blue-300" },
    ...
};
const DEFAULT_CONFIG = { ... };
/* eslint-enable no-restricted-syntax */
```

**Rollback plan**: `git revert` — comment-only change, no functional or data impact.

**Verification performed**:
- `eslint` on the changed file: 0 errors, 0 raw-color warnings (down from 19 before this fix); 2 unrelated pre-existing warnings remain (`react-hooks/set-state-in-effect`, `react-hooks/exhaustive-deps`) — out of scope for this #2816 fix.
- `vitest run`: 339/339 tests pass across all 35 test files.
- `tsc --noEmit`/`npm run build`: not re-run per-batch — the pre-existing, diff-unrelated `@spinr/shared` Turbopack failure in this environment was already root-caused via `git stash` against unmodified `origin/main` in sub-batch 31's PR (#4371).

**What was NOT verified**: No visual regression tooling exists in this repo (standing gap) — moot here since no visual output changed (comment-only diff), but stated per convention.
