# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-10 |
| Author | ittalenthire.ca@gmail.com (via Claude Code) |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | (branch `claude/bulk-import-ux-batch-g`, stacked on batch-f / PR #5172) |
| Related issue or gap ID | Batch G of the Bulk Import UX rollout — Route Map Snapshots + Route Backfill (`SnapshotRegenerateSection` / `RouteRegenerateSection` in `bulk-operations/page.tsx`) |

## 1. Issue / gap identified

Route Map Snapshots and Route Backfill had no what/why/which-files/value panel, and their collapsed `Error details` list showed only a raw `ride_id — error` string per failed ride, with no plain-language explanation.

## 2. Root cause

Same as every prior batch — these two tools predate the redesign. Unlike the CSV-upload tools, neither has a `row_ref`/`field`/`message`-shaped report: both report an aggregate success/failed count plus an optional per-ride `{ride_id, error}` list rendered in a `<details>`/`<ul>`, not a table — so this batch's per-item treatment is a plain-language line under each list item rather than the shared `IssueTable` component (which is built around the ref/field/message shape the CSV tools share, not this one).

## 3. Fix / remediation

- Added a `WhatThisToolDoes` panel above each of the two tool cards (`SnapshotRegenerateSection`, `RouteRegenerateSection`).
- Added `explainRouteRegenIssue` to `lib/bulk-import-error-help.ts`, built from reading both backend route handlers in full (`backend/routes/admin/rides.py`'s `admin_regenerate_imported_snapshots` and `admin_regenerate_imported_routes`). **Important limitation, stated explicitly rather than worked around**: two of the four possible error strings per tool (`f"upload: {exc}"`, `f"db update: {exc}"`) interpolate the raw underlying exception text with no fixed vocabulary — unlike every other explainer in this file, these two get a generic, honest "this is usually transient, re-run and check backend logs" explanation rather than a guess at what the specific exception could be. The two genuinely fixed-vocabulary messages (`"missing coordinates"`, `"render returned None"`, `"no route from OSRM or Google Directions"`) get real, specific cause/fix text.
- Rendered the explanation inline under each `<li>` in both tools' existing `<details>` error list — the list structure itself is untouched, only a conditional explanation block was added under each row, matching the shared `IssueTable` component's own inline-explanation styling for visual consistency without forcing this two-field shape into a table component built for a different one.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to these two components' error-list rendering.** No other component reads `explainRouteRegenIssue` or renders this `{ride_id, error}` shape. Confirmed via grep that both sections' logic (`handlePreview`, `handleRegenerate`, force/offset paging state) is completely untouched — only presentation inside the existing `result.errors.map(...)` block changed.
- **No behavior change**: preview/regenerate network calls, force-mode paging, and toast messaging are all untouched.
- **What could regress**: none identified — this is the same additive pattern (inline explanation appended under existing content) used throughout the rollout.

## 5. User-experience effect

Internal-admin-facing only (super-admin-only page). Both tools now show the consistent explainer panel, and a failed ride's error line gets a plain-language cause/fix underneath it where the message is in the fixed vocabulary; for the two exception-interpolated messages, the admin gets an honest "this varies, here's what usually helps" note instead of either silence or a fabricated specific explanation.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/lib/bulk-import-error-help.ts` | Added `explainRouteRegenIssue` | Plain-language error help for Route Map Snapshots / Route Backfill, shared since both tools emit two identical error strings |
| `admin-dashboard/src/lib/__tests__/bulk-import-error-help.test.ts` | Added tests for the new explainer | Lock in exact/prefix matching and cross-tool isolation |
| `admin-dashboard/src/app/dashboard/bulk-operations/page.tsx` | Added `WhatThisToolDoes` panels to both sections; added inline plain-language explanation under each error-list `<li>` in both `SnapshotRegenerateSection` and `RouteRegenerateSection` | Batch G rollout — completes all 8 planned batches of the Bulk Import UX rollout |

## 7. Before / after

```tsx
// Before
<ul className="mt-2 space-y-1 text-xs font-mono">
    {result.errors.map((e, i) => (
        <li key={i}>
            {e.ride_id.slice(0, 8)}… — {e.error}
        </li>
    ))}
</ul>
```

```tsx
// After
<ul className="mt-2 space-y-2">
    {result.errors.map((e, i) => {
        const explanation = explainRouteRegenIssue(e.error);
        return (
            <li key={i} className="text-xs">
                <p className="font-mono">{e.ride_id.slice(0, 8)}… — {e.error}</p>
                {explanation ? (
                    <div className="mt-1 space-y-0.5 rounded border-l-2 border-muted-foreground/30 pl-2 text-muted-foreground">
                        <p>{explanation.cause}</p>
                        <p className="font-medium">What to do: {explanation.fix}</p>
                    </div>
                ) : null}
            </li>
        );
    })}
</ul>
```

## 8. Rollback plan

`git-revert-safe` — purely additive/presentational; reverting restores the original raw `ride_id — error` list and removes the two `WhatThisToolDoes` panels with zero effect on the tools' preview/regenerate logic.

## 9. Verification performed

- [x] Automated tests: `npx vitest run` (full suite, cumulative with A–F/H) → 628 passed (65 files), up from 625 (3 new tests for `explainRouteRegenIssue`).
- [x] **Real production build run**: `npm run build` completed with no errors.
- [x] `npx tsc --noEmit` — no errors.
- [x] `npx eslint` on all three changed files — no warnings.
- [x] Blast-radius grep/read performed: confirmed both backend route handlers' complete error-string vocabulary before writing the explainer, and confirmed no other file renders this `{ride_id, error}` shape.
- [x] Reviewed against `CLAUDE.md` conventions: no PII in any new string — `ride_id` is a UUID (already truncated to 8 chars in the existing display), and the two exception-interpolated messages come from Supabase storage/DB client errors, which do not carry rider/driver PII by their own nature (connection/permission/constraint errors, not row contents).

## What was NOT verified

- Not manually clicked through in a running admin-dashboard instance (would require seeded imported-ride fixtures and either a Google Maps key or the OSM fallback path).
- The exact set of exception strings `upload: {exc}` / `db update: {exc}` can produce was not enumerated (by design — see the "Important limitation" note above; this is an open-ended exception surface, not a fixed vocabulary that could be fully cataloged).
- No screenshot/visual-regression check — `/dashboard/bulk-operations` is not one of the 6 seeded Playwright pages.

## Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed
- [x] No silent behavior change to an already-shipped flow — and the explainer's own honesty limitation (generic text for exception-interpolated messages) is stated rather than glossed over

## Rollout complete

This is the 8th and final batch (A–H) of the Bulk Import UX rollout — all ~18 tools on the Bulk Import page now have a consistent what/why/which-files/value panel, and every tool with a fixed per-row/per-item error vocabulary has plain-language cause/fix explanations. Merge order (each PR stacked on the previous): #5163 (A) → #5165 (B) → #5168 (D) → #5169 (E) → #5170 (H) → #5171 (C) → #5172 (F) → this PR (G).
