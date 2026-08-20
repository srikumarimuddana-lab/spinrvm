# Phase 1 — Period-1 idle capture, P2 pickup-leg geometry, period-distance revisions, recovery nudge

**Date:** 2026-08-18
**Surface:** backend rides/insurance/drivers + driver-app (live-tested surfaces), 8 commits
**Trigger:** tracking-overhaul roadmap Phase 1 (owner-approved). Distance Travelled/Logs needs per-phase evidence: Period-1 roaming was never durably captured, Period-2 pickup legs were computed but invisible, and late-arriving GPS could never correct the insurer audit.

## Issue/gap identified
Four gaps: (1) Period-1 ("driving around") GPS existed only as ephemeral WS pings — no durable capture, so per-day idle km/duration could not be shown or audited; (2) the actual pickup-leg route (Period 2) was discarded from ride geometry even when captured; (3) `driver_period_distances` was frozen at settlement — a late-uploaded GPS tail (SPR-PE7TTB's exact failure) could never correct the insurer numbers; (4) a driver whose recording died mid-trip got a WS-only nudge that a dead socket never delivered, and an abandoned trip left its Period-3 insurance span open forever.

## Root cause
The v2 durable outbox protocol was built ride-scoped only; geometry and period-distance writers were single-shot by design; recovery signaling assumed a live WebSocket.

## Fix/remediation (by commit)
1. `aa69af1` — migration 345 (full unique index on `(driver_id, recording_session_id, sequence_number)` + six settings flags, all default off) + `persist_idle_location_batch` + idle branch in `/drivers/location-batch` (v2 shape `{session_kind:'online_idle', recording_session_id, points}`, no ride_id; 409 terminal-drains when flag off/offline; feeds the P1 accumulator).
2. `80eda83` — migration 346: `revision`/`supersedes_id` columns on `driver_period_distances` (append-only corrections), CONCURRENTLY create-before-drop index swap, `driver_period_distances_current` view with `REVOKE ALL FROM anon, authenticated` (view RLS-bypass guard).
3. `8def30d` — route finalizer's re-derivation appends period-distance revisions (noise band: ≥0.05 km AND ≥2%); compliance/insurer reads move to the `_current` view with a `source` column.
4. `7127883` — flag-gated (`p2_route_geometry_enabled`) additive P2 pickup-leg projection: observed-only segments prepended for the admin surface; rider/driver projections filter to trip-phase unless `rider_show_pickup_leg_enabled`; main P3 pipeline untouched (quality/rejection unchanged).
5. `6432004` — gap-monitor recovery nudge gains a flag-gated FCM data-push fallback (`location_health_push_nudge_enabled`) so a dead-socket driver app can still self-heal; handler wired in driver-app background messaging.
6. `bb7a98f` — driver-app idle recording sessions through the SAME durable outbox (30 s/60 s cadence, 100 m displacement floor, `@idle` sentinel translated at flush; auto stop on ride start/offline; server flag consulted via `/settings`).
7. `55c46fd` — idle breadcrumb retention configurable (`idle_breadcrumb_retention_hours`, default 2160 h = 90 days, owner decision); the old hardcoded 24 h admin purge would have silently destroyed the new Distance Logs history.
8. `a5427cf` — `stale_p3_closer` loop: alerts on open Period-3 spans whose ride is terminal or long-abandoned; flag-gated autoclose (`stale_p3_autoclose_enabled`, default off) writes only the open span's `ended_at` at the evidence-based time.

## Risk & impact on existing functionality
- **`/drivers/location-batch` (dispatch-critical path)**: idle branch is dispatch-checked FIRST via `isinstance` — ride batches take the existing path byte-for-byte. The v1-shape guard (`_has_v2_markers`) keeps legacy WS-era pings out of the new P1 accumulator. Consumers grepped: `tripLocationRecorder.ts` (only writer), `breadcrumbs.py`, `trip_distance.py`, gap monitor.
- **`driver_period_distances` readers** (blast radius of migration 346): `routes/admin/compliance.py` (swapped to `_current` in the same deploy — **deploy coupling: 346 must apply before/with the reader swap**), `period1_distance_finalizer`, `route_finalizer`, data-transfer export. Old rows read as `revision=0` — view returns them unchanged, so pre-migration reports are byte-identical.
- **Ride geometry**: P2 projection is additive and observed-only; the P3 display/quality pipeline, rejection ladder, and `distance_km` writes are untouched (explicitly re-verified after an early draft widened the finalizer window — reverted).
- **Insurance table append-only rule**: revisions are new rows; the stale-P3 closer writes only `ended_at` on the OPEN row (the sanctioned closing mechanism), conditional on it still being open — concurrent transitions always win. It never mutates closed rows or ride state.
- **Background loops**: all three new loops (`period1_distance_finalizer`, `distance_reconciliation` — Phase 0, `stale_p3_closer`) are Redis-leader-locked, heartbeat every iteration, registered in `_WATCHDOG_LOOP_NAMES` + `LOOP_THRESHOLDS`; replay-safe by claim-or-conditional-write.
- **Retention**: idle rows still fall under the blanket 90-day `purge_pii_retention()` delete; the new step only acts when configured TIGHTER. One unbounded `delete_many` on first tightening — same shape as the existing admin endpoint.

## User experience effect
Nothing visible until flags flip. When enabled: drivers' idle roaming records silently (no UI); admins later get per-day Distance Travelled/Logs (Phase 3); a driver whose tracking dies mid-trip gets a silent recovery push instead of nothing. No rider-visible change (pickup-leg display stays off until `rider_show_pickup_leg_enabled`).

## Before/after (behavior-changing diffs)
Idle GPS, before → after:
```
before: WS ping → in-memory marker only; offline/app-kill = data never existed
after:  fix → SQLite outbox (durable) → batched upload → driver_location_history
        (tracking_phase='online_idle', ride_id NULL) → P1 accumulator → audit
```
Insurer numbers on late GPS, before → after:
```
before: driver_period_distances row frozen at settlement; late tail ignored
after:  late tail re-derives → INSERT revision=n+1 (supersedes_id=old) when
        |Δ| ≥ 0.05 km and ≥ 2%; billing reads driver_period_distances_current
```

## Rollback plan
Every behavior is dark behind its own `app_settings` flag (`idle_location_v2_enabled`, `p2_route_geometry_enabled`, `rider_show_pickup_leg_enabled`, `location_health_push_nudge_enabled`, `stale_p3_autoclose_enabled`, `period1_distance_tracking_enabled`) — flip off in the admin dashboard, no redeploy. Migration 345's index: `DROP INDEX CONCURRENTLY uq_dlh_session_sequence`. Migration 346: revision rows are additive; to revert readers, point compliance back at the base table (revision-0 rows are the pre-migration dataset). Idle retention: unset `idle_breadcrumb_retention_hours` (falls back to 90 d). Already-written idle/revision rows are data, not behavior — they can stay.

## Verification performed
- Backend: full fast suite (`pytest -m "not slow"`, ~12k tests) green at each commit; new suites `test_idle_location_batch.py` (8), `test_period_distance_revisions.py` (6), `test_stale_p3_closer.py` (9), `test_retention_purge.py` idle cases (4).
- Driver-app: full jest + `tsc --noEmit` green (idle-session describe in recorder tests; settings-flag effect in dashboard).
- Migrations 345/346 reviewed by `spinr-migration-reviewer`; both blockers fixed (CONCURRENTLY create-before-drop; view REVOKE) and re-verified.
- **No real production build was run for driver-app** (`eas build` not available in this environment) — jest + tsc only, stated per the release-gate rule.

## What was NOT verified
- Not tested against live Supabase (mocked PostgREST responses only); migration 345/346 dry-runs happen at deploy via `run_migrations.py --dry-run`.
- FCM data-push delivery on a real device (handler unit-tested; end-to-end nudge needs a staging device).
- PostgREST `on_conflict` behavior against the new full unique index exercised via mocks, not a live instance.
- No visual regression tooling exists for admin/rider surfaces (standing gap, ACTION_ITEMS.md) — P2 admin map rendering reasoned about, not screenshotted.
