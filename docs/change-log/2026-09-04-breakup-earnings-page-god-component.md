# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-04 |
| Author | Claude Code (background agent), on behalf of vikas@ngitservices.com |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | `refactor(admin-dashboard): decompose earnings/page.tsx god-component (design-audit follow-up)` — see PR |
| Related issue or gap ID | `/design Spinr Apps` design/UX audit finding: "Break up the god-components … `earnings/page.tsx` 1,915" |

## 1. Issue / gap identified

`admin-dashboard/src/app/dashboard/earnings/page.tsx` was a 1,915-line "god component" — five distinct UI sections (business-overview KPIs, payout-flow KPIs, payout operational queues, T4A/compliance, three full tabs) all defined in one file. This is a design-drift risk: no natural component boundary means every future tweak touches the same giant file, increasing the odds of an accidental cross-section change.

## 2. Root cause

Organic growth: the page was built by repeatedly appending new sections/tabs (ride earnings → Spinr Pass revenue → payouts → payout compliance) directly into the top-level page file instead of following the `rides/_components/*` decomposition pattern already established elsewhere in this codebase.

## 3. Fix / remediation

Pure code-motion refactor, zero behavior change. Extracted the monolith into 8 new files under `admin-dashboard/src/app/dashboard/earnings/_components/`, following the exact conventions already used by `rides/_components/*` (named exports, `"use client"` at top of files with hooks/JSX, prop-drilled state — no state lifted or pushed, no context introduced). `page.tsx` is now an 83-line router/shell: tab state + tab-bar JSX only, identical to its pre-refactor top-level JSX byte-for-byte except for import paths.

Done as 8 separate commits, one extraction per commit (≤3 files each per `CLAUDE.md` task-decomposition rule), with `npx tsc --noEmit` run clean after every single commit before proceeding to the next — see commit list below.

No calculation, formatting, prop name, handler signature, className, or conditional-render branch was changed. No styling, no bug fixes, no renames.

## 4. Risk & impact on existing functionality

**Blast-radius check performed** (grep, whole repo, before touching anything):
- `grep -r` for any import of `dashboard/earnings/page.tsx` (or a relative-path equivalent) across the repo → only doc/audit markdown files reference it by path in prose, plus one test: `admin-dashboard/src/__tests__/dashboard/pages.smoke.test.tsx`, which does `const { default: Page } = await import("@/app/dashboard/earnings/page")` and asserts it renders without throwing. It only touches the **default export** — unaffected by moving internals into sub-components. Ran it explicitly post-refactor; still passes.
- Checked the two sibling route files that live under the same `earnings/` directory tree — `admin-dashboard/src/app/dashboard/earnings/payouts/page.tsx` and `admin-dashboard/src/app/dashboard/earnings/payouts/[id]/page.tsx`. Both are separate Next.js routes with their own local `Payout` interface and their own imports from `@/lib/api` / `@/lib/utils` / shared UI kit only — **neither imports anything from `dashboard/earnings/page.tsx`**, so no shared piece needed to move to a location both could import from. This refactor's new `_components/` directory is scoped entirely under `dashboard/earnings/`, not shared with those two routes.
- Isolated, single-surface (admin-dashboard only), zero cross-surface impact. No backend, no other admin route, no rider/driver app touched.
- No background loop, ride-state-machine, or wallet-delta interaction — this page only *displays* data already computed and returned by existing `/admin/earnings/*` and `/admin/payouts/*` endpoints (see §"Money arithmetic" below).

**Money arithmetic — explicitly addressed per the task's mandatory check:**
- Read every line of the original 1,915-line file before touching anything. Confirmed: **no Decimal/fare/payout calculation logic lives in this file.** Every number rendered is either (a) a value returned directly by the backend (`overview.metrics.*`, `payout.amount`, `stats.total_revenue`, etc.) or (b) a **display-only** transform of already-computed values:
  - `formatCurrency()`, `Intl`-style helpers in `_components/earnings-format.ts` (`fmtMoney`, `fmtCount`, `fmtPct`, `fmtHours`, `fmtPeriodKey`) — pure string formatting, no arithmetic on money magnitudes beyond what was already there (`fmtPct` does `n.toFixed(2)` on an already-computed percentage from the API; `fmtHours` divides an already-computed hours value for unit conversion display, e.g. hours→days).
  - `RideEarningsTab`'s `totals` `Array.reduce` (moved verbatim into `_components/ride-earnings-tab.tsx`) sums four already-computed per-ride fields (`total_fare`, `driver_earnings`, `admin_earnings`, `tip_amount`) from the ride list the API already returned, for a client-side custom-date-range subtotal display. This is a display aggregate over API-provided numbers, not a fare/payout calculation — no rate, tax, or surge math. Moved character-for-character, not touched.
  - `PayoutsTab`'s `${Number(p.amount || 0).toFixed(2)}` inline display (moved verbatim into `_components/payouts-tab.tsx`) — `Number.toFixed` string formatting of an already-computed payout amount, not a calculation.
  - No `Decimal`/`_d()`/`_round()`/`_f()` equivalents were expected or found in this frontend file (those are backend conventions); nothing here computes a fare, surge multiplier, tax line, or driver payout — it only renders numbers the backend already settled. **This did not need `spinr-money-auditor` review** on that basis, but is flagged here explicitly per the task instruction rather than silently assumed.

**What was noticed but deliberately left untouched** (per instruction — flag, don't fix):
- `page.tsx` retained two **pre-existing** dead imports (`getEarnings` from `@/lib/api`, `statusColor` from `@/lib/utils`) that were unused *before* this refactor started (confirmed by reading the original file and grepping for their call sites — zero, in both cases). Per the "surgical changes" rule, only imports orphaned *by this diff* were removed; these two were not touched, so the page still imports two objectively unused symbols exactly as it did pre-refactor. Not a new issue, not fixed here.
- The nine `setState`-synchronously-in-`useEffect` and one `react-hooks/exhaustive-deps` ESLint warnings surfaced by `npx eslint` on the (now-split) files are **all pre-existing** in the original code (same effect bodies, just relocated) — confirmed by re-running lint before/after each extraction and diffing the warning set; no new lint warnings were introduced by this refactor, and none were fixed (out of scope for a pure code-motion PR).
- `docs/change-log/2026-08-21-admin-color-tokens-batch5-earnings.md` and related batch-7 entries reference this page by line-based context in their own historical PRs; those are past, applied changes and not re-verified here — noted only so a reviewer doesn't mistake the doc hits from the blast-radius grep for a live dependency.

## 5. User-experience effect

None intended or expected — this is a pure internal code-motion refactor. No JSX branch, prop, className, aria attribute, or number-formatting call was changed; the rendered DOM should be byte-identical for every prior render path. Internal-admin-facing page (`/dashboard/earnings`), not visible to riders, drivers, or corporate admins in any form. Not visible mid-session to anyone in a different sense than before — no session state was touched.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/earnings/page.tsx` | Reduced from 1,915 → 83 lines. All extracted code removed; imports pointed at new `_components/*` files; orphaned imports (only those newly orphaned by each extraction) removed. | God-component decomposition |
| `admin-dashboard/src/app/dashboard/earnings/_components/earnings-format.ts` (new) | `PERIOD_OPTIONS`, `fmtMoney`, `fmtCount`, `fmtPct`, `fmtHours`, `fmtPeriodKey` — moved verbatim | Shared formatting helpers used by multiple extracted components |
| `admin-dashboard/src/app/dashboard/earnings/_components/metric-card.tsx` (new) | `MetricCard`, `DeltaChip` — moved verbatim | Shared leaf presentational components (no state) used by both CEO headers |
| `admin-dashboard/src/app/dashboard/earnings/_components/ceo-metrics-header.tsx` (new) | `CeoMetricsHeader` + private `CancellationMixBar` — moved verbatim | Ride-earnings business-overview KPI section |
| `admin-dashboard/src/app/dashboard/earnings/_components/payouts-ceo-header.tsx` (new) | `PayoutsCeoHeader` + private `PayoutsOpsQueues` — moved verbatim | Payout-flow KPI + operational-queues section |
| `admin-dashboard/src/app/dashboard/earnings/_components/payouts-compliance.tsx` (new) | `PayoutsCompliance` — moved verbatim | T4A snapshot + period-close section |
| `admin-dashboard/src/app/dashboard/earnings/_components/ride-earnings-tab.tsx` (new) | `RideEarningsTab` — moved verbatim | Ride Earnings tab body |
| `admin-dashboard/src/app/dashboard/earnings/_components/spinr-pass-revenue-tab.tsx` (new) | `SpinrPassRevenueTab` — moved verbatim | Spinr Pass Revenue tab body |
| `admin-dashboard/src/app/dashboard/earnings/_components/payouts-tab.tsx` (new) | `PayoutsTab` — moved verbatim | Payouts tab body |

## 7. Before / after

Not applicable in the usual sense — no line of application logic changed value. As a concrete illustration of the code-motion, `MetricCard` before (inline in `page.tsx`) and after (`_components/metric-card.tsx`) are identical function bodies; only the wrapping changed:

```tsx
// Before — inline in page.tsx, not exported
function MetricCard({ icon: Icon, label, metric, format, accent, loading }: {...}) {
    return ( /* ...unchanged JSX... */ );
}
```

```tsx
// After — _components/metric-card.tsx, named export, same body
export function MetricCard({ icon: Icon, label, metric, format, accent, loading }: {...}) {
    return ( /* ...unchanged JSX... */ );
}
```

## 8. Rollback plan

`git revert` of the 8 extraction commits (or the whole PR merge commit) is a complete, sufficient rollback — this touches only static frontend source files with no data-layer, migration, or Stripe/wallet side effects. No feature flag needed: nothing here is user-visible or gated behind a flag, and no live data was written or read differently by this change (same API calls, same request payloads, same response consumption). A `git revert` is fully adequate here — unlike money/ride-state changes, there is no live-data drift to reconcile after reverting pure component code-motion.

## 9. Verification performed

- [x] `npx tsc --noEmit` run **after every one of the 8 extraction commits**, clean each time, before proceeding to the next extraction (per task instruction) — not just once at the end.
- [x] `npx eslint` run on every touched file after each extraction to catch newly-orphaned imports/unused vars beyond what `tsc` (which doesn't flag unused imports in this repo's `tsconfig.json` — `noUnusedLocals` is not set) would catch. Confirmed zero new lint errors introduced at every step; only pre-existing warnings remained (see §4 "noticed but not touched").
- [x] **Real production build**: `npm run build` (Next.js `next build`) run once after all 8 extractions — succeeded (`✓ Compiled successfully`, `Finished TypeScript`), with the identical 78-route list as before, including `/dashboard/earnings`, `/dashboard/earnings/payouts`, `/dashboard/earnings/payouts/[id]`. This is the real production build CLAUDE.md requires, not just `tsc --noEmit` or a dev server.
- [x] **Full test suite**: `npm run test` (vitest — confirmed via `package.json`'s `"test": "vitest run"` script, not `npx jest` which fails in this repo per project convention) — **59 test files, 562 tests, all passed**, including the `/dashboard/earnings` page-render smoke test in `pages.smoke.test.tsx`, re-run explicitly in isolation as an extra check.
- [x] Blast-radius grep performed and documented in §4: repo-wide search for imports of `dashboard/earnings/page.tsx`, plus explicit check of the two sibling `payouts/` route files for any shared component/helper dependency.
- [x] Reviewed against the relevant `CLAUDE.md` conventions: task decomposition (≤3 files/commit, one extraction per commit), surgical changes (only newly-orphaned imports removed, nothing else touched), Change Impact & Risk Log (this document).
- [ ] Feature-flagged — **not applicable**: pure internal code-motion with no user-visible behavior change; flagging a no-op refactor would add complexity with no corresponding risk to gate.

### What was NOT verified

- **No live/staging manual click-through was performed** — this was a background agent session with no browser or running dev server against live Supabase; verification relied on `tsc`, `eslint`, the real production build, and the full automated test suite (including the page-level smoke test), not a human or automated visual pass through the actual rendered UI.
- **Visual regression: `earnings` is NOT one of admin-dashboard's currently-seeded Playwright visual-regression baselines** (the seeded set is `login`, `dashboard-home`, `dashboard-drivers`, `dashboard-monitoring`, `dashboard-settings`, and — as of the most recent CLAUDE.md update — `dashboard-rides`; `dashboard-earnings` is not among them). This means there is **zero automated visual coverage for this page either way** — this refactor neither gained nor lost visual-regression protection, and that absence is not new risk introduced by this PR, but it does mean a rendering regression subtle enough to not throw (e.g. a swapped prop that still type-checks) would not be caught by CI. Per CLAUDE.md gate #6, stating this explicitly rather than relying on "no visible diff" reasoning.
- Because there is no visual tooling for this page, the "does it look the same" question was answered by **reading the moved JSX before and after extraction side-by-side** (confirming identical className strings, identical conditional branches, identical prop wiring) — not by taking or comparing a live screenshot. This is reasoning about the code, not visual verification, and is called out per the task's explicit instruction to distinguish the two.
- Real Supabase / live API responses were not exercised — the `npm run test` suite mocks `@/lib/api` calls (per this repo's existing test setup, `getServiceAreas`/`getPayouts`/etc. are stubbed), so behavior under real backend data shapes was not independently re-verified beyond what the pre-existing test suite already covered before this refactor.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (`git revert`, no data-layer side effects — see §8).
- [x] Blast radius is stated, not assumed (§4: isolated to admin-dashboard, one consuming test file re-verified, two sibling routes confirmed independent).
- [x] No silent behavior change to an already-shipped flow — this document exists specifically because CLAUDE.md requires it even for a fix/refactor with no intended UX change; §5 states explicitly there is none.
