# Change Impact & Risk Log — Data Transfer module: search/select UI (Phase 3.2)

## Issue/gap identified
Phase 3.1 shipped the search backend with nothing to call it — no frontend
existed for an admin to actually search, view results, or select records.

## Root cause
Deliberate phasing — backend endpoint first, UI second, per CLAUDE.md's
≤3-file subtask rule.

## Fix/remediation
- Modified `admin-dashboard/src/lib/api.ts`: added `searchDataTransferEntities`
  typed wrapper + `DataTransferEntityRow`/`DataTransferSearchResult`/
  `DataTransferSearchParams` types, following the existing `request<T>()`
  pattern (auto Content-Type, CSRF header, 401 refresh — all inherited, no
  new logic).
- New `admin-dashboard/src/components/data-transfer/useEntitySelection.ts`:
  shared selection-state hook supporting both explicit per-row checkboxes
  (a `Set<string>` of IDs) and "select all N matching this filter" (stores
  the filter criteria itself, not resolved IDs, so the Export/SGI-forms tabs
  can pass the filter to the backend and let it resolve the full ID set
  server-side rather than the client paging through thousands of rows to
  build a giant ID array).
- New `admin-dashboard/src/components/data-transfer/EntitySearchTable.tsx`:
  the actual search UI — text input, entity-type filter, date-range pickers,
  paginated results table with checkboxes, reusing the existing `Table`/
  `Select`/`Input`/`Button` shadcn components (no new UI primitives) and the
  existing plain `<input type="checkbox">` convention already used elsewhere
  in this codebase (`staff/page.tsx`, `document-reviewer.tsx`, etc. — no
  dedicated Checkbox component exists in this codebase, so this doesn't
  introduce one either).
- New `admin-dashboard/src/app/dashboard/data-transfer/page.tsx`: the module's
  page shell — tabs for Search & Select (fully wired), Export/Import/SGI
  Compliance Forms (placeholder content, wired in follow-up subtasks),
  gated by `useRequireModule("bulk_operations")` matching the backend's
  `require_module("bulk_operations")` gate from Phase 1.2/2.1/3.1.

## Risk & impact on existing functionality
Blast radius: `api.ts` only gets new additive exports (a new interface block
+ one new function) appended at the end of the file — no existing export
signature changed. The three new component files and the new page route
(`/dashboard/data-transfer`) have zero existing importers/callers (grep-
confirmed) — nothing currently links to this page (nav wiring is Phase 6.1).
Ran `npx tsc --noEmit` across the whole `admin-dashboard` project: 22
pre-existing errors, all in unrelated test files (`companyApi.test.ts`,
`route-segments.test.ts` — missing Jest type globals, unrelated to this
change); zero errors reference any of the four files this subtask touched.

## User experience effect
None yet for existing users — this is a brand-new, unlinked page
(`/dashboard/data-transfer`) not yet reachable from the sidebar (Phase 6.1
adds that link). An admin who navigates to the URL directly and has the
`bulk_operations` module can now search/select, but Export/Import/SGI Forms
tabs show "Coming soon" placeholders until their subtasks land.

## Files modified
| File | What changed | Why |
|---|---|---|
| `admin-dashboard/src/lib/api.ts` | +search wrapper + types (additive only) | Typed client for the Phase 3.1 search endpoint |
| `admin-dashboard/src/components/data-transfer/useEntitySelection.ts` | New: selection state hook | Shared explicit/select-all-matching selection |
| `admin-dashboard/src/components/data-transfer/EntitySearchTable.tsx` | New: search UI | Search & Select tab |
| `admin-dashboard/src/app/dashboard/data-transfer/page.tsx` | New: module page shell | Tabs container, module gating |

## Before/after snippet
N/A — purely additive; no existing behavior-changing diff (new page, new
components, new API export).

## Rollback plan
Delete the three new files and the `page.tsx` route directory; revert the
additive block in `api.ts`. No other code imports any of them yet (grep-
confirmed) — the page isn't linked from any nav, so removing it has zero
user-visible effect beyond a 404 on a URL nobody was given yet.

## Verification performed
- `npx tsc --noEmit -p tsconfig.json` across the entire admin-dashboard
  project — 22 pre-existing errors, none in the files this subtask touched
  (confirmed via grep on the error output for the new file names).
- Manually verified `useToast`'s import path (`@/components/ui/use-toast`)
  and every shadcn component (`Table`, `Select`, `Input`, `Button`, `Card`,
  `Tabs`) referenced actually exists in `components/ui/` before using it.
- Confirmed the checkbox pattern (`<input type="checkbox">` + `accent-*`
  styling elsewhere) is the codebase's existing convention rather than
  inventing a new Checkbox component.

## What was NOT verified
- Not run in a browser — no dev server was started to click through the
  search flow, verify the "select all matching filter" banner renders
  correctly, or confirm pagination behaves as expected against live data.
  Per CLAUDE.md's UI-change guidance, this should be done before this
  feature ships to real admins; flagging it as outstanding.
- No visual regression tooling exists in this repo (a standing gap per
  CLAUDE.md), so layout/spacing correctness was reasoned about against
  existing similar pages, not screenshotted.
- The `useEntitySelection` hook's "select all matching" -> individual-toggle
  transition (narrowing back to explicit mode) is logically straightforward
  but has no unit test — no test harness for React hooks was set up in this
  subtask to keep it to 3 code files.
