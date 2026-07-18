# Trip Location and Route Integrity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Durably capture every delivered active-trip GPS fix, finalize a timestamp-ordered segmented route anchored to the completion location, and publish one truthful route revision to every map and receipt.

**Architecture:** The driver app writes foreground and background fixes into one SQLite outbox and deletes them only after idempotent REST acknowledgement. The backend stores raw breadcrumbs, orders them by sensor capture time, splits discontinuities before map matching, and asynchronously publishes versioned segments and snapshots. WebSockets remain an ephemeral live-marker channel; fare settlement never waits for map generation.

**Tech Stack:** Expo SDK 54, React Native, TypeScript, expo-location, expo-sqlite, FastAPI, Python 3.12, Supabase/Postgres, OSRM/Google Roads, Next.js 16, MapLibre, react-native-maps, Jest, Pytest, Vitest.

## Global Constraints

- Active-trip capture cadence is 4 seconds or 10 metres at high accuracy.
- `captured_at` is the immutable sensor timestamp and primary route order.
- `received_at` is diagnostic only and never replaces `captured_at`.
- Persisted identity is `(ride_id, driver_id, recording_session_id, sequence_number)`.
- Never delete an unacknowledged active-trip point because of retry count, logout, offline transition, or process restart.
- Never trust client-supplied driver identity or ride phase; derive both server-side.
- Raw GPS coordinates, exact addresses, and completion coordinates must not enter logs, metrics, analytics, or Sentry.
- Break routes for gaps over 60 seconds, gaps over 300 metres, implausible speed, timestamp regression, session discontinuity, or matcher split.
- Provider chunks contain at most 90 points plus 10-point overlap; never globally downsample the ride to 100 points.
- A solid actual-route line may contain only one continuous verified segment.
- Completion thresholds are 15-second fix age, 30-second tail age, and `max(75 m, final_accuracy + completion_accuracy + 25 m)`.
- Planned-dropoff checks are ≤200 m normal, 200 m–1 km confirmation, and >1 km strong confirmation plus reason.
- Raw breadcrumbs are retained 90 days; derived route geometry, completion point, quality, and snapshot are retained 3 years.
- Fare settlement and `in_progress -> completed` remain independent of route finalization.
- Every task touches no more than three files and ends with one logical commit.
- Use `Decimal` for money code, preserve the dual-import pattern, and never soften DB/auth/payment failures.

## Execution Documents

Execute the appendices strictly in this order:

1. [Foundation and ingestion](2026-07-17-trip-location-route-integrity-01-foundation.md)
2. [Mobile durable recorder](2026-07-17-trip-location-route-integrity-02-mobile.md)
3. [Route finalization and receipts](2026-07-17-trip-location-route-integrity-03-finalization.md)
4. [Consumer surfaces, retention, and rollout](2026-07-17-trip-location-route-integrity-04-surfaces-operations.md)

## File and Interface Map

### Shared contract

- `shared/types/api/route.ts` owns `RouteCoordinate`, `ActualRouteSegment`, `RouteQuality`, and `RouteGeometryStatus`.
- `shared/types/api/ride.ts` attaches the optional v2 route fields to `Ride`.
- `shared/utils/routeSegments.ts` validates API geometry and produces React Native and GeoJSON coordinates.

### Driver capture

- `driver-app/utils/tripLocationOutbox.ts` owns SQLite schema, sessions, sequences, enqueue, peek, acknowledge, and quarantine.
- `driver-app/utils/tripLocationRecorder.ts` converts native fixes into the shared outbox contract, uploads acknowledged REST batches, provides task liveness, watches capture gaps, and captures the completion fix.
- Existing `backgroundLocation.ts` and `useDriverDashboard.ts` become thin producers into the recorder.
- `driverStore.completeRide()` submits `RideCompletionRequest` with `completion_fix`, pending count, and final session/sequence.

### Backend ingestion

- Migration `235_trip_location_route_integrity.sql` adds idempotency, route-revision, processing-state, completion, and quality fields plus indexes and feature settings.
- `repositories/_base.py` provides conflict-safe bulk insert.
- `utils/breadcrumbs.py` provides `persist_trip_location_batch(...) -> LocationBatchPersistResult`; REST exposes only its `LocationBatchAck`.
- `POST /drivers/location-batch` accepts one ordered session batch and returns a contiguous acknowledgement.

### Route finalization

- `utils/route_segments.py` owns deterministic ordering, rejection, hard-gap segmentation, coverage, and completion-tail validation.
- `utils/route_distance.py` map-matches one verified segment at a time and preserves every provider sub-trace.
- `utils/route_finalizer.py` atomically claims pending rides, recomputes revisions, saves route quality, publishes snapshots, and retries replay-safely.
- `core/lifespan.py` starts the finalizer and active-ride gap-monitor loops on every replica.
- `repositories/ride_repo.py` exposes the versioned route contract to authorized ride-detail readers.

### Snapshots and consumers

- `utils/route_snapshot.py` accepts multiple segments, emits separate paths, and burns an incomplete-coverage banner into the PNG.
- `_shared.py` publishes revisioned snapshot object keys.
- Rider, driver, and admin maps consume `actual_route_segments`; endpoint Directions is planned-only and labelled.
- HTML email, client PDF, and backend PDF use the same snapshot revision and quality note.

## Ordered Task Index

1. Shared route API contract.
2. Additive database schema and indexes.
3. Conflict-safe bulk database helper.
4. Idempotent breadcrumb persistence and acknowledgements.
5. REST batch protocol and backward compatibility.
6. Install the SQLite dependency.
7. Durable trip-location outbox.
8. Unified recorder and background-task liveness.
9. Foreground recorder integration and sensor timestamps.
10. Separate ephemeral WebSocket live markers.
11. Driver completion-fix request.
12. Backend completion-fix validation and persistence.
13. Deterministic route segmentation.
14. Chunked per-segment road matching.
15. Replay-safe route finalizer.
16. Finalizer loop and atomic recovery.
17. Re-finalization after late acknowledged points.
18. Route-detail API projection.
19. Segmented, revisioned snapshot generation.
20. HTML and backend PDF route receipts.
21. Shared route rendering utility.
22. Rider ride details and client PDF.
23. Rider ride-completed surface.
24. Driver ride-details surface.
25. Admin route map and lifecycle/coverage labels.
26. Active-ride GPS heartbeat and monitor.
27. Gap-monitor lifecycle wiring.
28. Three-year derived-route retention.
29. End-to-end regression and physical-device matrix.
30. Full verification, Graphify rebuild, and rollout gates.

## Cross-Task Data Contracts

```ts
type LocationBatchRequest = {
  ride_id: string;
  recording_session_id: string;
  points: TripLocationPoint[];
};

type LocationBatchAck = {
  recording_session_id: string;
  acked_through: number;
  accepted_count: number;
  rejected: Array<{ sequence_number: number; reason: string }>;
};
```

```py
@dataclass(frozen=True)
class SegmentedRoute:
    ordered_points: list[dict]
    observed_segments: list[list[dict]]
    rejected: list[dict]
    quality: dict
```

```ts
type RideCompletionRequest = {
  completion_fix: TripLocationPoint | null;
  final_session_id: string | null;
  final_sequence_number: number | null;
  pending_outbox_count: number;
  off_route_confirmation?: { confirmed: true; reason: string };
};
```

## Task Completion Protocol

For every task:

1. Add the specified failing test or compile-time assertion.
2. Run only that test and confirm the documented failure.
3. Implement the minimum production change.
4. Run the targeted test and relevant type/lint check.
5. Inspect `git diff --check` and confirm no unrelated files changed.
6. Commit only the task’s files.
7. Mark the task complete before starting the next one.

Do not combine tasks, skip failing-test confirmation, or begin a later appendix early.

## Final Acceptance Gate

- Every acknowledged captured point exists exactly once in `driver_location_history`.
- A replayed batch produces the same acknowledgement without duplicate rows.
- The final route is deterministic in `captured_at`, session, and sequence order.
- The 49-minute regression fixture retains both long gaps as segment boundaries.
- No actual-route renderer or snapshot produces a solid chord across a hard gap.
- The final segment either validates against the completion fix or reports `missing_tail`.
- Lifecycle duration and GPS coverage duration are separate on every detail surface.
- Rider, driver, admin, HTML email, client PDF, and backend PDF reference the same route revision.
- Targeted suites, full non-slow backend tests, mobile tests, admin tests, lint, builds, and Graphify rebuild pass before rollout.
