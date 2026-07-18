# Trip Location and Route Integrity Design

**Date:** 2026-07-17  
**Status:** Approved  
**Scope:** Driver capture, backend ingestion and route finalization, snapshots, rider/driver/admin ride details, and receipts
## Problem

A completed ride can contain long periods with no GPS fixes. The current pipeline may still flatten separate OSRM matchings into one polyline, and clients render that array as one line. The result is a straight chord that looks like an actual driven route. Foreground fixes also use upload time instead of the location sensor timestamp, WebSocket points are cleared before durable acknowledgement, and the foreground REST fallback deletes data after three failed uploads.

The production ride investigated for this design lasted about 49 minutes but contained only about 13 minutes of usable in-trip GPS coverage, including gaps of roughly 26 and 10 minutes. The missing path cannot be reconstructed as fact.

## Goals

- Durably store every location fix successfully captured during an active ride.
- Order the trip by the GPS sensor capture timestamp.
- Build actual-route geometry only from defensible, continuous observations.
- Never display an inferred straight connection as an actual route.
- Validate the final route point against a fresh ride-completion location.
- Publish one versioned route snapshot consistently to all consuming surfaces.
- Keep fare settlement and ride completion responsive when route processing is delayed.
- Make missing coverage explicit and auditable.

## Non-goals

- Guarantee location delivery after an operating-system suspension or deliberate app force-quit.
- Reconstruct historical coordinates that were never captured.
- Replace the disclosed fare policy or infer billable distance through unknown gaps.
- Build custom Swift/Kotlin recorders before the Expo/native location implementation is tested on physical devices.

## Chosen Architecture
Use a unified durable mobile recorder, idempotent acknowledged ingestion, and an asynchronous segmented-route finalizer.

```text
Expo/native location fix
  -> durable device outbox
  -> acknowledged REST batches
  -> append-only raw breadcrumbs
  -> timestamp ordering and gap segmentation
  -> per-segment map matching
  -> versioned route segments and quality
  -> versioned snapshot
  -> rider, driver, admin, email, and PDF consumers
```

WebSocket location messages remain an ephemeral live-position channel. REST acknowledgement is the authority for deleting points from the device outbox.
## Mobile Capture and Outbox

Foreground and background location callbacks must call one `TripLocationRecorder`. The recorder uses a local SQLite outbox and writes each fix before attempting transmission.

Each point contains:

```text
ride_id, driver_id, recording_session_id, sequence_number,
captured_at, monotonic_ms, lat, lng, accuracy, speed, heading,
altitude, source, mocked, is_completion_fix
```

- Active-trip cadence is four seconds or ten metres at high accuracy.
- `captured_at` comes from the native location object and is immutable.
- `received_at` is assigned by the backend and never replaces `captured_at`.
- `sequence_number` increases within one recording session.
- A new app process creates a new `recording_session_id`.
- Foreground and background callbacks deduplicate through the same session/sequence contract.
- The outbox has no retry-count deletion. It removes only server-acknowledged sequences.
- Going offline stops new idle collection but does not delete unacknowledged active-ride points.
- Task liveness uses the actual location-service state, not persistent task registration.

One upload batch contains points from one session in sequence order. The server returns the highest contiguous acknowledged sequence plus permanent per-point rejections. Retryable failures remain queued; permanently invalid points move to a local diagnostic quarantine without blocking later valid points.

## Ingestion and Data Model

`driver_location_history` remains the append-only raw store and gains capture identity and source metadata. A partial unique index on `(ride_id, driver_id, recording_session_id, sequence_number)` makes retries idempotent. Exact GPS coordinates never appear in application logs, metrics, or Sentry.

`ride_routes` gains:

```text
route_schema_version, route_revision, processing_status,
observed_segments, road_matched_segments, completion_point,
route_quality, snapshot_revision, finalized_at
```

The public ride-detail contract exposes:

```text
actual_route_segments, route_quality, route_revision,
route_snapshot_url, route_geometry_status
```

Legacy single-polyline fields remain readable during rollout but are no longer written by the new finalizer. Existing historical rides are not reclassified as complete routes.

## Timestamp Ordering

The sensor timestamp is the primary route order:

```text
ORDER BY captured_at ASC, recording_session_id ASC, sequence_number ASC
```

Sequence is a stable tie-breaker for identical timestamps. Late and offline uploads are reinserted idempotently and the full trace is re-queried in capture order. Server receipt order and database insertion order never determine geometry.

The backend preserves clock anomalies rather than silently rewriting them. A backward timestamp, invalid future timestamp, or non-monotonic matcher input creates a segment boundary and lowers confidence. Points outside the ride capture window are rejected or quarantined with a non-PII reason.

## Completion Location

When the driver taps Complete, the app obtains a fresh high-accuracy fix, writes it to the outbox, and sends it as `completion_fix` with the final session and sequence identity.

Normal completion requires:

- Completion-fix age no greater than 15 seconds.
- Final breadcrumb no more than 30 seconds before completion.
- Final-point distance from completion fix no greater than  
  `max(75 m, final_accuracy + completion_accuracy + 25 m)`.

Failure does not invent a tail. It sets `missing_tail=true`, lowers confidence, and displays “Location recording ended before ride completion.”

The completion fix is also compared with the planned drop-off:

- Up to 200 m: normal completion.
- Over 200 m through 1 km: driver confirms that the ride ended elsewhere.
- Over 1 km: strong confirmation and a reason such as rider-requested stop, changed destination, or emergency.

GPS unavailability cannot permanently trap a ride in progress. The driver may confirm completion, but the route is marked incomplete and no completion connection is drawn.

## Segmentation and Map Matching

Hard segment boundaries are created for any of:

- More than 60 seconds between consecutive fixes.
- More than 300 metres without intermediate observations.
- Physically implausible implied speed.
- Timestamp regression or invalid time.
- Recording-session discontinuity that cannot be reconciled.
- Map-matcher split or low-probability transition.

Each continuous segment is processed independently. Provider requests use at most 90 points with a 10-point overlap, staying below the 100-point provider limit. The complete ride is never globally downsampled to 100 points. OSRM `matchings` remain separate; Google fallback chunks are merged only inside the same verified segment.

Display simplification happens after segmentation and can never remove a segment boundary. Actual distance is the sum of accepted segment distances. Unknown gaps contribute no invented distance. Any fare fallback remains separately identified in route quality.

## Finalization and Revisions

Fare settlement and the ride state transition complete independently of map generation. A replay-safe finalizer waits for the completion fix and a short upload grace period, then builds route revision 1. New acknowledged points trigger a debounced, idempotent recomputation and increment the revision.

Processing states are `pending`, `processing`, `complete`, `incomplete`, and `failed`. Provider or storage failures remain retryable and visible; they do not silently fall back to a misleading endpoint route.

Snapshots use revisioned object keys and URLs. A receipt is queued until initial route finalization, within a bounded delivery window. If no defensible route exists, the receipt is still delivered with an explicit unavailable/incomplete note. A later material revision updates apps immediately and is used by any resent receipt.

## Rendering and Receipts

All interactive maps consume `actual_route_segments`, never a flattened actual polyline. Each observed segment is solid. Unknown intervals remain blank; an inferred path, if ever enabled, must be dashed and explicitly labelled. Planned routes use a separate colour and label.

The versioned snapshot draws every segment as a separate path and includes pickup, verified completion, and planned drop-off markers when they differ. Incomplete routes show coverage text such as “Route recording incomplete — 26% GPS coverage.”

The same snapshot revision and quality note appear in:

- Rider ride-completed and ride-details screens.
- Driver ride-details screen.
- Admin ride-details map.
- HTML email receipt.
- Rider-generated PDF receipt.
- Backend-generated PDF receipt.

A completed-ride fallback may show the planned route only when labelled “Planned route.” It must never call Directions between endpoints and present the result as actual travel.

## Privacy and Retention

- Full-resolution raw breadcrumbs: 90 days.
- Segmented derived routes, completion point, quality, and snapshot: 3 years.
- Ride financial record: existing 7-year policy remains unchanged.
- Access remains role- and ride-scoped, with administrative access audited.
- Purge logic must clear all new geometry and snapshot fields at the configured boundary.

## Observability

Record non-PII metrics for capture coverage, largest gap, acknowledgement lag, duplicate rate, completion-fix age, completion distance, finalization duration, matcher outcome, snapshot revision, and receipt route status. An active ride with no server-received fix for 30 seconds emits a degraded-tracking alert to operations and the driver app.

Logs contain ride/driver IDs and reason codes only. Raw coordinates and exact addresses are prohibited.

## Testing and Acceptance

- Mobile unit tests cover ordering, durable restart recovery, acknowledgements, duplicate callbacks, permanent rejection, and no retry deletion.
- Backend unit tests cover idempotency, timestamp ordering, hard gaps, chunk overlap, matcher splits, completion thresholds, missing tails, and revision replay.
- Contract tests cover route segments and quality fields on rider, driver, and admin APIs.
- Snapshot tests prove no path crosses a segment boundary.
- Receipt tests verify the same revision and incomplete-route note in HTML and both PDFs.
- An end-to-end fixture reproduces a 49-minute ride with 26- and 10-minute gaps.
- Physical iOS and Android tests cover foreground, background, screen lock, low-power mode, offline recovery, process restart, permission downgrade, and force-quit limitations.

Acceptance requires:

- Every acknowledged captured point is stored exactly once.
- Route output is deterministic in sensor-capture order.
- No solid path crosses a hard gap.
- The final segment passes completion-location validation or is marked incomplete.
- Rider, driver, admin, snapshot, and receipts use the same route revision.
- Lifecycle duration is shown separately from GPS coverage duration.

## Rollout

Ship additive schema and backward-compatible readers first. Enable idempotent ingestion and the finalizer behind feature flags, then release the unified driver recorder. Run old and new route computation in shadow mode without exposing duplicate geometry. Compare coverage, distance, endpoint validation, and chord detection before enabling segmented rendering and versioned receipts. Roll back by disabling new writes and readers; additive data remains available for diagnosis.
