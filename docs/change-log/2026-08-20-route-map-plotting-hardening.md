# Route map plotting hardening — "never show a missing path"

**Date:** 2026-08-20
**Surface:** backend rides geometry + driver-app + rider-app (live-tested), 4 commits
**Trigger:** owner report with screenshots of two 2026-08-19 Regina live-test rides whose maps were not populated properly, and the explicit directive: gap bridging "need[s] to be hardened in all edge cases and never show missing path."

## Issue/gap identified
Two plotting failures on real rides. (1) SPR-YDEBCH (7:09 PM): GPS capture died mid-trip (11-minute hole; the completion fix was never uploaded) and the route finalizer only ran gap-bridging reconstruction when a completion fix existed — so it skipped reconstruction entirely and published bare observed fragments ("Route incomplete · 10% GPS coverage", a map of dashes). (2) SPR-JE4G7T (6:46 PM): 20 transient OSRM chunk-match failures made whole observed segments plot as raw jagged GPS instead of road-snapped geometry. Additionally, a v2 ride with no drawable route rendered an *empty* map, and none of the tracking-overhaul rollout flags were actually flippable from the admin dashboard (silently dropped by `SettingsUpdateRequest`'s `extra="ignore"`).

## Root cause
The reconstruction entry gate required a *recorded* completion fix even though the start anchor has always been the *booked* pickup; a single OSRM chunk failure downgraded its whole segment with no retry; the clients had no display fallback for a v2 ride without a complete actual route; the rollout flags were added as settings columns but never to the admin update model.

## Fix/remediation (by commit)
1. `f4e29bf` — finalizer: when the completion fix is missing but observed GPS exists, anchor the reconstruction tail to the **booked dropoff** (flag `route_booked_dropoff_anchor_enabled`, migration 349, default off). Internal gaps, missing starts, and missing tails all bridge with road-following inferred segments; provenance recorded in `route_quality.completion_anchor_source` (`completion_fix` | `booked_dropoff`); zero-evidence rides still never fabricate an "actual route".
2. `709772b` — admin settings: all seven tracking rollout flags + `idle_breadcrumb_retention_hours` (bounded 24–2160 h) wired into `SettingsUpdateRequest` so the dashboard can actually flip them (found by the migration-349 review).
3. `9901266` / `a898f04` — driver-app ride-detail + rider-app ride-details/ride-completed: booked-route **dashed grey underlay** whenever a v2 route is processing, incomplete, or has no drawable sections; label reads "Booked route" when nothing observed exists; camera fit includes the underlay; v2 rides still never feed planned coords into the solid gradient line.
4. (in commit `f4e29bf`'s follow-up file set) — `route_distance.py`: one bounded retry per failed OSRM chunk before the Google Roads / raw fallback.

## Risk & impact on existing functionality
- **`route_finalizer.finalize_route` (all consumers: finalizer loop, late-tail revisions via `mark_route_pending`, admin re-queue):** flag off → byte-for-byte legacy behavior (pinned by test). Flag on → rides that previously published fragments become `complete` with reconstructed geometry; their `_recompute_ride_distance_stats` then runs — the measured-distance resolver's coverage floor (`route_min_observed_coverage_ratio`, 0.6) still routes low-coverage rides to the `planned_estimated` basis, so **fares are never touched and low-coverage measured distances still fall back to the booked estimate** (this is the existing incident-hardened path, now reachable for missing-fix rides; it also makes the stats tile agree with the fare line's km for those rides).
- **Insurance/audit surfaces:** segments keep `geometry_kind` observed/inferred; `missing_tail`, coverage, and the new `completion_anchor_source` stay in `route_quality`; `driver_period_distances` revisions unchanged. Bridged tails are labeled inferred — the insurer never sees a booked-dropoff bridge presented as GPS evidence.
- **`compute_segmented_road_route` callers (finalizer only):** retry adds ≤1 extra OSRM call + 0.25 s per failed chunk in a background loop — no request-path latency.
- **Client blast radius:** the three ride screens each gain one raw `Polyline`; the shared `RouteLine`/`RoutePins`/`routeSegments` are untouched, so live map screens (driver-arriving, ride-in-progress, dashboards, Android Auto) are unaffected. Contract tests re-pinned to two sanctioned Polylines per screen.
- **Admin settings:** additive optional fields; omitted fields still mean "leave unchanged"; none are credentials or super-admin-gated; retention hours bounds (24–2160) prevent a save from configuring the purge to destroy finalizer evidence.

## User experience effect
Driver- and rider-visible on already-shipped ride-detail screens, **only after the next app build** (no OTA): rides whose route is missing/incomplete show a dashed grey booked-route line instead of fragments or an empty map, with the existing honest labels ("Booked route", "Route incomplete · X% GPS coverage"). Backend-side, once `route_booked_dropoff_anchor_enabled` flips on, newly finalized (and late-tail-revised) rides get a continuous reconstructed line labeled "Route reconstructed · X% GPS observed · Y% inferred" — the Uber/Lyft-style always-continuous plot, with Spinr's honesty labels kept. Historical rides are display-fixed by the client underlay. Not visible mid-session to anyone on the current build.

## Files modified
| file | what changed | why |
|---|---|---|
| `backend/utils/route_finalizer.py` | booked-dropoff tail-anchor fallback + `completion_anchor_source` in quality | run reconstruction in every edge case with evidence |
| `backend/migrations/349_route_booked_dropoff_anchor_flag.sql` | new flag column, default false | ship dark, flip without redeploy |
| `backend/utils/route_distance.py` | one bounded OSRM chunk retry | fewer raw-jagged fallback segments |
| `backend/routes/admin/settings.py` | rollout flags + retention hours on `SettingsUpdateRequest` | make the flag flips operable from the dashboard |
| `driver-app/app/driver/ride-detail.tsx` | dashed booked-route underlay, label, camera fit | never an empty/fragmented map |
| `rider-app/app/ride-details.tsx`, `rider-app/app/ride-completed.tsx` | same underlay pattern | same, rider side |
| `backend/tests/test_route_finalizer.py`, `backend/tests/test_route_distance_osrm.py`, `backend/tests/test_tracking_rollout_flags_settings.py`, app contract tests | new pins | lock the behavior |

## Before/after (behavior-changing)
Finalizer, ride with GPS but no completion fix (flag on):
```
before: reconstruction skipped -> observed fragments, status incomplete,
        "Route incomplete · 10% GPS coverage"
after:  reconstruct with tail anchored to booked dropoff -> continuous
        road-following line, status complete,
        "Route reconstructed · 24% GPS observed · 76% inferred",
        quality.completion_anchor_source = "booked_dropoff"
```
Client, v2 ride without a complete actual route:
```
before: fragments only, or an empty map
after:  dashed grey booked-route underlay beneath whatever was observed;
        label "Booked route · Actual route unavailable" when nothing observed
```

## Rollback plan
Backend behavior: flip `route_booked_dropoff_anchor_enabled` off in admin settings (no redeploy); migration 349 rollback is a one-line `DROP COLUMN` (in-file comment). OSRM retry: revert the commit (pure code, no data). Client underlay: ships only in the next app build; reverting is a code revert before the build, or a subsequent build after. Settings-model fields: additive; reverting restores the (broken) drop-on-save behavior only. No live-data mutation anywhere; already-finalized route rows are only ever superseded by normal revisioning.

## Verification performed
- New/updated suites: `test_route_finalizer.py` (16, incl. flag-off byte-parity, booked-dropoff anchoring, fix-wins-over-fallback, zero-evidence guard, settings-failure fail-safe), `test_route_distance_osrm.py` (retry recovers / bounded / Google fallback), `test_tracking_rollout_flags_settings.py` (round-trip, non-super-admin, omitted-field, retention bounds), driver + rider route contract tests re-pinned.
- Migration 349 reviewed by `spinr-migration-reviewer`: SAFE TO APPLY, zero blockers; its one warning (flags not flippable via admin) fixed in-batch.
- Full fast backend suite, full driver-app jest + `tsc --noEmit`, full rider-app jest + `tsc --noEmit` run before push (results in the PR/commit thread).
- **No real production build was run for driver-app/rider-app** (`eas build` unavailable in this environment) — jest + tsc only, stated per the release-gate rule.

## What was NOT verified
- Not run against live Supabase; PostgREST behavior mocked. Migration 349 dry-runs at deploy via `run_migrations.py --dry-run`.
- OSRM /match behavior with the booked-dropoff anchor exercised via mocks, not a live OSRM instance.
- No visual regression tooling exists for these surfaces (standing gap, ACTION_ITEMS.md) — the dashed-underlay rendering was reasoned about against the existing pickup-leg pattern, not screenshotted on a device.
- Historical rides' display fix requires the next app build; no OTA path was verified.
