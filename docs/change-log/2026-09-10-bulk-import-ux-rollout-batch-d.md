# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-10 |
| Author | ittalenthire.ca@gmail.com (via Claude Code) |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | (branch `claude/bulk-import-ux-batch-d`, stacked on batch-b / PR #5165) |
| Related issue or gap ID | Batch D of the Bulk Import UX rollout — Legacy Driver Import |

## 1. Issue / gap identified

`drivers/legacy-import/page.tsx`'s main import flow had the same flat, unexplained error/warning table gap as the other tools. Its two inline sibling sections (Fix Orphaned Legacy-Linked Accounts, Fix Backfilled Driver Join Dates) already had thorough, full-sentence explanatory copy and no per-row error table (just aggregate scan/fix counts) — left unchanged, since they don't have the gap this rollout addresses.

## 2. Root cause

Same as prior batches for the main import flow.

## 3. Fix / remediation

- Added `explainLegacyDriverImportIssue` to `lib/bulk-import-error-help.ts`, built from `services/driver_import_service.py`'s `build_mongo_driver_import_plan` message strings, including prefix matches for the two messages with a dynamic suffix (blank-name placeholder, duplicate-batch merge).
- Replaced the page's bespoke `IssueTable`/`Stat` with the shared components and added the `WhatThisToolDoes` panel, folding the existing "create, link, or enrich" safety note into it.
- Deliberately did **not** touch `OrphanedDriverBackfillSection` or `DriverCreatedAtBackfillSection` — both already explain themselves fully in prose and have no per-row messages to map; adding a redundant panel would not serve the goal this rollout exists for.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated** to this one page. Grepped for other importers of the touched local components — none exist; `LegacyDriverImportReportItem`'s shape (`old_driver_id`/`field`/`message`) is used only on this page.
- **No behavior change**: validate/commit logic, the validation-token gate, and the two repair sections' scan/apply logic are all untouched.
- **What could regress**: none identified — presentation-only change, confirmed via full build + test suite.

## 5. User-experience effect

Internal-admin-facing only. Same nature as prior batches: richer default content on the main import card, unchanged interaction flow. The two repair sections are visually and functionally unchanged.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/lib/bulk-import-error-help.ts` | Added `explainLegacyDriverImportIssue` | Plain-language error help for this tool |
| `admin-dashboard/src/lib/__tests__/bulk-import-error-help.test.ts` | Added tests for the new explainer | Lock in exact/prefix matching and isolation from other tools' messages |
| `admin-dashboard/src/app/dashboard/drivers/legacy-import/page.tsx` | Adopted shared components for the main import flow only | Batch D rollout |

## 7. Before / after

Same pattern as prior batches — see Batch A's Change Impact Log for the representative before/after snippet.

## 8. Rollback plan

`git-revert-safe` — purely additive/presentational.

## 9. Verification performed

- [x] Automated tests: `npx vitest run` (full suite, cumulative with Batches A/B) → 613 passed (65 files).
- [x] **Real production build run**: `npm run build` completed with no errors.
- [x] Blast-radius grep performed: confirmed `LegacyDriverImportReportItem` and the touched local components have no other consumers.
- [x] Reviewed against `CLAUDE.md` conventions: no PII in any new string; no backend/API logic touched.

## What was NOT verified

- Not manually clicked through in a running admin-dashboard instance.
- `driver_import_service.py`'s messages confirmed as of this reading; same maintenance note as prior batches.
- No screenshot/visual-regression check — this route isn't one of the 6 seeded Playwright pages.

## Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed
- [x] No silent behavior change to an already-shipped flow
