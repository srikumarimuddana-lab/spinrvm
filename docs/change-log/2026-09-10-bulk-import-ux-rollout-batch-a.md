# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-10 |
| Author | ittalenthire.ca@gmail.com (via Claude Code) |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | (branch `claude/bulk-import-ux-batch-a`) |
| Related issue or gap ID | Rollout Batch A of the Bulk Import UX redesign (piloted on Legacy Tax-ID Backfill, PR #5154) to the three already two-file-shaped legacy backfill tools |

## 1. Issue / gap identified

Three Bulk Import tools — Legacy SIN/DOB Backfill, Legacy Vehicle-History Backfill, and Legacy Saved-Address Backfill — already had decent page-level explanatory copy, but (a) it wasn't in the consistent what/why/which-files/value shape the Tax-ID pilot established, and (b) their validation error/warning tables showed only a raw technical message (e.g. `"invalid SIN, skipped: SIN must be 9 digits; got 8"`) with no plain-language explanation of cause or fix, same gap the Tax-ID pilot closed for that tool.

## 2. Root cause

These three tools were built to the same validate → review → commit contract but predate the plain-language redesign; each duplicated its own `IssueTable`/`Stat` React components with no error-help mapping layer.

## 3. Fix / remediation

- Extracted three new shared, reusable components (`admin-dashboard/src/components/bulk-import/`): `WhatThisToolDoes` (the what/why/which-files/value panel), `StatTile` (the tone-colored count tile), and a generic `IssueTable` that accepts per-tool field accessors and an optional `explain` function — replacing what was previously a bespoke copy of each in every tool's page.
- Added `lib/bulk-import-error-help.ts`: a shared `createIssueExplainer` factory (exact-match then prefix-match lookup, mirroring `lib/tax-id-error-help.ts`'s logic) plus three tool-specific explainer maps (`explainSinDobIssue`, `explainVehicleHistoryIssue`, `explainSavedAddressIssue`) built from each tool's actual backend message strings (verified by reading `services/driver_import_service.py` and `services/saved_address_import_service.py` directly, not guessed).
- Wired all three into their respective pages: replaced the ad-hoc warning-box copy with the shared `WhatThisToolDoes` panel (folding the existing safety guarantee into its `safetyNote` slot rather than duplicating it in a separate box), and replaced each page's local `IssueTable`/`Stat` with the shared components.
- `lib/tax-id-error-help.ts` (the already-merged pilot's own file) was left untouched — this is new, shared infrastructure for tools rolled out from here forward, not a retrofit of already-shipped code.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to these three pages plus new shared files with no other consumers yet.** Grepped for existing importers of `components/bulk-import/*` and `lib/bulk-import-error-help` — none exist outside the three edited pages (this PR introduces both).
- **No behavior change to validate/commit flow**: no API client, backend route, or handler logic was touched — only the presentation layer (which local components render the same `report.errors`/`report.warnings` arrays the pages already fetched).
- **What could regress**: none identified. `WhatThisToolDoes`, `StatTile`, and `IssueTable` are new, purely additive UI components; each old inline component is fully removed and replaced 1:1 at every call site (verified via `tsc --noEmit` catching any missed reference, and a full `npm run build`).

## 5. User-experience effect

Internal-admin-facing only, on three already-existing migration tools with no rider/driver visibility. Each page's default-visible content changes (a plain-language panel replaces a shorter callout; error/warning tables now show inline explanations) but the underlying upload/validate/commit interaction is unchanged — same buttons, same flow, same confirmation dialogs.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/components/bulk-import/what-this-tool-does.tsx` (new) | Shared what/why/files/value panel | Reused pilot pattern, generalized for rollout |
| `admin-dashboard/src/components/bulk-import/stat-tile.tsx` (new) | Shared count tile | Removes 3x duplicated `Stat` component |
| `admin-dashboard/src/components/bulk-import/issue-table.tsx` (new) | Generic error/warning table with optional per-row explanation | Removes 3x duplicated `IssueTable` component |
| `admin-dashboard/src/lib/bulk-import-error-help.ts` (new) | Shared explainer factory + 3 tool-specific message maps | Plain-language error help, generalized from the Tax-ID pilot |
| `admin-dashboard/src/lib/__tests__/bulk-import-error-help.test.ts` (new) | Unit tests for the factory and all 3 explainers | Lock in exact/prefix matching and per-tool message isolation |
| `admin-dashboard/src/app/dashboard/drivers/legacy-sin-dob-backfill/page.tsx` | Adopted shared components; added explainer panel | Batch A rollout |
| `admin-dashboard/src/app/dashboard/drivers/legacy-vehicle-history-backfill/page.tsx` | Same | Batch A rollout |
| `admin-dashboard/src/app/dashboard/riders/legacy-saved-address-backfill/page.tsx` | Same | Batch A rollout |

## 7. Before / after

```tsx
// Before — each page had its own copy of this exact component
function IssueTable({ items }: { items: SinDobBackfillReportItem[] }) {
    return (
        <Table>...</Table>  // no explanation, just row_ref/field/message
    );
}
```

```tsx
// After — shared, generic, with plain-language help
function SinDobIssueTable({ items }: { items: SinDobBackfillReportItem[] }) {
    return (
        <IssueTable
            items={items}
            getRowKey={(it, i) => `${it.old_driver_id}-${it.field}-${i}`}
            getRef={(it) => it.old_driver_id}
            getField={(it) => it.field}
            getMessage={(it) => it.message}
            refLabel="old_driver_id"
            explain={explainSinDobIssue}
        />
    );
}
```

## 8. Rollback plan

`git-revert-safe` — no schema/config/API dependency; purely a presentation-layer change on three internal admin pages. Reverting removes the new shared components and explainer file and restores each page's own bespoke inline components exactly as they were.

## 9. Verification performed

- [x] Automated tests run: `npx vitest run` (full suite) → 605 passed (65 files), including 12 new tests for the shared error-help factory and its three tool-specific explainers.
- [x] **Real production build run**: `npm run build` completed with no errors; all three touched routes compiled. `tsc --noEmit` and `eslint` also run clean beforehand as a faster first pass, not a substitute.
- [x] Blast-radius grep performed: confirmed no other file imports the new shared components or the new error-help module yet (this PR is their only consumer).
- [x] Reviewed against `CLAUDE.md` conventions: no PII in any new explanatory or error-help string (all fixed text); no backend/API/schema touched.

## What was NOT verified

- Not manually clicked through in a running admin-dashboard instance — verification is `npm run build` + `vitest` + `tsc`/`eslint` only, no live browser session in this environment.
- These three tools' backend messages were confirmed correct as of this reading of `driver_import_service.py`/`saved_address_import_service.py`; if either service's message strings change later, this file's mappings need a matching update (same maintenance note as `lib/tax-id-error-help.ts`).
- No screenshot/visual-regression check — none of these three routes are among the 6 pages `admin-dashboard`'s seeded Playwright visual-regression suite covers.

## Sign-off

- [x] Rollback plan is concrete and testable (`git revert`, purely additive/presentational)
- [x] Blast radius is stated, not assumed (isolated to 3 pages + new shared files with no other consumers)
- [x] No silent behavior change to an already-shipped flow (validate/commit interaction itself is unchanged; only presentation)
