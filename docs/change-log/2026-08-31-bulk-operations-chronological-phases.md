# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-31 |
| Author | Claude Code (session), owner-directed UX request |
| Surface(s) | admin-dashboard, backend |
| Domain (Sentry tag) | admin |
| PR / commit link | see branch `claude/migration-batch-readiness-wicr1d` |
| Related issue or gap ID | owner follow-up after PR #4815 — "add [Data Quality Scan] as step 17" + "arrange the flow in the admin portal" |

## 1. Issue / gap identified

Three related gaps on the Bulk Operations admin page: (1) the newly-shipped Migration Data
Quality Scan tool wasn't reflected in the Migration Checklist panel or `migration-tool-order.md`;
(2) the page had no UI section for that tool at all — it was only reachable indirectly via the
Rides page's "Needs Review" filter; (3) the page's own tool sections were **not** laid out in
their real chronological/dependency order (Route Snapshots and Route Backfill rendered *before*
Legacy Booking Import, even though they hard-require rides Booking Import writes), and nothing on
the page explained what each section does or why it's positioned where it is — an operator
working through a fresh export had to already know the order from a separate doc.

## 2. Root cause

The page grew incrementally across many sessions, each adding one new tool's section wherever it
fit in the file at the time, with no structural concept of "phase" or dependency order enforced in
the layout itself — only `docs/runbooks/migration-tool-order.md` (a separate doc) captured the
true order. The Data Quality Scan tool (PR #4815) shipped its backend Preview→Apply route but no
matching page section, and wasn't added to `migration_status_service.py`'s tool list.

## 3. Fix / remediation

- **Backend**: added `_tool_17_data_quality_scan()` to `migration_status_service.py` (previous
  commit on this branch) and a step-17 row in `migration-tool-order.md`.
- **New component**: `DataQualityScan.tsx`, mirroring `PreLaunchDataFlag.tsx`'s Preview→Apply
  pattern (no confirm-phrase gate — see the component's own docstring for why this one is
  lower-stakes than the pre-launch flag tool).
- **API client**: added `adminPreviewDataQualityScan`/`adminCommitDataQualityScan` to
  `lib/api/imports.ts`, re-exported via the `api.ts` barrel, matching every other tool's pattern.
- **Page reorg**: introduced a `PhaseSection` component grouping the 17 tools into 6 numbered
  phases matching `migration-tool-order.md`'s dependency structure (Bring in people → Enrich
  driver profiles → Link payment identities → Import trip history → Finish the ride records →
  Final review). Each phase renders a heading + one-paragraph overview of what it does and why it
  comes where it does, then either the on-page tool cards (unchanged internals — only their
  position moved) or link-out cards for tools that live on other pages. This replaces the old flat
  list of ad hoc muted-text labels with one consistent, ordered structure, and physically
  reorders Route Snapshots/Backfill to *after* Legacy Booking Import (previously reversed).
- Flagged, not silently worked around: Bulk Driver Tax-ID Import (step 9) has no dedicated admin
  page yet (API-only per `migration-tool-order.md`) — Phase 3 now says so explicitly instead of
  linking to a page that doesn't exist.

## 4. Risk & impact on existing functionality

- **Blast radius**: isolated to the Bulk Operations page and its two new backend additions
  (step-17 status function, already covered in the prior commit's Change Impact Log). No existing
  tool's internal logic, state, or API calls changed — `RiderImportSection`,
  `SnapshotRegenerateSection`, `RouteRegenerateSection`, `LegacyBookingImport`,
  `RiderCreatedAtBackfillSection`, `LegacyWalletImport`, and `PreLaunchDataFlag` are the exact
  same components, called exactly once each (verified via grep — see Verification below), just
  rendered inside a `<PhaseSection>` wrapper instead of a flat `<div>` label.
- **Grepped every render call site** of the 8 moved components to confirm no duplicate renders
  were introduced and none were dropped.
- Reordering Route Snapshots/Backfill to *after* Legacy Booking Import is itself a real (desired)
  behavior-adjacent change: an operator following the page top-to-bottom will now naturally run
  tools in the order the underlying services actually require, instead of hitting Snapshots first
  with nothing yet imported for it to snapshot.

## 5. User-experience effect

- Internal admin only (super_admin-gated page). Visible on next page load: 6 numbered phase
  headings replace the old flat list of muted-text labels; a new "Final review" phase includes
  the Data Quality Scan tool's own card; off-page tools (SIN/DOB backfill, vehicle-history
  backfill, etc.) now render as a small linked card with a one-line note instead of being buried
  in the intro paragraph's inline prose.
- Not mid-session-visible to any rider/driver — this surface has no live end-user on it.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/bulk-operations/_components/DataQualityScan.tsx` | New | Preview→Apply UI for the step-17 tool, previously only reachable via the Rides page filter |
| `admin-dashboard/src/lib/api/imports.ts` | Added Data Quality Scan API client functions/types | Matches the tool's two new backend endpoints |
| `admin-dashboard/src/lib/api.ts` | Re-exported the new functions/types | Barrel-file convention this codebase uses |
| `admin-dashboard/src/app/dashboard/bulk-operations/page.tsx` | Added `PhaseSection` component; reorganized all sections into 6 numbered phases; reordered Route Snapshots/Backfill after Legacy Booking Import; removed duplicate `RiderImportSection` call | Chronological, self-explaining flow per the owner's request |
| `admin-dashboard/src/app/dashboard/bulk-operations/_components/MigrationChecklist.tsx` | "16 tools" → "17 tools" copy | Step 17 now exists |

## 7. Before / after

```
# Before -- flat, ad hoc order (Snapshots/Backfill rendered BEFORE Booking Import,
# which they actually depend on)
<MigrationChecklist />
<div>Stripe Mapping Import label</div>
<Card>...Stripe flow...</Card>
<div>Imported Ride Snapshots label</div>
<SnapshotRegenerateSection />
<div>Imported Ride Routes label</div>
<RouteRegenerateSection />
<RiderImportSection />
<RiderCreatedAtBackfillSection />
<div>Legacy Booking Import label</div>
<LegacyBookingImport />
...
```

```
# After -- 6 numbered phases matching migration-tool-order.md, each with a
# heading + overview; Booking Import now precedes the route tools that need it
<MigrationChecklist />
<PhaseSection phase={1} title="Bring in people" overview="...">
  {offPage links to Bulk/Legacy Driver Import}
  <RiderImportSection />
</PhaseSection>
<PhaseSection phase={3} title="Link payment identities" overview="...">
  ...Stripe flow...
</PhaseSection>
<PhaseSection phase={4} title="Import trip history" overview="...">
  <LegacyBookingImport />
  <RiderCreatedAtBackfillSection />
  <LegacyWalletImport />
</PhaseSection>
<PhaseSection phase={5} title="Finish the ride records" overview="...">
  <SnapshotRegenerateSection />
  <RouteRegenerateSection />
</PhaseSection>
<PhaseSection phase={6} title="Final review" overview="...">
  <PreLaunchDataFlag />
  <DataQualityScan />
</PhaseSection>
```

## 8. Rollback plan

`git-revert-safe` — pure layout/component reorganization, no data touched, no API contract
changed for any existing endpoint. A revert restores the prior flat layout exactly.

## 9. Verification performed

- [x] Automated tests: `ruff check` clean on backend (prior commit); `eslint --max-warnings 1751`
      clean (0 errors) on all changed frontend files; 27/27 admin-dashboard vitest smoke tests
      passing. **Real production build performed**: `npm run build` (not just `next dev` or
      `tsc --noEmit`).
- [x] Blast-radius grep performed: every render call site of the 8 relocated components, confirmed
      exactly one call site each post-reorg (listed in §4).
- [x] Manual repro: not against a live staging environment (none available to this session) —
      reasoned through the JSX structure and confirmed via the real build + lint + smoke suite.
- [ ] Feature-flagged: not flagged — pure layout reorganization on an already-admin-gated page,
      no new capability exposed to any role that didn't already have it.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (§8)
- [x] Blast radius is stated, not assumed (§4)
- [x] No silent behavior change without the UX field filled in (§5) — the one real behavior change
      (tool ordering) is called out explicitly as the intended fix.

## What was NOT verified

- No screenshot/visual check — no browser access in this session; relied on the real production
  build succeeding, ESLint passing, and the vitest smoke suite passing instead. No active visual
  regression tooling exists for admin-dashboard (`ACTION_ITEMS.md` B38, baselines not yet seeded).
- The Data Quality Scan tool's live behavior (Preview/Commit) was tested at the service/route
  level in the prior commit, not end-to-end through this new UI card — no live backend write
  credentials in this session, same limitation as every other importer this migration effort
  shipped.
