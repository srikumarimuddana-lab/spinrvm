# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-28 |
| Author | Claude Code (session), on behalf of vikas@ngitservices.com |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | drivers |
| PR / commit link | branch `claude/migration-batch-readiness-wicr1d` |
| Related issue or gap ID | UX feedback from operator running the live Phase 1 legacy driver import this session |

## 1. Issue / gap identified

The Legacy Driver Import validate report (and its two Phase 2 siblings —
SIN/DOB backfill, vehicle-history backfill) renders stat tiles followed by a
full row-by-row warnings/errors table that can run 900+ rows. There was no
compact way for an operator to share/record just the summary counts (e.g.
to paste into a chat for a second pair of eyes to sanity-check) without a
screenshot of the whole page.

## 2. Root cause

The report views were built with the detail table as the only output; no
aggregate/copyable view of the stat-tile numbers was ever added.

## 3. Fix / remediation

Added a `buildSummaryText()` helper on each of the three legacy-import
report pages that renders just the stat-tile counts (plus batch id) as a
small markdown table, and a "Copy summary" button (using the existing
`navigator.clipboard.writeText` + toast pattern already used elsewhere in
this codebase, e.g. `dashboard/drivers/page.tsx`) placed between the stat
grid and the detail tables. No change to the underlying report data, the
existing stat tiles, or the detail tables themselves — purely additive.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to 3 files** — `legacy-import/page.tsx`,
  `legacy-sin-dob-backfill/page.tsx`, `legacy-vehicle-history-backfill/page.tsx`.
  Each `buildSummaryText()` is a private function scoped to its own page
  component; grepped for any other consumer of these page files or the
  function name — only each page's own smoke test matches, no shared
  component or other page imports them.
- No API client, backend route, or report data shape changed — the summary
  text is derived client-side from data the page already holds in state.
- PII: the SIN/DOB and vehicle-history reports' own header comments already
  guarantee no raw SIN/DOB/plate/VIN appears in the report (counts + old_driver_id/field/message
  only); the new summary text draws only from the same counts fields, so it
  carries no new PII exposure.

## 5. User-experience effect

- **Internal admin only.** No rider/driver-facing change. Adds one small
  "Copy summary" button per page; no existing button, tile, or table
  changed position or behavior.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/drivers/legacy-import/page.tsx` | Added `buildSummaryText()` + "Copy summary" button | Compact, copyable report summary |
| `admin-dashboard/src/app/dashboard/drivers/legacy-sin-dob-backfill/page.tsx` | Same, for the SIN/DOB backfill report shape | Same |
| `admin-dashboard/src/app/dashboard/drivers/legacy-vehicle-history-backfill/page.tsx` | Same, for the vehicle-history backfill report shape | Same |

## 7. Before / after

```
# Before (legacy-import/page.tsx, representative of all three)
<div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
    <Stat label="Rows" value={counts?.rows ?? 0} />
    ...
</div>
{report.errors.length > 0 && ( ... )}
```

```
# After
<div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
    <Stat label="Rows" value={counts?.rows ?? 0} />
    ...
</div>
<div className="flex justify-end">
    <Button variant="ghost" size="sm" onClick={() => {
        navigator.clipboard.writeText(buildSummaryText(report));
        toast({ description: "Summary copied", duration: 1500 });
    }}>
        <Copy className="mr-2 h-4 w-4" /> Copy summary
    </Button>
</div>
{report.errors.length > 0 && ( ... )}
```

## 8. Rollback plan

`git revert` is sufficient — purely additive UI, no data written or altered.

## 9. Verification performed

- [x] Automated tests run: `npx vitest run src/__tests__/dashboard/pages.smoke.test.tsx -t legacy` — 3 passed (one per page).
- [x] **Real production build run**: `npm run build` — succeeded, all three legacy routes listed in output.
- [x] `npx eslint` on all three changed files — clean, no warnings.
- [x] Blast-radius grep performed: no other file imports these page components or `buildSummaryText`.
- [x] Reviewed against CLAUDE.md conventions: additive-only, isolated blast radius, no PII added.
- [ ] Feature-flagged: not applicable — internal-admin-only UI addition.

## What was NOT verified

- Not tested against a live Supabase/live report data end-to-end from the
  browser in this session — verified by build + smoke test + code reading
  only. The Phase 1 page's summary button will get real-world exercise the
  next time an operator runs this report (this session's own live Phase 1
  import already completed before this change landed).
- No visual-regression tooling is active for admin-dashboard (standing gap,
  `ACTION_ITEMS.md` B38) — button placement/spacing was reasoned about
  (same `flex justify-end` + `Button variant="ghost" size="sm"` pattern used
  elsewhere in this codebase) rather than screenshotted.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`)
- [x] Blast radius is stated, not assumed (isolated to 3 files, each self-contained)
- [x] No silent behavior change to an already-shipped flow — purely additive, nothing existing removed or altered
