# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-10 |
| Author | ittalenthire.ca@gmail.com (via Claude Code) |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | (branch `claude/bulk-import-ux-batch-c`, stacked on batch-h / PR #5170) |
| Related issue or gap ID | Batch C of the Bulk Import UX rollout — Bulk Rider Import (Fix Rider Join Dates left unchanged — already well-explained, no error table) |

## 1. Issue / gap identified

`bulk-operations/page.tsx`'s `RiderImportSection` had the same flat, unexplained error/warning table gap as the CSV-upload tools in earlier batches.

## 2. Root cause

Same as prior batches. `RiderCreatedAtBackfillSection` (Fix Rider Join Dates) already has a full-sentence `CardDescription` and no per-row error table (aggregate scan/fix counts only) — same shape as Batch D's two repair sections — so it was deliberately left unchanged, consistent with that precedent.

## 3. Fix / remediation

- Added `explainRiderImportIssue` to `lib/bulk-import-error-help.ts`, built from `services/rider_import_service.py`'s message strings, with prefix rules for three dynamic-suffix/dynamic-middle messages (invalid phone format, non-Stripe-shaped customer_id, PII-protected-account skip).
- **Important scoping note**: this is the first batch to touch `bulk-operations/page.tsx` itself. That file already defines its own local `IssueTable` and `Stat` components, reused by the not-yet-redesigned Stripe Mapping Import section (Batch F) and Route Snapshot/Backfill sections (Batch G). To avoid breaking those before their own batches land:
  - The shared `IssueTable` component is imported under the alias `SharedIssueTable` (the page's own local `IssueTable` function, used by Stripe Mapping, is untouched).
  - Only `RiderImportSection`'s own `<Stat .../>` call sites were changed to `<StatTile .../>` — the page's local `Stat` function definition is left in place (still used by Stripe Mapping's and the route tools' sections) and will be removed once Batch G (the last page.tsx batch) redesigns its last consumer.
- Added the `WhatThisToolDoes` panel to `RiderImportSection` only.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to `RiderImportSection`.** Confirmed via `tsc --noEmit` across the whole project (no errors) and the full `npm run build` that Stripe Mapping Import and the Route tools — still using the page's local `IssueTable`/`Stat` — are unaffected.
- **No behavior change**: validate/commit logic for rider import is untouched.
- **What could regress**: none identified for `RiderImportSection` itself. The main risk this batch introduces is the shared-vs-local naming collision in one file — mitigated by the explicit `SharedIssueTable` alias and by leaving `Stat` in place until its last consumer (Batch G) is redesigned.

## 5. User-experience effect

Internal-admin-facing only, on the Rider Import section specifically. Stripe Mapping and Route tools' sections on the same page are visually and functionally unchanged in this batch.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/lib/bulk-import-error-help.ts` | Added `explainRiderImportIssue` | Plain-language error help for Bulk Rider Import |
| `admin-dashboard/src/lib/__tests__/bulk-import-error-help.test.ts` | Added tests for the new explainer | Lock in exact/prefix matching and isolation |
| `admin-dashboard/src/app/dashboard/bulk-operations/page.tsx` | `RiderImportSection` adopts shared components (aliased import to avoid colliding with the page's own not-yet-redesigned local components); added explainer panel | Batch C rollout |

## 7. Before / after

Same pattern as prior batches — see Batch A's Change Impact Log for the representative snippet. The one structural difference this batch introduces is the `SharedIssueTable` import alias, documented above.

## 8. Rollback plan

`git-revert-safe` — purely additive/presentational; reverting restores `RiderImportSection`'s own local components exactly as they were, with zero effect on the page's other sections since they were never touched.

## 9. Verification performed

- [x] Automated tests: `npx vitest run` (full suite, cumulative with A/B/D/E/H) → 619 passed (65 files).
- [x] **Real production build run**: `npm run build` completed with no errors.
- [x] Blast-radius grep/type-check performed: confirmed the page's other sections (Stripe Mapping, Route Snapshot, Route Backfill) still compile and are unaffected by the aliasing approach.
- [x] Reviewed against `CLAUDE.md` conventions: no PII in any new explanatory string. Note: the backend's own `invalid phone format (expected +1XXXXXXXXXX): {phone}` error message echoes the row's own phone value back to the admin viewing their own uploaded CSV's validation report — this is pre-existing backend behavior, not something this batch changed, and out of scope for this presentation-only rollout; flagging for awareness only.

## What was NOT verified

- Not manually clicked through in a running admin-dashboard instance.
- `rider_import_service.py`'s messages confirmed as of this reading.
- No screenshot/visual-regression check.

## Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed (including the shared-file naming-collision risk, explicitly mitigated)
- [x] No silent behavior change to an already-shipped flow
