# PR 5722 driver stop progress API

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

The driver had no persisted way to mark an intermediate stop complete, and rider edits could race with driver progress writes.

## Root cause

The `rides.stops` JSON stored only address and coordinates; add/remove routes performed unguarded read/modify/write operations.

## Fix / remediation

Added a driver-only completion endpoint that persists `completed: true` and stable IDs using compare-and-swap on ride status and the exact stop array. Rider add/remove writes now use the same route-version guard and return 409 on a concurrent edit. Stop coordinates are validated before insertion or completion.

## Risk & impact on existing functionality

Blast radius: single-surface backend ride routes. Readers/writers checked: `backend/routes/rides/stops.py`, `backend/routes/drivers/ride_reads.py`, and `backend/routes/drivers/ride_complete.py`. The driver active-ride endpoint serializes stops; the parent agent is adding the completion gate before settlement side effects. A stale rider edit may now receive 409 and must refresh; this prevents overwriting stop progress. No background loop, ride status transition, or money calculation changes in this commit.

## User-experience effect

Drivers gain a persisted stop completion API used by the follow-up panel change. Riders may see a conflict response only when simultaneous edits overlap; no copy or notification changes here. Existing sessions can use the additive stop fields without a migration.

## Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/rides/stops.py` | Added guarded stop completion, coordinate validation, and CAS rider edits | Persist ordered progress safely |
| `backend/tests/test_driver_stop_progress.py` | Added success, authorization, and concurrent-route regressions | Pin endpoint contract |
| `docs/change-log/2026-09-23-pr5722-driver-stop-progress-api.md` | Recorded impact and verification | Live-tested change log |

## Before / after

```py
# Before: no driver stop progress write existed.
```

```py
# After: POST /rides/{ride_id}/stops/{stop_index}/complete persists the
# completion only if the exact stop route version read by the driver still exists.
```

## Rollback plan

The endpoint is additive and the JSON fields are optional. Reverting the code removes the new route behavior; `completed` and `id` fields can remain harmlessly in stop JSON and do not require data rollback.

## Verification performed

- [ ] Automated tests run (awaiting shared Python test environment)
- [x] Blast-radius grep performed for stop readers/writers and ride completion
- [x] Reviewed exact JSON compare-and-swap behavior and PIPEDA constraints; no addresses or coordinates are logged
- [x] Change is additive and backward-compatible for old stop records

## What was NOT verified

No live Supabase write or staging driver session was performed. Driver-app visual regression tooling is absent; the later panel change will be reasoned about and covered with component tests, not screenshots.
