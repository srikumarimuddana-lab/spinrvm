# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-10 |
| Author | ittalenthire.ca@gmail.com (via Claude Code) |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | (branch `claude/bulk-import-ux-batch-e`, stacked on batch-d / PR #5168) |
| Related issue or gap ID | Batch E of the Bulk Import UX rollout — Bulk Driver Import |

## 1. Issue / gap identified

`drivers/import/page.tsx` (the bespoke Saskatoon-recruitment-CSV importer, distinct from the Mongo-export-based Legacy Driver Import) had the same flat, unexplained error/warning table gap.

## 2. Root cause

Same as prior batches.

## 3. Fix / remediation

- Added `explainDriverImportIssue` to `lib/bulk-import-error-help.ts`, built from `services/driver_import_service.py`'s `build_plan` (the Saskatoon-CSV-shaped function, distinct from `build_mongo_driver_import_plan` used by Legacy Driver Import) and `validators.py`'s `validate_vin` message strings, with prefix rules for the two dynamic-suffix messages (unmatched vehicle type, already-imported vehicle-field update).
- Replaced the page's bespoke `IssueTable`/`Stat` with the shared components and added the `WhatThisToolDoes` panel — clarifying explicitly that this tool is for **new recruit onboarding**, not a previous-app migration (the two driver-import tools' distinction was previously only mentioned as an aside).
- This tool has no raw-export two-file path and none was added — it's explicitly a different CSV shape (a curated recruitment sheet), not a Mongo export join, so a "prepare from Mongo export" tab would be inapplicable here.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated** to this one page. `DriverImportReportItem`'s shape is used only here.
- **No behavior change**: validate/commit logic and the validation-token gate are untouched.
- **What could regress**: none identified — confirmed via full build + test suite.

## 5. User-experience effect

Internal-admin-facing only. Same nature as prior batches.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/lib/bulk-import-error-help.ts` | Added `explainDriverImportIssue` | Plain-language error help for this tool |
| `admin-dashboard/src/lib/__tests__/bulk-import-error-help.test.ts` | Added tests for the new explainer | Lock in exact/prefix matching and isolation from Legacy Driver Import's messages (same `old_driver_id`/`field`/`message` shape, different backend function, different vocabulary) |
| `admin-dashboard/src/app/dashboard/drivers/import/page.tsx` | Adopted shared components; added explainer panel | Batch E rollout |

## 7. Before / after

Same pattern as prior batches.

## 8. Rollback plan

`git-revert-safe` — purely additive/presentational.

## 9. Verification performed

- [x] Automated tests: `npx vitest run` (full suite, cumulative with A/B/D) → 616 passed (65 files).
- [x] **Real production build run**: `npm run build` completed with no errors.
- [x] Blast-radius grep performed.
- [x] Reviewed against `CLAUDE.md` conventions: no PII in any new string; no backend/API logic touched.

## What was NOT verified

- Not manually clicked through in a running admin-dashboard instance.
- `driver_import_service.py`'s `build_plan` and `validators.py`'s messages confirmed as of this reading.
- No screenshot/visual-regression check.

## Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed
- [x] No silent behavior change to an already-shipped flow
