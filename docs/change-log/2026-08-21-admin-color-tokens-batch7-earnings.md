# Change Impact & Risk Log — #2816 Batch 7, sub-batch 36: earnings CEO/ops metrics (partial)

**Issue/gap identified**: `earnings/page.tsx` (a large ~1900-line file covering ride earnings, Spinr Pass, and payouts) used hardcoded Tailwind color utilities for genuine single-signal metric accents throughout its CEO-header and operational-queue sections: revenue/refund/promo/surge KPI cards, ride-funnel outcome cards, payout health cards (stuck/blocked), the at-risk-drivers failure count, the T4A "≥$30k mandatory" bucket row, and the delta-chip (up/down trend indicator).

**Root cause**: Predates the semantic-token system introduced for #2816. This is a large, high-traffic file (CEO business overview + payout operations), so #2816 coverage is being done incrementally.

**Fix/remediation**: Converted the following genuine positive/negative/caution signals to `--success`/`--warning`/`--destructive` tokens:
- `DeltaChip` up/down trend colors.
- CEO header: Net Revenue (success), Refunds (destructive), Promo Spend (destructive), Surge Revenue (warning).
- Ride funnel: Travelled (success), Rider Cancelled (warning), Driver Cancelled (destructive), Cancelled After Start (destructive).
- Payouts header: Outstanding to drivers (warning), Paid out (success), Failed (destructive), Success rate (success).
- Ops queues: Stuck>48h card border/count (warning), Blocked-by-Stripe card border/count (destructive), at-risk-driver failure count (destructive).
- T4A compliance: the "≥$30k — GST/HST registration MANDATORY" row (warning).

This is a **partial** pass — a repo-scan at the start of this sub-batch found 55 raw-color matches in this file; the remaining ~44 (verified via post-fix `eslint`) include the `CancellationMixBar` chart legend/segments (rider/driver/system — a categorical actor-differentiation, not a severity signal, left untouched per the established multi-category-differentiation exclusion), the solid-fill white-text "Close" period button (emerald, left per the dark-mode `--success` contrast-risk exclusion), decorative tab/icon colors, and raw hex/HSL values passed directly to `recharts` `fill`/`stroke` props (out of Tailwind-class migration scope). The file's second half (rows 1059–1890, the payout-detail table and Spinr Pass revenue tab) was not reviewed in this sub-batch and is deferred to a follow-up.

**Risk & impact on existing functionality**: Pure CSS class-name substitution across ~14 isolated JSX attributes — no logic, props, or conditional rendering changed. `--success`/`--warning`/`--destructive` are pre-existing tokens already used elsewhere in this same file (e.g. the "Why payouts are failing" and "At-risk drivers" card-title icons already used `text-destructive`/`text-warning`). Blast radius: isolated to `earnings/page.tsx`; no shared component/hook touched.

**User experience effect**: Internal-admin-only surface (`/dashboard/earnings`, CEO/finance-facing). Visually equivalent in both themes — same hue family as before, now theme-aware via tokens instead of hardcoded light/dark class pairs.

**Files modified**:
| File | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/earnings/page.tsx` | DeltaChip, CEO-header KPIs, ride-funnel outcomes, payout health cards, at-risk-driver count, T4A mandatory-bucket row → success/warning/destructive tokens | #2816 |

**Before/after snippet**:
```tsx
// before
<MetricCard icon={Wallet} label="Net Revenue" metric={m?.net_revenue} format={fmtMoney} accent="text-emerald-600 dark:text-emerald-400" loading={loading} />
// after
<MetricCard icon={Wallet} label="Net Revenue" metric={m?.net_revenue} format={fmtMoney} accent="text-success" loading={loading} />
```

**Rollback plan**: `git revert` — pure class-name substitution, no data/migration involved.

**Verification performed**:
- `eslint` on the changed file: 0 errors, 44 warnings (down from 55; remaining are the categorical/decorative/raw-chart-fill exclusions discussed above, plus the untouched second half of the file).
- `vitest run`: 339/339 tests pass across all 35 test files.
- `tsc --noEmit`/`npm run build`: not re-run per-batch — the pre-existing, diff-unrelated `@spinr/shared` Turbopack failure in this environment was already root-caused via `git stash` against unmodified `origin/main` in sub-batch 31's PR (#4371).

**What was NOT verified**: No visual regression tooling exists in this repo (standing gap). This file's second half (~800 lines, payout-detail table + Spinr Pass revenue tab) was not reviewed in this sub-batch and is flagged for follow-up rather than rushed.
