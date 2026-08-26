# Change Impact & Risk Log — #2816 Batch 7, sub-batch 37: driver approval queue SLA/missing-docs signals

**Issue/gap identified**: `drivers/queue/page.tsx`'s `slaTone()` 3-tier time-in-queue urgency ladder, the "needs re-review" status badge, the missing/complete-docs indicators, and the photo-reject button used hardcoded Tailwind color utilities instead of `--success`/`--warning`/`--destructive` tokens.

**Root cause**: Predates the semantic-token system introduced for #2816.

**Fix/remediation**: Converted:
- `slaTone()` (time-in-queue urgency ladder: ≥24h destructive, ≥4h warning, else success) — same "urgency ladder" pattern already converted in `drivers/expiring/page.tsx` (sub-batch 27).
- Status badge's non-"pending" branch (resubmitted/needs-review) → `--warning` tokens (the "pending" branch stays blue-informational, unchanged — it's a neutral "waiting", not a signal).
- Missing-docs count (destructive when >0) and zero-missing confirmation (success) → tokens.
- Photo-reject outline button → destructive tokens.

Also verified (no edit needed): `monitoring/driver-panel.tsx` — already fully converted from an earlier pass; its 3 remaining raw-color matches (solid green "Online" badge using a fixed `green-500` shade rather than the `--success` token, the fixed star-rating amber convention, and a decorative blue "Current Ride" navigation link) are all established exclusions consistent with prior sub-batches.

Left untouched in `drivers/queue/page.tsx`: the solid-fill white-text photo-approve button (`bg-emerald-600` — fixed shade, not the `--success` token, so it doesn't carry the known dark-mode `--success`-vs-white-text contrast risk, but converting it to the token would introduce that risk, so it stays as-is), the violet "photo pending" badge (categorical, distinct from status), and the blue "pending" status badge (informational, not a signal).

**Risk & impact on existing functionality**: Pure CSS class-name substitution — no logic, props, or conditional rendering changed. Blast radius: isolated to `drivers/queue/page.tsx`; `slaTone()` is a module-local function used only within this file (verified via grep — not exported/imported elsewhere).

**User experience effect**: Internal-admin-only surface (`/dashboard/drivers/queue`). Visually equivalent in both themes.

**Files modified**:
| File | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/drivers/queue/page.tsx` | `slaTone()` ladder, status badge's non-pending branch, missing/complete-docs indicators, photo-reject button → success/warning/destructive tokens | #2816 |

**Before/after snippet**:
```tsx
// before
const slaTone = (seconds: number) => {
    if (seconds >= 86400) return "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-300 border-red-200 dark:border-red-800";
    if (seconds >= 14400) return "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300 border-amber-200 dark:border-amber-800";
    return "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300 border-emerald-200 dark:border-emerald-800";
};
// after
const slaTone = (seconds: number) => {
    if (seconds >= 86400) return "bg-destructive/15 text-destructive border-destructive/30";
    if (seconds >= 14400) return "bg-warning/15 text-warning border-warning/30";
    return "bg-success/15 text-success border-success/30";
};
```

**Rollback plan**: `git revert` — pure class-name substitution, no data/migration involved.

**Verification performed**:
- `eslint` on both files: 0 errors, 8 warnings total (all pre-existing residual raw-color warnings on the deliberately-untouched buttons/badges discussed above).
- `vitest run`: 339/339 tests pass across all 35 test files.
- `tsc --noEmit`/`npm run build`: not re-run per-batch — the pre-existing, diff-unrelated `@spinr/shared` Turbopack failure in this environment was already root-caused via `git stash` against unmodified `origin/main` in sub-batch 31's PR (#4371).

**What was NOT verified**: No visual regression tooling exists in this repo (standing gap).
