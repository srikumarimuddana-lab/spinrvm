# Change Impact & Risk Log — #2816 Batch 7, sub-batch 45: zoho-config-card connection status

**Issue/gap identified**: `support-tickets/_components/zoho-config-card.tsx`'s Connected/Not-connected badge used hardcoded `bg-emerald-100 text-emerald-800`/`bg-amber-100 text-amber-800` instead of `--success`/`--warning` tokens; three "(saved)" secret-field confirmations used `text-emerald-600 dark:text-emerald-400` instead of `--success`.

**Root cause**: Predates the semantic-token system introduced for #2816.

**Fix/remediation**: Converted the Connected/Not-connected badge to `bg-success/15 text-success`/`bg-warning/15 text-warning`, and all three "(saved)" confirmation spans to `text-success`.

Left untouched: the email-signature preview box's `bg-white dark:bg-zinc-950` — deliberate fixed light/dark chrome so the preview renders the signature HTML against a neutral background regardless of the admin dashboard's theme, consistent with other fixed-chrome exclusions (photo-viewer/lightbox backdrops) established in prior sub-batches.

**Risk & impact on existing functionality**: Pure CSS class-name substitution — no logic, props, or conditional rendering changed. Blast radius: isolated to this one component (used only on the support-tickets settings page).

**User experience effect**: Internal-admin-only surface (support-tickets Zoho Desk connection settings). Visually equivalent in both themes.

**Files modified**:
| File | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/support-tickets/_components/zoho-config-card.tsx` | Connected/Not-connected badge + 3× "(saved)" confirmations → success/warning tokens | #2816 |

**Before/after snippet**:
```tsx
// before
{status?.connected ? (
    <Badge className="ml-2 bg-emerald-100 text-emerald-800 hover:bg-emerald-100">
        <CheckCircle2 className="mr-1 h-3 w-3" /> Connected
    </Badge>
) : (
    <Badge variant="secondary" className="ml-2 bg-amber-100 text-amber-800 hover:bg-amber-100">
        <XCircle className="mr-1 h-3 w-3" /> Not connected
    </Badge>
)}
// after
{status?.connected ? (
    <Badge className="ml-2 bg-success/15 text-success hover:bg-success/15">
        <CheckCircle2 className="mr-1 h-3 w-3" /> Connected
    </Badge>
) : (
    <Badge variant="secondary" className="ml-2 bg-warning/15 text-warning hover:bg-warning/15">
        <XCircle className="mr-1 h-3 w-3" /> Not connected
    </Badge>
)}
```

**Rollback plan**: `git revert` — pure class-name substitution, no data/migration involved.

**Verification performed**:
- `eslint` on the changed file: 0 errors, 2 warnings (1 pre-existing on the deliberately-untouched signature-preview background chrome, 1 unrelated `react-hooks/set-state-in-effect`).
- `vitest run`: 339/339 tests pass across all 35 test files.
- `tsc --noEmit`/`npm run build`: not re-run per-batch — the pre-existing, diff-unrelated `@spinr/shared` Turbopack failure in this environment was already root-caused via `git stash` against unmodified `origin/main` in sub-batch 31's PR (#4371).

**What was NOT verified**: No visual regression tooling exists in this repo (standing gap).
