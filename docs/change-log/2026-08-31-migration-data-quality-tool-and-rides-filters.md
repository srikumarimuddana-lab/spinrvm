# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-31 |
| Author | Claude Code (session), owner-directed investigation |
| Surface(s) | backend, admin-dashboard |
| Domain (Sentry tag) | rides |
| PR / commit link | see branch `claude/migration-batch-readiness-wicr1d` |
| Related issue or gap ID | owner spot-check of Admin → Rides (screenshots), 2026-08-31 |

## 1. Issue / gap identified

Two separate things, found while investigating an owner-reported screenshot of completed rides
showing a missing Driver or Rider column: (a) 11 of 261 completed rides in production genuinely
have a null `driver_id` or `rider_id`, with no way to find or triage them from the admin UI; (b)
the existing "No Driver Found" tab on Admin → Rides is dead code — it never sends a status filter
to the backend, so selecting it silently shows the same rows as "All".

## 2. Root cause

(a) `booking_import_service.py`'s `_match_rider_driver` resolves both sides of a legacy booking by
phone number against `users`/`drivers` at import time; if one side's phone never matched anyone
already imported, that side is left `NULL` rather than dropping the whole row (only a row missing
*both* sides is skipped, since nobody could see it). This is expected import behavior, not
corruption — confirmed via a direct duplicate check (0 duplicate phone/license/old-system-ID
across all 910 drivers / 1,938 users) — but there was no admin-facing way to find these 11 rows.

(b) `admin-dashboard/.../rides/page.tsx` builds its API request from the active tab and explicitly
excludes `no_driver_found` from ever setting the `status` query param (`opts.tab !== "all" &&
opts.tab !== "no_driver_found"` guarded the only place `status` got set), with no client-side
post-filter anywhere to fill the gap. The tab's real intended meaning — migration 38's
`cancellation_type = 'no_drivers_found'` column, set by the dispatch offer-timeout auto-cancel
handler in `routes/rides/matching.py` — was never wired up on either side.

## 3. Fix / remediation

- New Preview→Apply admin tool (`migration_data_quality_service.py` +
  `routes/admin/migration_data_quality.py`) that scans completed rides for four categories —
  missing driver, missing rider, a placeholder address, or \$0.00 fare — and additively tags each
  onto `legacy_import_metadata.data_quality.issues`. Never touches `rides.status`; see
  `docs/runbooks/migration-data-quality-strategy.md` for the full root-cause reasoning per
  category and why status reclassification was rejected in favor of an additive flag.
- `_build_rides_filters` in `routes/admin/rides.py` now translates two synthetic `status` tab
  values instead of passing them through as a raw (and always-empty) status equality:
  `no_driver_found` → `status=cancelled AND cancellation_type=no_drivers_found` (the real,
  previously-unwired live dispatch signal); `needs_review` → an `$in` filter over
  `fetch_needs_review_ride_ids()` (the new import-quality signal). The two compose correctly with
  the existing `pre_launch` filter via set intersection/subtraction rather than one clobbering the
  other's `id` filter key.
- `admin-dashboard`: added a "Needs Review" tab, removed the now-unnecessary `no_driver_found`
  exclusion in `page.tsx` (both call sites — the main list load and the CSV export path), and
  added a small amber sub-badge per row (`No driver` / `No rider` / `No address` / `$0 fare`) next
  to the existing "Imported" badge so an admin can tell which issue(s) a flagged row has without
  opening the detail modal.

## 4. Risk & impact on existing functionality

- **`legacy_import_metadata` on `rides`**: already has multiple read-merge-write backfills
  touching it (`legacy_gst_backfill_service.py`, `booking_import_service
  .apply_duration_estimated_backfill`, `pre_launch_flag_service.py`). The new tool uses the exact
  same whole-column optimistic-concurrency guard (re-read immediately before write, guard the
  `UPDATE` on the read snapshot) those already use, so it's safe alongside them — grepped for every
  other writer of this column; no new collision introduced.
- **`_build_rides_filters`**: shared by `GET /admin/rides` and `GET /admin/rides/export`. Grepped
  for every caller of `fetch_pre_launch_flagged_ids` and the `status` query param across
  `admin-dashboard` — only the Rides page and its CSV export consume this route; no other admin
  page sends `status=no_driver_found` or a `needs_review` value today, so nothing else changes
  behavior. A search UI that happened to hardcode `status=no_driver_found` expecting the old
  (broken, all-rows) behavior would now see a narrower, correct result set — this is the intended
  fix, not a regression, but noted since it's a real behavior change to what that param returns.
- **Blast radius**: isolated to the Rides admin page and its two new backend routes/service. No
  ride-state-machine transition, no money/wallet delta, no insurance-period write.

## 5. User-experience effect

- Internal admin only. Two admin-dashboard changes are visible immediately on next page load of
  Admin → Rides: the "No Driver Found" tab now actually filters (previously showed everything),
  and a new "Needs Review" tab appears. Not mid-session-visible to any rider/driver — this surface
  has no live user on it.
- The data-quality tags themselves are not applied to any row until a super_admin runs the new
  tool's Preview → Commit from the admin UI (this session has no production write credentials —
  same limitation as every other importer built this migration effort).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `docs/runbooks/migration-data-quality-strategy.md` | New runbook | Root-cause categories, detection queries, handling policy per category |
| `backend/services/migration_data_quality_service.py` | New | Scan/apply/fetch-flagged three-function service, mirrors `pre_launch_flag_service.py` |
| `backend/routes/admin/migration_data_quality.py` | New | Preview/commit admin route, super_admin only |
| `backend/routes/admin/__init__.py` | Wired new router | Same `require_super_admin` boundary as the other bulk-write importers |
| `backend/utils/rate_limiter.py` | Two new rate limiters | Matches the pre-launch-flag tool's generous small-dataset headroom |
| `backend/tests/test_migration_data_quality_service.py` | New, 18 tests | Detection per category, multi-issue merge, re-plan idempotency, concurrency guard |
| `backend/tests/test_admin_migration_data_quality.py` | New, 7 tests | HTTP-layer: super-admin boundary, preview never writes, commit idempotency |
| `backend/routes/admin/rides.py` | `_build_rides_filters` translates `no_driver_found`/`needs_review` | Real semantics instead of a silent no-op |
| `backend/tests/test_admin_rides_coverage.py` | 6 new tests | Translation correctness, empty-set handling, pre_launch composition |
| `admin-dashboard/src/app/dashboard/rides/page.tsx` | Removed `no_driver_found` status exclusion (2 call sites) | Value now flows through like any other tab |
| `admin-dashboard/src/app/dashboard/rides/_components/ride-list.tsx` | Added "Needs Review" tab, fixed "No Driver Found" tab's comment/intent, added per-row issue sub-badges | Surfaces the new filter and makes flagged rows self-explanatory |

## 7. Before / after

```
# Before (page.tsx, both call sites) -- the exclusion silently dropped the status filter
} else if (opts.tab !== "all" && opts.tab !== "no_driver_found") {
    apiOpts.status = opts.tab;
}
```

```
# After -- no_driver_found and needs_review flow through like any other tab;
# the backend now translates them (see routes/admin/rides.py diff below)
} else if (opts.tab !== "all") {
    apiOpts.status = opts.tab;
}
```

```
# Before (rides.py) -- a raw, always-empty equality filter
if status:
    filters["status"] = status
```

```
# After
if status == "no_driver_found":
    filters["status"] = "cancelled"
    filters["cancellation_type"] = "no_drivers_found"
elif status == "needs_review":
    needs_review_ids = fetch_needs_review_ride_ids()
    filters["id"] = {"$in": list(needs_review_ids)}
elif status:
    filters["status"] = status
```

## 8. Rollback plan

- Frontend/backend filter fix: plain `git revert` — no data touched, purely additive/corrective
  filter logic. The "No Driver Found" tab would return to its prior (broken, all-rows) behavior,
  which is a regression to a known-bad state but not a new failure mode.
- Data-quality tool: additive-only, reversible without a second deploy — a tagged row's
  `legacy_import_metadata.data_quality` key can be cleared with a plain `UPDATE ... SET
  legacy_import_metadata = legacy_import_metadata - 'data_quality'`, same as the runbook's §2
  documents. No `rides.status`, driver/rider assignment, or fare figure is ever touched by this
  tool, so there is nothing money- or state-machine-adjacent to unwind.

## 9. Verification performed

- [x] Automated tests run: 18 new unit tests (`test_migration_data_quality_service.py`), 7 new
      route tests (`test_admin_migration_data_quality.py`), 6 new + 110/110 passing in the full
      `test_admin_rides_coverage.py` file, 27/27 passing admin-dashboard smoke tests. `ruff check`
      clean on all changed backend files. **Real production build performed**: `npm run build`
      (not just `next dev` or `tsc --noEmit`) — see build log; ✅ passing on completion.
- [x] Manual repro steps followed: not against a live staging environment (none available to this
      session) — verified via the investigation's direct production Supabase read queries
      (duplicate checks, the 11-row missing-driver/rider set, the payout/fare correlation check).
- [x] Blast-radius grep performed: every caller of `_build_rides_filters`,
      `fetch_pre_launch_flagged_ids`, and every writer of `rides.legacy_import_metadata` — listed
      in §4.
- [x] Reviewed against relevant CLAUDE.md convention: additive-over-destructive (never touches
      `rides.status`), Preview→Apply pattern, dual-import pattern, query-filter-DSL rules
      (`$in`/`$nin` id-resolution instead of an unsupported JSONB-path operator, matching the
      existing `pre_launch` filter's own precedent).
- [ ] Feature-flagged: not flagged — this is an admin-only, opt-in-by-clicking-the-tab surface
      with no default-on behavior change for anyone not actively using the new tab/tool.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (§8)
- [x] Blast radius is stated, not assumed (§4)
- [x] No silent behavior change to an already-shipped flow without the UX field filled in (§5) —
      the one real behavior change ("No Driver Found" now actually filters) is called out
      explicitly as the intended fix, not an accidental side effect.

## What was NOT verified

- Not tested against a live Supabase branch/staging environment — this session has read-only
  Supabase MCP access and no backend service-role write credentials, so the actual tagging run
  (Preview → Commit) has not been executed against production. The tool ships built and tested;
  a super_admin runs it via the deployed admin dashboard, same as every other importer this
  migration effort shipped.
- The `$0.00`-fare cluster (7 rides, May 13–18, 2026) is flagged as *suspected* test/comp data
  based on clustering and the fact both driver and rider matched cleanly — not confirmed against
  the raw old-system export, since this session doesn't have those files. If the owner provides
  them, that classification should be verified rather than assumed.
- No visual regression tooling exists for admin-dashboard's currently-unseeded Playwright baseline
  (`ACTION_ITEMS.md` B38) — the new "Needs Review" tab and row sub-badges were verified via the
  vitest smoke suite and a real `npm run build`, not screenshotted.
