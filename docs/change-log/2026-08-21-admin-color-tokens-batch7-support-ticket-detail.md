# Change Impact & Risk Log — #2816 Batch 7, sub-batch 44: support-ticket-detail service-area warning

**Issue/gap identified**: `support-tickets/tickets/[id]/page.tsx`'s "needs service-area assignment" card (border/background + notice text) used hardcoded `border-amber-400 bg-amber-50/50 dark:bg-amber-950/20` and `text-amber-700 dark:text-amber-400` instead of the `--warning` token.

**Root cause**: Predates the semantic-token system introduced for #2816. This file's `statusClass()` function was already partially converted in an earlier pass (hold/escalated/closed use tokens; "open" carries a documented categorical exclusion), but the separate service-area warning card was missed.

**Fix/remediation**: Converted the service-area card's conditional border/background and its warning notice text to `--warning` tokens.

Left untouched (established exclusions): the message-thread accent colors (comment=amber, agent-reply=blue, customer=slate — a categorical message-role differentiation, not a severity signal), the "Show full message" toggle link and email-signature link colors (decorative navigation/link styling, not signals).

**Risk & impact on existing functionality**: Pure CSS class-name substitution on one conditional `Card` className and one `<p>` — no logic, props, or conditional rendering changed. Blast radius: isolated to this file.

**User experience effect**: Internal-admin-only surface (`/dashboard/support-tickets/tickets/[id]`). Visually equivalent in both themes.

**Files modified**:
| File | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/support-tickets/tickets/[id]/page.tsx` | Service-area "needs assignment" card + notice text → warning tokens | #2816 |

**Before/after snippet**:
```tsx
// before
<Card className={areaInfo?.needs_assignment ? "border-amber-400 bg-amber-50/50 dark:bg-amber-950/20" : undefined}>
...
<p className="flex items-start gap-1.5 text-xs text-amber-700 dark:text-amber-400">
// after
<Card className={areaInfo?.needs_assignment ? "border-warning/40 bg-warning/5" : undefined}>
...
<p className="flex items-start gap-1.5 text-xs text-warning">
```

**Rollback plan**: `git revert` — pure class-name substitution, no data/migration involved.

**Verification performed**:
- `eslint` on the changed file: 0 errors, 4 warnings (all pre-existing on the deliberately-untouched message-thread accents and link colors, plus one unrelated `react-hooks/set-state-in-effect`).
- `vitest run`: 339/339 tests pass across all 35 test files.
- `tsc --noEmit`/`npm run build`: not re-run per-batch — the pre-existing, diff-unrelated `@spinr/shared` Turbopack failure in this environment was already root-caused via `git stash` against unmodified `origin/main` in sub-batch 31's PR (#4371).

**What was NOT verified**: No visual regression tooling exists in this repo (standing gap).
