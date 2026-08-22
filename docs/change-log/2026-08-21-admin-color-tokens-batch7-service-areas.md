# Change Impact & Risk Log — #2816 Batch 7, sub-batch 33: service-areas signal conversions

**Issue/gap identified**: `service-areas/page.tsx` (a large ~2900-line file) used hardcoded Tailwind color utilities for several genuine success/warning signals: the surge-above-cap justification warning box, the "Required" document badge, the Spinr Pass enabled/disabled kill-switch panel, the driver subscription "active" status badge, and an incentive-toggle hover state.

**Root cause**: Predates the semantic-token system introduced for #2816. This is a large, frequently-touched file (surge config, Spinr Pass, subscriptions, incentives, airport zones all live here), so #2816 coverage here is being done incrementally across sub-batches rather than in one pass.

**Fix/remediation**: Converted five genuine single-signal instances:
- Surge-above-2.5×-cap justification warning box (border + background) → `border-warning/30`/`bg-warning/10` (its text was already `text-warning` from an earlier pass).
- Document "Required" badge → `bg-success/15 text-success`.
- Spinr Pass enabled/disabled kill-switch panel background/border → `bg-success/10 border-success/30` when enabled.
- Driver subscription "active" status badge → `bg-success/15 text-success`.
- Incentive-toggle active-state hover background → `hover:bg-success/10` (text was already `text-success`).

This is a partial pass on this file — 62 raw-color matches were found repo-scan-wide at the start of this sub-batch; the remainder (blue informational/airport-zone theme, violet sub-region-count badges, decorative icon colors, the "Saved!" solid-fill white-text confirmation buttons — left per the established dark-mode `--success` contrast-risk exclusion, and the categorical incentive-type badge map) were reviewed and are either decorative/categorical exclusions or deferred to a future sub-batch given the file's size.

**Risk & impact on existing functionality**: Pure CSS class-name substitution on 5 isolated `<span>`/`<div>` elements — no logic, props, or conditional rendering changed. `--success`/`--warning` are pre-existing tokens already used elsewhere in this same file (e.g. the geofence-boundary-points confirmation text, the surge-enabled toggle icons), so no new tokens introduced. Blast radius: isolated to `service-areas/page.tsx`; no shared component/hook touched.

**User experience effect**: Internal-admin-only surface (`/dashboard/service-areas`). Visually equivalent in both themes.

**Files modified**:
| File | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/service-areas/page.tsx` | Surge-justification warning box, Required badge, Spinr Pass kill-switch panel, subscription-active badge, incentive-toggle hover → warning/success tokens | #2816 |

**Before/after snippet**:
```tsx
// before
<div className={`... ${enabled ? 'bg-green-50 border border-green-200 dark:bg-green-900/20 dark:border-green-800' : 'bg-muted border border-border'}`}>
// after
<div className={`... ${enabled ? 'bg-success/10 border border-success/30' : 'bg-muted border border-border'}`}>
```

**Rollback plan**: `git revert` — pure class-name substitution, no data/migration involved.

**Verification performed**:
- `eslint` on the changed file: 0 errors, 121 warnings (all pre-existing residual raw-color warnings on the ~57 remaining matches this sub-batch did not touch — a large file with substantial remaining #2816 work for future sub-batches).
- `vitest run`: 339/339 tests pass across all 35 test files.
- `tsc --noEmit`/`npm run build`: not re-run per-batch — the pre-existing, diff-unrelated `@spinr/shared` Turbopack failure in this environment was already root-caused via `git stash` against unmodified `origin/main` in sub-batch 31's PR (#4371).

**What was NOT verified**: No visual regression tooling exists in this repo (standing gap). This file's remaining ~57 raw-color occurrences (blue/violet decorative theming, categorical incentive-type map, the "Saved!" confirmation buttons) were reviewed but not converted in this sub-batch — flagged for follow-up rather than rushed in a single pass given the file's size.
