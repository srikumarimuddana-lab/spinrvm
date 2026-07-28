# Change Impact & Risk Log — Data Transfer Import tab: update_existing checkbox

## Issue/gap identified
The backend `update_existing` opt-in on `POST /data-transfer/import/commit`
(added in PR #2665) had no frontend surface — the Import tab always
committed with the default `update_existing=false`, so the capability was
reachable only via a raw API call, not through the admin UI it was built
for.

## Root cause
PR #2665 was scoped backend-contract-only by design (flagged explicitly in
its Change Impact Log as a follow-up). This PR is that follow-up.

## Fix/remediation
- `admin-dashboard/src/lib/api.ts`: `adminCommitDataTransferImport` gains
  an optional third `updateExisting` param, appended to the outgoing
  `FormData` as `update_existing=true` only when set (omitted otherwise,
  matching the backend's `Form(False)` default — no behavior change when
  the toggle is off). `DataTransferImportCommitResult` gains
  `updated_users`/`updated_drivers` fields to match the backend's response
  shape (additive, already present in the backend response since #2665;
  the frontend type just hadn't been updated to read them).
- `admin-dashboard/src/app/dashboard/data-transfer/ImportTab.tsx`: added a
  `Switch` (reused existing `components/ui/switch.tsx` — no new UI
  component introduced) labeled "Update already-imported entities", wired
  to local `updateExisting` state. Passed through to
  `adminCommitDataTransferImport` on commit. The existing_match count in
  the validate/commit report now reads "(will be updated)" or "(will be
  skipped)" depending on the toggle, so the admin sees the effect before
  committing. The commit-success toast appends an update summary
  (`updated_users`/`updated_drivers`) only when the toggle was on and at
  least one row was actually updated.

## Risk & impact on existing functionality
Blast radius: `ImportTab.tsx` is only rendered from
`dashboard/data-transfer/page.tsx`'s Import tab — grepped for other
importers of `ImportTab`, `BundleDropzone`, and
`adminCommitDataTransferImport`; none found outside this page.
`DataTransferImportCommitResult`/`adminCommitDataTransferImport` in
`api.ts` are likewise only consumed by `ImportTab.tsx`.

Default behavior is unchanged: the switch defaults to off, and
`adminCommitDataTransferImport`'s third param is optional — any other
hypothetical caller passing only `(file, batch)` behaves exactly as
before (no `update_existing` field appended, backend defaults to
`false`). No existing consumer of `DataTransferImportCommitResult` breaks
from the two new optional fields.

## User experience effect
Admin-facing only (Data Transfer → Import tab), not visible to
riders/drivers. An admin can now opt into syncing already-imported
records from a refreshed bundle instead of the import silently doing
nothing for them — the gap called out in the prior PR. No change to any
already-shipped screen's default behavior; the toggle is off by default
and the commit flow is otherwise identical.

## Files modified
| File | What changed | Why |
|---|---|---|
| `admin-dashboard/src/lib/api.ts` | `adminCommitDataTransferImport` gains optional `updateExisting` param; `DataTransferImportCommitResult` gains `updated_users`/`updated_drivers` | Wire the backend's existing `update_existing` field + response shape through to the frontend |
| `admin-dashboard/src/app/dashboard/data-transfer/ImportTab.tsx` | Added update-existing `Switch` toggle, wired to commit call; report summary and success toast reflect the toggle | Give the admin a UI to reach the backend capability added in #2665 |

## Before/after snippet
```tsx
// ImportTab.tsx, before:
const result = await adminCommitDataTransferImport(file);
// existing_match entities were always silently skipped, no way to change that from the UI

// after:
const result = await adminCommitDataTransferImport(file, undefined, updateExisting);
// updateExisting is a Switch the admin controls before committing
```

## Rollback plan
Revert both files (`git revert`) — purely additive UI/type changes, no
backend or data changes bundled in this PR. Reverting restores the prior
Import tab (no toggle, always `update_existing=false`) with no other
side effects, since the toggle already defaults to off.

## Verification performed
- Real production build: `npm run build` in `admin-dashboard/` — exit 0,
  confirmed `/dashboard/data-transfer` compiled (not just `tsc --noEmit`
  or a dev server, per CLAUDE.md's requirement for admin-dashboard
  changes).
- Manually traced the `updateExisting` prop through
  `adminCommitDataTransferImport` to confirm the FormData field name
  (`update_existing`) matches the backend route's
  `update_existing: bool = Form(False)` parameter added in #2665.
- Grepped for other consumers of `ImportTab`, `BundleDropzone`,
  `adminCommitDataTransferImport`, and `DataTransferImportCommitResult`
  to confirm isolated blast radius (see Risk section).

## What was NOT verified
- No visual regression tooling exists in this repo (a standing gap per
  CLAUDE.md's release-gates section) — the new `Switch`/`Label`
  placement was reasoned about from the existing component library's
  conventions, not screenshotted in a running browser.
- Not exercised against a live backend / real bundle ZIP in this sandbox
  — the build confirms the code compiles and types match the backend's
  response shape from #2665, not an end-to-end click-through (validate →
  toggle on → commit → confirm updated_users/updated_drivers render
  correctly against a real already-imported entity).
- No automated frontend test (unit/e2e) added for this component; the
  existing Data Transfer frontend surface has no test coverage
  precedent in this repo to extend (same standing gap as the rest of the
  module's UI layer).
