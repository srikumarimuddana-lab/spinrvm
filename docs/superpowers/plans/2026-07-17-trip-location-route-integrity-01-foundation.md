# Foundation and Ingestion Tasks

Required context: read the [master plan](2026-07-17-trip-location-route-integrity.md) and approved [design](../specs/2026-07-17-trip-location-route-integrity-design.md). Execute Tasks 1–5 in order.

### Task 1: Shared route API contract

**Files:** Create `shared/types/api/route.ts`; modify `shared/types/api/ride.ts`, `shared/types/index.ts`.

**Produces:** `RouteCoordinate`, `ActualRouteSegment`, `RouteQuality`, `RouteGeometryStatus`, and optional v2 fields on `Ride`.

- [ ] Add `import type { ActualRouteSegment, RouteGeometryStatus, RouteQuality } from './route';` and the v2 fields to `Ride` before creating `route.ts`.
- [ ] Run `npx tsc --project shared/tsconfig.build.json --noEmit`; expect `Cannot find module './route'`.
- [ ] Create the contract:

```ts
export type RouteCoordinate = readonly [lat: number, lng: number];
export type RouteGeometryStatus = 'pending' | 'processing' | 'complete' | 'incomplete' | 'failed';
export interface ActualRouteSegment {
  id: string;
  points: RouteCoordinate[];
  captured_from: string;
  captured_to: string;
  source: 'observed' | 'osrm' | 'google_roads';
  confidence: 'high' | 'medium' | 'low';
}
export interface RouteQuality {
  confidence: 'high' | 'medium' | 'low';
  coverage_pct: number;
  covered_seconds: number;
  lifecycle_seconds: number;
  max_gap_seconds: number;
  missing_tail: boolean;
  completion_distance_m: number | null;
  incomplete_reason?: string;
}
```

- [ ] Add to `Ride`: `actual_route_segments?`, `route_quality?`, `route_revision?`, `route_snapshot_url?`, and `route_geometry_status?`; export `./api/route` from `types/index.ts`.
- [ ] Re-run the typecheck; expect PASS. Commit: `feat(shared): define segmented route contract`.

### Task 2: Additive database schema

**Files:** Create `backend/migrations/235_trip_location_route_integrity.sql`, `backend/tests/test_trip_location_route_migration.py`.

**Produces:** Idempotent point identity, finalizer state, v2 geometry, indexes, and shadow-mode settings.

- [ ] Write a failing SQL-contract test that asserts migration 235 contains the four-column unique index, `captured_at`, v2 segment columns, processing index, rollback comment, and settings defaults.
- [ ] Run `pytest backend/tests/test_trip_location_route_migration.py -q`; expect FAIL because migration 235 is absent.
- [ ] Create the migration with these core statements:

```sql
ALTER TABLE driver_location_history
  ADD COLUMN IF NOT EXISTS captured_at timestamptz,
  ADD COLUMN IF NOT EXISTS recording_session_id uuid,
  ADD COLUMN IF NOT EXISTS sequence_number bigint,
  ADD COLUMN IF NOT EXISTS monotonic_ms bigint,
  ADD COLUMN IF NOT EXISTS source text,
  ADD COLUMN IF NOT EXISTS is_completion_fix boolean NOT NULL DEFAULT false;
CREATE UNIQUE INDEX IF NOT EXISTS uq_dlh_ride_driver_session_sequence
  ON driver_location_history(ride_id, driver_id, recording_session_id, sequence_number);
CREATE INDEX IF NOT EXISTS idx_dlh_ride_captured
  ON driver_location_history(ride_id, captured_at, recording_session_id, sequence_number);

ALTER TABLE ride_routes
  ADD COLUMN IF NOT EXISTS route_schema_version integer NOT NULL DEFAULT 1,
  ADD COLUMN IF NOT EXISTS route_revision integer NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS processing_status text NOT NULL DEFAULT 'complete',
  ADD COLUMN IF NOT EXISTS observed_segments jsonb NOT NULL DEFAULT '[]'::jsonb,
  ADD COLUMN IF NOT EXISTS road_matched_segments jsonb NOT NULL DEFAULT '[]'::jsonb,
  ADD COLUMN IF NOT EXISTS completion_point jsonb,
  ADD COLUMN IF NOT EXISTS snapshot_revision integer NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS processing_claimed_at timestamptz,
  ADD COLUMN IF NOT EXISTS next_retry_at timestamptz,
  ADD COLUMN IF NOT EXISTS retry_count integer NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS finalized_at timestamptz;
CREATE INDEX IF NOT EXISTS idx_ride_routes_processing
  ON ride_routes(processing_status, next_retry_at, computed_at);

ALTER TABLE settings
  ADD COLUMN IF NOT EXISTS route_integrity_v2_mode text NOT NULL DEFAULT 'shadow',
  ADD COLUMN IF NOT EXISTS route_finalize_grace_seconds integer NOT NULL DEFAULT 60,
  ADD COLUMN IF NOT EXISTS route_location_gap_alert_seconds integer NOT NULL DEFAULT 30;
```

- [ ] Add checks limiting mode to `off|shadow|on`, non-negative sequence/retry values, and JSON arrays; include an explicit rollback plan.
- [ ] Run the migration test; expect PASS. Commit: `feat(db): add trip route integrity schema`.

### Task 3: Conflict-safe bulk database helper

**Files:** Modify `backend/repositories/_base.py`, `backend/db_supabase.py`; create `backend/tests/test_insert_many_ignore_conflicts.py`.

**Produces:** `insert_many_ignore_conflicts(table, docs, on_conflict) -> list[dict]`.

- [ ] Test that serialization occurs and Supabase receives `upsert(rows, on_conflict=..., ignore_duplicates=True)` once; test invalid docs raise `TypeError`.
- [ ] Run `pytest backend/tests/test_insert_many_ignore_conflicts.py -q`; expect import failure.
- [ ] Implement beside `insert_many`:

```py
async def insert_many_ignore_conflicts(table: str, docs: List[Dict[str, Any]], on_conflict: str):
    if not supabase or not docs:
        return []
    if not on_conflict.strip():
        raise ValueError("on_conflict is required")
    if any(not isinstance(doc, dict) for doc in docs):
        raise TypeError(f"insert_many_ignore_conflicts({table!r}) requires dict rows")
    rows = [_serialize_for_api(doc) for doc in docs]
    return await run_sync(lambda: _rows_from_res(
        supabase.table(table).upsert(rows, on_conflict=on_conflict, ignore_duplicates=True).execute()
    ))
```

- [ ] Re-export it in both import branches of `db_supabase.py`.
- [ ] Run the targeted test and `ruff check backend/repositories/_base.py backend/db_supabase.py`; expect PASS. Commit: `feat(db): support idempotent bulk inserts`.

### Task 4: Idempotent breadcrumb persistence

**Files:** Modify `backend/utils/breadcrumbs.py`, `backend/tests/test_breadcrumb_persistence.py`.

**Produces:** `LocationBatchAck`, internal `LocationBatchPersistResult`, and `persist_trip_location_batch(driver_id, ride_id, session_id, points, active_ride=...)`.

- [ ] Add failing tests for duplicate replay, capture-time preservation, server-derived phase, contiguous acknowledgement, permanent invalid-coordinate rejection, and no raw coordinates in logs.
- [ ] Run the six new tests; expect missing-symbol failures.
- [ ] Add frozen acknowledgement dataclasses and implement the v2 writer using `captured_at`, validated sequence identity, the existing ride-window/phase helpers, and:

```py
inserted = await db_supabase.insert_many_ignore_conflicts(
    "driver_location_history",
    rows,
    on_conflict="ride_id,driver_id,recording_session_id,sequence_number",
)
return LocationBatchPersistResult(
    ack=LocationBatchAck(
        recording_session_id=session_id,
        acked_through=max(p["sequence_number"] for p in points),
        accepted_count=len(rows),
        rejected=tuple(rejections),
    ),
    inserted_count=len(inserted),
)
```

- [ ] Preserve `persist_ride_breadcrumbs` for legacy clients; make it delegate when v2 identity is present. Do not overwrite anomalous `captured_at`; store `received_at` separately.
- [ ] Run `pytest backend/tests/test_breadcrumb_persistence.py -q` and Ruff; expect PASS. Commit: `feat(gps): persist acknowledged trip points idempotently`.

### Task 5: Acknowledged REST batch protocol

**Files:** Modify `backend/routes/drivers/location.py`, `backend/tests/test_location_batch.py`.

**Consumes:** `persist_trip_location_batch`. **Produces:** v2 request validation and `LocationBatchAck` JSON.

- [ ] Replace mirror-only tests with async endpoint tests covering v2 success, non-contiguous sequence 422, batch over 500 points 422, DB failure 503, and legacy `{points:[...]}` compatibility.
- [ ] Run `pytest backend/tests/test_location_batch.py -q`; expect FAIL because v2 requests return only `{"success": true}`.
- [ ] Add strict request models. Bind driver from auth and verify assignment. Accept active rides plus completed rides whose capture timestamps remain inside that ride’s lifecycle and 90-day raw-retention window; this is required for delayed offline outboxes. Call the v2 writer and return `result.ack.to_dict()`.
- [ ] Keep the last valid point as the live driver marker, but never acknowledge before persistence succeeds. Remove the current “log error and return success” behavior for v2 database failures.
- [ ] Run the targeted test and `ruff check backend/routes/drivers/location.py`; expect PASS. Commit: `feat(api): acknowledge durable location batches`.
