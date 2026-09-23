# PR 5722 next-stop route resolution

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-23 |
| Author | Codex |
| Surface(s) | backend |
| Domain (Sentry tag) | rides |
| PR / commit link | PR 5722 / pending |
| Related issue or gap ID | PR 5722 driver navigation review |

## Issue / gap identified

The live route and turn-by-turn endpoints always selected final dropoff while a ride was in progress, including when intermediate stops remained.

## Root cause

Both tracking endpoints chose their destination from ride status alone and shared a cache key for all dropoff route targets.

## Fix / remediation

Both endpoints now resolve the first uncompleted stop from the persisted stop array, use a route-specific cache key, and return an empty route/step set for malformed pending stops rather than silently routing past them. Final dropoff resumes after all stops are marked complete.

## Risk & impact on existing functionality

Blast radius: backend ride tracking APIs, read by the driver app and rider app. Readers checked: `driver-app/app/driver/(tabs)/index.tsx`, `driver-app/lib/androidAuto/useCarLiveRoute.ts`, and rider trip tracking usage of `/live-route`; response destination labels remain `pickup`/`dropoff`, and only the target coordinates/cache key change when a stop remains. No state transition, fare, or background process changes.

## User-experience effect

Drivers see route line/ETA/instructions to the next stop. Riders continue to see ride tracking; the live line also follows the same next stop. An invalid pending stop produces no route until corrected instead of showing a misleading final-dropoff route.

## Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/rides/tracking.py` | Added first-uncompleted-stop destination selection and target-specific cache keys | Route the active leg correctly |
| `backend/tests/test_route_next_stop.py` | Added live-route and turn-step destination regressions, including malformed-stop fail-closed coverage | Pin the backend contract |
| `docs/change-log/2026-09-23-pr5722-next-stop-routing.md` | Recorded impact and verification | Live-tested change log |

## Before / after

```py
# Before
dest_lat, dest_lng, destination = ride.get("dropoff_lat"), ride.get("dropoff_lng"), "dropoff"
```

```py
# After
dest_lat, dest_lng, destination, route_key = _ride_destination(ride)
```

## Rollback plan

Reverting this additive route-target resolution restores final-dropoff routing. Cache keys are ephemeral and expire; no data rollback or migration is needed.

## Verification performed

- [ ] Automated tests run (shared Python environment now available; run before final commit)
- [x] Blast-radius grep performed for `/live-route` and `/navigation-steps` consumers
- [x] Reviewed route cache keys and verified the external response labels remain compatible

## What was NOT verified

No live Supabase or staging route session was used. No mobile visual regression tooling exists for this app; the map destination update is reasoned about and covered with route contract tests, not screenshots.
