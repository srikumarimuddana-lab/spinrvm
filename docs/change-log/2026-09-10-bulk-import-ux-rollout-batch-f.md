# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-10 |
| Author | ittalenthire.ca@gmail.com (via Claude Code) |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | (branch `claude/bulk-import-ux-batch-f`, stacked on batch-c / PR #5171) |
| Related issue or gap ID | Batch F of the Bulk Import UX rollout — Stripe Mapping Import (`bulk-operations/page.tsx`) |

## 1. Issue / gap identified

Stripe Mapping Import's error/warning table showed the same flat, unexplained technical messages as every other tool before its own batch — plus this section carries the widest message vocabulary in the whole rollout (local CSV/matching guards, plus a second "live Stripe validation" phase with Stripe-object-derived findings), all previously undocumented anywhere outside the backend source.

## 2. Root cause

Same as prior batches — this section predates the redesign. `NeedsUpdateSection` (the "already mapped — review to update" per-driver confirm flow) is deliberately untouched: it has no per-row error/warning message vocabulary, it's a confirm-and-act UI keyed on `row_ref`/`driver_id`, and it's already narrowly scoped and PII-free by design.

## 3. Fix / remediation

- Added a `suffixes` matching mode to the shared `createIssueExplainer` factory (`lib/bulk-import-error-help.ts`), alongside the existing exact/prefix modes. Three of this service's messages put the dynamic Stripe ID as a *prefix* of the whole message with a fixed, matchable *suffix* (e.g. `f"{acct} is already mapped to another driver"` always ends in the literal string `"is already mapped to another driver"`) — the existing exact/prefix-only matching couldn't express that shape, so extending the factory was the only way to add these without guessing/generalizing the message strings themselves.
- Added `explainStripeMappingIssue`, built strictly from reading `backend/services/stripe_mapping_import_service.py` in full (both `_build_local_driver_plan`/`_build_local_rider_plan`'s local-guard phase and `build_plan`'s live-Stripe-validation phase: `_account_findings`, `_customer_findings`, `_livemode_error`, `_retrieve_stripe`) — every exact/prefix/suffix rule maps to a message string actually present in that file, none invented.
- This is the **third and final** batch to touch `bulk-operations/page.tsx` (after Batch C's Rider Import). Its local `IssueTable` and `Stat` function definitions were used *only* by this Stripe Mapping section (confirmed via grep before editing — no other section referenced either) — both are now **removed**, replaced by the shared `SharedIssueTable`/`StatTile` components already imported under that alias since Batch C. `REPORT_COLUMNS` is kept (still used by the "Download errors" CSV export) and `NeedsUpdateSection` is untouched.
- Added the `WhatThisToolDoes` panel above step 1 of the Stripe Mapping card stack.
- Removed the now-unused `StripeImportReportItem` type import (only consumer was the deleted local `IssueTable`).

## 4. Risk & impact on existing functionality

- **Blast radius**: confirmed via grep before editing that the page's local `Stat`/`IssueTable` had no consumers outside this section (Rider Import already moved to shared components in Batch C; Route Snapshot/Backfill — Batch G, not yet done — use a `<details>`-based list, never `Stat`/`IssueTable`). Removing both function definitions is therefore safe now that their last consumer is redesigned.
- **No behavior change**: validate/commit/discover-by-email/KYC-status-refresh logic is entirely untouched — only the review-and-commit and KYC-status cards' presentation changed.
- **What else reads/writes the same state**: `report.errors`/`report.warnings` (from `adminValidateStripeImport`) and `status.*` (from `adminStripeImportStatus`) are read-only here; nothing in this batch writes to them differently.
- **What could regress**: the CSV export (`exportToCsv("stripe-mapping-errors", report.errors, REPORT_COLUMNS)`) still reads `row_ref`/`field`/`message` off the same `StripeMappingErrorItem`-shaped objects — unaffected, since only the on-screen table changed, not the underlying data.

## 5. User-experience effect

Internal-admin-facing only (super-admin-only page). The Stripe Mapping Import section now shows the same what/why/which-files/value panel as every other redesigned tool, and every error/warning row gets a plain-language cause/fix line where a mapping exists. No information is removed — the raw technical message is still shown above the plain-language explanation.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/lib/bulk-import-error-help.ts` | Added `suffixes` matching mode to `createIssueExplainer`; added `explainStripeMappingIssue` | Handle the dynamic-Stripe-ID-as-prefix/fixed-suffix message shape unique to this tool |
| `admin-dashboard/src/lib/__tests__/bulk-import-error-help.test.ts` | Added tests for the new suffix-matching mode and `explainStripeMappingIssue` | Lock in exact/prefix/suffix matching and cross-tool isolation |
| `admin-dashboard/src/app/dashboard/bulk-operations/page.tsx` | Stripe Mapping section adopts shared components; removed now-dead local `IssueTable`/`Stat` and the now-unused `StripeImportReportItem` import | Batch F rollout; completes the page's migration off its own local components (started in Batch C) |

## 7. Before / after

```tsx
// Before
<Stat label="Errors" value={report.errors.length} tone="error" />
...
<IssueTable items={report.errors} />
```

```tsx
// After
<StatTile label="Errors" value={report.errors.length} tone="error" />
...
<SharedIssueTable
    items={report.errors}
    getRowKey={(it, i) => `${it.row_ref}-${it.field}-${i}`}
    getRef={(it) => it.row_ref}
    getField={(it) => it.field}
    getMessage={(it) => it.message}
    refLabel="Row"
    explain={explainStripeMappingIssue}
/>
```

The factory extension:

```ts
// Before — createIssueExplainer only supported exact + prefix matching
export function createIssueExplainer(
    exact: Record<string, IssueExplanation>,
    prefixes: [string, IssueExplanation][] = [],
): IssueExplainer { ... }
```

```ts
// After — added suffix matching for a dynamic-value-as-prefix message shape
export function createIssueExplainer(
    exact: Record<string, IssueExplanation>,
    prefixes: [string, IssueExplanation][] = [],
    suffixes: [string, IssueExplanation][] = [],
): IssueExplainer { ... }
```

## 8. Rollback plan

`git-revert-safe` — purely additive/presentational for the page; the `createIssueExplainer` factory change is additive (a new optional third parameter, defaulting to `[]`), so every existing caller (SIN/DOB, Vehicle-History, Saved-Address, Wallet, Booking, Legacy Driver, Bulk Driver, Rider) is unaffected and needs no change. Reverting restores the page's own local `Stat`/`IssueTable` definitions and the two `<Stat>`/`<IssueTable>` call sites exactly as they were.

## 9. Verification performed

- [x] Automated tests: `npx vitest run` (full suite, cumulative with A/B/C/D/E/H) → 625 passed (65 files), up from 619 (6 new tests: 2 for the suffix-matching mode, 4 for `explainStripeMappingIssue`).
- [x] **Real production build run**: `npm run build` completed with no errors.
- [x] `npx tsc --noEmit` — no errors.
- [x] `npx eslint` on all three changed files — no warnings.
- [x] Blast-radius grep performed before editing: confirmed the page's local `Stat`/`IssueTable` had exactly one remaining consumer (this section) before deletion.
- [x] Reviewed against `CLAUDE.md` conventions: no PII in any new explanatory string — every message this batch explains is already PII-free by the backend service's own design (`row_ref` is `old_driver_id`/`old_user_id`/a CSV line number, never a phone/email/name; Stripe object IDs and account/business-type/country values are not PII).

## What was NOT verified

- Not manually clicked through in a running admin-dashboard instance (would require a live Stripe test-mode key and seeded driver/rider fixtures).
- `stripe_mapping_import_service.py`'s messages confirmed as of this reading (2026-09-10); a future backend change to any of these strings needs a matching update here.
- No screenshot/visual-regression check — `/dashboard/bulk-operations` is not one of the 6 seeded Playwright pages.

## Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed (grep-confirmed before deleting the local components)
- [x] No silent behavior change to an already-shipped flow
