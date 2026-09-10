# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-10 |
| Author | ittalenthire.ca@gmail.com (via Claude Code) |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | (branch `claude/bulk-import-ux-batch-b`, stacked on `claude/bulk-import-ux-batch-a` / PR #5163) |
| Related issue or gap ID | Batch B of the Bulk Import UX rollout — Legacy Wallet-Balance Import and Legacy Booking Import |

## 1. Issue / gap identified

Same class of gap as Batches A and the Tax-ID pilot: `LegacyWalletImport.tsx` and `LegacyBookingImport.tsx` had only a short technical description and a flat, unexplained `row_num`/`booking_or_wallet_id`/`field`/`message` error table.

## 2. Root cause

Same as prior batches — these tools predate the plain-language redesign.

## 3. Fix / remediation

- Extended the shared `IssueTable` component (`components/bulk-import/issue-table.tsx`) with an optional `extraColumn` prop — both Wallet Import (`old_id`, the legacy wallet entry id) and Booking Import (`booking_code`) have a second identifier column between the row number and the field, which the Batch A tools didn't need.
- Added two new tool-specific explainer maps to `lib/bulk-import-error-help.ts`: `explainWalletImportIssue` and `explainBookingImportIssue`, built from actually reading `services/wallet_import_service.py` and `services/booking_import_service.py`'s current message strings.
- Wired `WhatThisToolDoes`, `StatTile` (plus a small local `MoneyStatTile` for the dollar-formatted tiles each of these two tools already had), and the extended `IssueTable` into both components, folding each tool's existing safety-guarantee callout into the panel's `safetyNote` slot.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated.** `extraColumn` on `IssueTable` is optional and additive — the three Batch A pages that don't pass it are unaffected (confirmed: `tsc --noEmit` and the full `npm run build` both pass with all Batch A + Batch B pages together, since this branch is stacked on Batch A's).
- **No behavior change**: no API client, backend route, or validate/commit/confirm-phrase logic touched on either tool — both keep their existing type-to-confirm gate (`APPLY` / `IMPORT`) exactly as before.
- **What could regress**: none identified for Batch A's tools (additive prop) or for these two tools' own upload/validate/commit flow (presentation-only change, confirmed via full build + test suite).

## 5. User-experience effect

Internal-admin-facing only, on two already-existing migration tools with no rider/driver visibility. Same nature of change as Batch A: richer default content, same interaction flow, same confirm-phrase gates.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/components/bulk-import/issue-table.tsx` | Added optional `extraColumn` prop | Wallet Import and Booking Import both have a second identifier column Batch A's tools didn't need |
| `admin-dashboard/src/lib/bulk-import-error-help.ts` | Added `explainWalletImportIssue`, `explainBookingImportIssue` | Plain-language error help for these two tools |
| `admin-dashboard/src/lib/__tests__/bulk-import-error-help.test.ts` | Added tests for both new explainers | Lock in message-vocabulary isolation between tools |
| `admin-dashboard/src/app/dashboard/bulk-operations/_components/LegacyWalletImport.tsx` | Adopted shared components; added explainer panel | Batch B rollout |
| `admin-dashboard/src/app/dashboard/bulk-operations/_components/LegacyBookingImport.tsx` | Same | Batch B rollout |

## 7. Before / after

```tsx
// Before — IssueTable had no way to show a second identifier column
<IssueTable items={report.errors} />  // bespoke per-tool component, 4 fixed columns

// After — shared, generic, with an opt-in extra column
<IssueTable
    items={items}
    getRef={(it) => String(it.row_num)}
    getField={(it) => it.field}
    getMessage={(it) => it.message}
    refLabel="Row"
    extraColumn={{ label: "Wallet entry", getValue: (it) => it.old_id }}
    explain={explainWalletImportIssue}
/>
```

## 8. Rollback plan

`git-revert-safe` — purely additive/presentational, same as Batch A. Reverting this PR alone (leaving Batch A merged) is safe since `extraColumn` is optional and Batch A's tools never pass it.

## 9. Verification performed

- [x] Automated tests run: `npx vitest run` (full suite, includes Batch A's tests since this branch stacks on it) → 610 passed (65 files).
- [x] **Real production build run**: `npm run build` completed with no errors.
- [x] Blast-radius grep/type-check: `tsc --noEmit` across the whole project confirms the new optional `extraColumn` prop doesn't break any existing `IssueTable` call site (Batch A's three tools omit it and still compile).
- [x] Reviewed against `CLAUDE.md` conventions: no PII in any explanatory or error-help string; no backend/API/money-write-path logic touched (both tools' existing type-to-confirm gates for real money/data writes are unchanged).

## What was NOT verified

- Not manually clicked through in a running admin-dashboard instance.
- Wallet/Booking Import's backend message strings were confirmed as of this reading of `wallet_import_service.py`/`booking_import_service.py`; same maintenance note as prior batches if those messages change later.
- No screenshot/visual-regression check — `bulk-operations` is not one of the 6 seeded Playwright visual-regression pages.

## Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed
- [x] No silent behavior change to an already-shipped flow (confirm-phrase gates, validate/commit logic all unchanged)
