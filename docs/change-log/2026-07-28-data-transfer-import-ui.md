# Change Impact & Risk Log — Data Transfer module: import tab + dropzone UI (Phase 4.2)

## Issue/gap identified
Phase 2's import backend (validate/commit) had no UI — an admin could not
actually upload a bundle ZIP through the admin dashboard.

## Root cause
Deliberate phasing — backend first, UI second.

## Fix/remediation
- Modified `admin-dashboard/src/lib/api.ts`: added
  `adminValidateDataTransferImport`/`adminCommitDataTransferImport` typed
  wrappers (FormData body, same `request<T>()` pattern as
  `adminValidateDriverImport`) + `DataTransferImportReport`/
  `DataTransferImportCommitResult` types matching the backend's
  `_report()`/commit response shapes from `data_transfer_import.py`.
- New `admin-dashboard/src/components/data-transfer/BundleDropzone.tsx`: a
  drag-and-drop (+ click-to-browse) file picker. No dedicated dropzone
  component exists elsewhere in this codebase (confirmed by the earlier
  research pass) — the existing import pages all use a plain
  `<input type="file">`. Built one specifically for the bundle ZIP since
  it's a larger, less-routine upload where drag-and-drop is a meaningfully
  better interaction than "click, navigate a file picker."
- New `admin-dashboard/src/app/dashboard/data-transfer/ImportTab.tsx`: wires
  the dropzone to validate/commit, following the same dry-run-then-commit
  UX shape as `drivers/import/page.tsx` (validate button always available
  once a file is picked; commit disabled until validation reports
  `can_commit`; re-shows the same report — counts, errors table, warnings
  list — after either call).
- Modified `admin-dashboard/src/app/dashboard/data-transfer/page.tsx`: wires
  `ImportTab` into the Import tab (replacing the placeholder).

## Risk & impact on existing functionality
Blast radius: all four changes are additive or scoped to files with zero
other consumers. `api.ts` again only gains new exports. `BundleDropzone.tsx`
and `ImportTab.tsx` are new, standalone components; `page.tsx`'s edit is a
one-import + one-tab-content swap, same shape as Phase 4.1's Export tab
wiring. No existing import flow (`drivers/import/page.tsx`, the
rider-import section of `bulk-operations`) is touched — this is a
completely separate upload path hitting different backend routes
(`/data-transfer/import/*` vs `/drivers/import/*`).

## User experience effect
None for existing users — reachable only via the still-unlinked
`/dashboard/data-transfer` URL (Phase 6.1 adds nav).

## Files modified
| File | What changed | Why |
|---|---|---|
| `admin-dashboard/src/lib/api.ts` | +import validate/commit wrappers + types (additive) | Typed client for Phase 2's import routes |
| `admin-dashboard/src/components/data-transfer/BundleDropzone.tsx` | New: drag-drop ZIP picker | Import UI's file input |
| `admin-dashboard/src/app/dashboard/data-transfer/ImportTab.tsx` | New: validate/commit UI | Import tab body |
| `admin-dashboard/src/app/dashboard/data-transfer/page.tsx` | Wires `ImportTab` into the Import tab | Replace placeholder |

## Before/after snippet
N/A — purely additive; no existing behavior-changing diff.

## Rollback plan
Delete `BundleDropzone.tsx` and `ImportTab.tsx`, revert the additive block in
`api.ts`, revert `page.tsx`'s Import tab to the placeholder. No other code
imports any of these yet (grep-confirmed).

## Verification performed
- `npx tsc --noEmit -p tsconfig.json` across the whole project — zero errors
  attributable to any file this subtask touched.
- Cross-checked `DataTransferImportReport`/`DataTransferImportCommitResult`
  field names against the actual backend response shapes in
  `data_transfer_import.py`'s `_report()` function and the commit endpoint's
  return dict (`created_users`, `created_drivers`, `documents_replayed`,
  `insurance_periods_replayed` — matching `entity_import_service.commit_plan`'s
  Phase 2.2 return keys exactly).

## What was NOT verified
- Not run in a browser — drag-and-drop behavior, the validate→commit flow,
  and the error/warning table rendering are untested against a live backend.
- No unit test for `BundleDropzone`'s drag-and-drop event handling (drag
  enter/leave/drop state transitions) — reasoned through by code reading,
  not exercised.
- The "commit disabled until validate reports can_commit" gate is UI-only;
  the backend route itself independently re-validates on commit (Phase 2.1),
  so a stale/bypassed UI state can't cause an unsafe commit — but this
  belt-and-suspenders relationship wasn't tested end-to-end together.
