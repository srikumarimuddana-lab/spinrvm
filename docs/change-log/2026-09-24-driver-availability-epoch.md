# Driver availability epoch (v2 backend)

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-24 |
| Author | Claude |
| Surface(s) | backend, driver-app, shared, migrations |
| Domain (Sentry tag) | availability |
| PR / commit link | PR #5748 (continues #5727) |
| Related issue or gap ID | Driver availability v2 — epoch-fenced transitions |

## 1. Issue / gap identified

Spinr's driver availability model (v1) uses a single boolean `is_available` with no epoch or session fencing. Concurrent requests (e.g. reconnect vs. admin block) can race, and there is no structured presence isolation or snapshot assembly for the dispatch pipeline.

## 2. Root cause

The v1 model was designed for a single-driver prototype. It lacks epoch fencing, session authority, presence isolation, and a composable availability snapshot for dispatch.

## 3. Fix / remediation

Migration 457 adds epoch columns (`availability_epoch`, `availability_state`, `availability_updated_at`, `availability_session_id`, `availability_deadline`, `availability_reason`, `availability_prev_state`) to `drivers`, a new `driver_availability_requests` table, and a `driver_availability_v2_enabled` flag in `settings` (default false). New backend services (`driver_availability_service.py`, `driver_availability_repo.py`, `driver_presence_repo.py`) implement epoch-fenced transitions, presence isolation, and snapshot assembly. All v2 code paths are guarded by the feature flag; v1 behavior is unchanged when the flag is off.

## 4. Risk & impact on existing functionality

- Blast radius: `backend/routes/drivers/status.py` (status endpoint, v2 branch behind flag), `backend/migrations/457_driver_availability_epoch.sql` (additive schema — new columns and table only, no existing column changes), new repository and service files, driver-app wire parsing utility, shared types.
- Existing `is_online`/`is_available` columns and all v1 dispatch, ride, insurance, and wallet paths are unchanged.
- Migration is append-only: adds columns with defaults and a new table. Old backend reads/writes work against the new schema without modification.
- No background loop, fare, payment, or corporate billing change.

## 5. User-experience effect

No user-visible change when the flag is off (default). When enabled, drivers see epoch-fenced availability states and readiness deadlines. No rider-facing or admin-facing changes in this PR.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/457_driver_availability_epoch.sql` | Adds 7 columns to `drivers`, `driver_availability_requests` table, settings flag, indexes, RLS | Schema foundation for v2 availability |
| `backend/routes/drivers/status.py` | v2 branch in status endpoint behind feature flag | Serve v2 availability state when flag is on |
| `backend/repositories/driver_availability_repo.py` | New repo for availability CRUD | Data access layer for v2 transitions |
| `backend/repositories/driver_presence_repo.py` | New repo for Redis presence | Presence isolation for v2 |
| `backend/services/driver_availability_service.py` | Epoch-fenced transition logic, snapshot assembly | Core v2 business logic |
| `backend/utils/driver_presence.py` | Presence helpers | Shared presence utilities |
| `shared/types/driverAvailability.ts` | v2 wire types | Type-safe v2 contract |
| `driver-app/utils/driverAvailabilityWire.ts` | Wire parsing utility | Parse v2 responses in driver app |

## 7. Before / after

```python
# Before (v1)
await supabase.table("drivers").update({"is_available": True}).eq("id", driver_id).execute()
```

```python
# After (v2, behind flag)
await driver_availability_service.transition(
    driver_id=driver_id,
    target_state="available",
    session_id=session_id,
    expected_epoch=current_epoch,
)
```

## 8. Rollback plan

1. Set `driver_availability_v2_enabled = false` in settings (immediate, no deploy).
2. Revert the PR (`git revert`).
3. Follow-up migration to drop new columns and table if desired (optional — they are inert when flag is off).

No data cleanup needed: v1 columns remain authoritative when flag is off.

## 9. Verification performed

- [x] Automated tests run: `test_driver_availability_epoch.py`, `test_driver_availability_presence_epoch.py`, `test_driver_availability_redis.py`, `test_driver_availability_snapshot.py`, `test_driver_presence_epoch.py`, `test_driver_presence_scoped_readers.py` — all passing in CI.
- [x] Blast-radius grep performed: `driver_availability_v2_enabled` flag guards all v2 code paths; no v1 path modified.
- [x] Feature-flagged: `settings.driver_availability_v2_enabled` (default false). All new behavior is dark.
- [x] Migration tested: 457 applies cleanly, adds columns with defaults, creates table with RLS, indexes verified.
- [ ] Manual staging repro: not run; flag is off in all environments.

## 10. Sign-off

- [x] Rollback plan is concrete and testable.
- [x] Blast radius is stated.
- [x] No notification or copy change.
