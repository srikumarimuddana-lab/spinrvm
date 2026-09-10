# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-10 |
| Author | ittalenthire.ca@gmail.com (via Claude Code) |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | (branch `claude/bulk-import-ux-batch-h`, stacked on batch-e / PR #5169) |
| Related issue or gap ID | Batch H of the Bulk Import UX rollout — the four Phase 6 no-file, preview/commit tools |

## 1. Issue / gap identified

Pre-Launch Legacy Data Flagging, Migration Data Quality Scan, Driver-Repair Pass, and Legacy ID Crosswalk Backfill already had solid `CardDescription` text and (for three of the four) a dedicated info callout explaining safety/scope — better starting copy than most tools in this rollout. What they lacked was the **consistent** what/why/which-files/value structure the rest of the page now has, so an operator moving between tools sees one predictable shape rather than four different ad-hoc prose styles.

## 2. Root cause

These four tools predate the redesign, like the rest. Unlike the CSV-upload tools, they have no per-row error/warning messages to map to plain language (they report aggregate counts only) — so this batch's scope is explainer-panel consistency, not error-help.

## 3. Fix / remediation

- Added the shared `WhatThisToolDoes` panel to all four components, folding each tool's existing safety/scope callout into the panel's `safetyNote` slot (removing the separate `Info` callout box where one existed, since the panel now carries that content).
- Replaced each tool's local `Stat` component with the shared `StatTile` (all four had a byte-for-byte or near-identical copy of it).
- No error-help additions — none of these four tools has a `row_ref`/`field`/`message`-shaped report; their "errors" are aggregate counts, already well summarized by the existing stat tiles and toast messages.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated** to these four components; each lives in its own file with no shared local state.
- **No behavior change**: preview/commit logic, the type-to-confirm gates (where present), and toast messaging are all untouched.
- **What could regress**: none identified — presentation-only, confirmed via full build + test suite.

## 5. User-experience effect

Internal-admin-facing only. Content is reorganized into the shared panel shape but no information is lost — every fact from the original callouts is preserved, either in the panel's structured fields or its `safetyNote`.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/bulk-operations/_components/PreLaunchDataFlag.tsx` | Adopted shared `WhatThisToolDoes`/`StatTile` | Batch H rollout |
| `admin-dashboard/src/app/dashboard/bulk-operations/_components/DataQualityScan.tsx` | Same | Batch H rollout |
| `admin-dashboard/src/app/dashboard/bulk-operations/_components/DriverRepairPass.tsx` | Same | Batch H rollout |
| `admin-dashboard/src/app/dashboard/bulk-operations/_components/LegacyIdCrosswalkBackfill.tsx` | Same | Batch H rollout |

## 7. Before / after

```tsx
// Before — DataQualityScan.tsx
<CardDescription>
    Find completed rides with a missing driver, ... Read the full breakdown ... in{" "}
    <a href="...">the data-quality strategy runbook</a>.
</CardDescription>
// (no structured panel; explanatory prose lives only in the CardDescription)
```

```tsx
// After
<CardDescription>
    Find completed rides with a data-quality issue and flag them for review. Read
    the full breakdown ... in <a href="...">the data-quality strategy runbook</a>.
</CardDescription>
...
<WhatThisToolDoes what={...} why={...} whichFiles={...} value={...} safetyNote={...} />
```

## 8. Rollback plan

`git-revert-safe` — purely additive/presentational; every fact from the original copy is preserved in the new structure.

## 9. Verification performed

- [x] Automated tests: `npx vitest run` (full suite, cumulative with A/B/D/E) → 616 passed (65 files) — unchanged count from Batch E since no new logic/tests were added, only presentation.
- [x] **Real production build run**: `npm run build` completed with no errors.
- [x] Blast-radius grep performed: confirmed each of the four components has no other consumers of its local `Stat`.
- [x] Reviewed against `CLAUDE.md` conventions: no PII in any new string; no backend/API/production-write logic touched (confirm-phrase gates for real writes are unchanged).
- [x] ESLint clean (2 minor unescaped-apostrophe warnings found and fixed during this batch).

## What was NOT verified

- Not manually clicked through in a running admin-dashboard instance.
- No screenshot/visual-regression check — none of these four tools' route is among the 6 seeded Playwright pages.

## Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed
- [x] No silent behavior change to an already-shipped flow (every fact from the original copy preserved)
