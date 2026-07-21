# Route Finalization Recovery Design

**Date:** 2026-07-21  
**Status:** Approved for implementation

## Problem

Completed ride `SPR-XUJZH9` exposed three linked failures:

1. The durable REST location upload failed during the trip, so the WebSocket
   fallback persisted 115 legacy breadcrumbs. That fallback omitted the
   device capture timestamp.
2. The finalizer fetched rows ordered by `captured_at`. Legacy rows have a null
   `captured_at`, and the segmenter assigned their database order as synthetic
   sequence order. It consequently rejected 77 valid rows as clock regressions
   and split 38 survivors into 34 fragments.
3. OSRM reconstructed 15 fragments but stopped at its connector guardrail,
   leaving eight internal gaps. The backend persisted and snapshotted the
   partial route as `incomplete`, cleared `next_retry_at`, and the UI described
   the terminal state as “OSRM reconstruction pending.”

The production Railway endpoint also returned HTTP 502 during investigation.
That outage explains why clients cannot refresh now, but it does not account
for the already-persisted malformed breadcrumb ordering.

## Goals

- Preserve chronological evidence when the acknowledged v2 upload is degraded.
- Reconstruct a completed route from pickup through recorded evidence to the
  authorized completion point.
- Retry provider-partial reconstruction instead of freezing a partial map.
- Never publish a revisioned snapshot with unresolved required gaps.
- Repair existing affected rides without changing settled money.
- Distinguish actively retrying, complete, and terminally incomplete states in
  user-facing copy.

## Non-goals

- Repricing completed rides.
- Reordering route evidence with OSRM Trip.
- Removing connector, distance, endpoint, or privacy guardrails.
- Sending raw coordinates to logs or public routing services.

## Design

### 1. Legacy evidence normalization

Legacy breadcrumbs are identified when both `recording_session_id` and
`sequence_number` are absent. Their canonical ordering is the parsed device
`timestamp`, not database return order. The segmenter will retain valid legacy
points even when the database returns rows in arbitrary null-`captured_at`
order, then sort all accepted evidence chronologically.

Durable v2 evidence retains the stronger invariant: sequence numbers within a
recording session are monotonic, and a decreasing capture time is rejected as
a clock regression.

### 2. Timestamp-preserving WebSocket fallback

The live WebSocket message will continue omitting v2 identity fields so it uses
the compatibility persistence path when REST is unhealthy. It will include
the point's `captured_at` value. The server already accepts this field and
stores it in the legacy `timestamp` column, allowing deterministic recovery.

The v2 SQLite outbox remains authoritative and is not deleted merely because a
transient upstream failure occurred.

### 3. Finalizer retry semantics

An attempted reconstruction with unresolved required gaps or unverified
pickup/completion anchors is retryable provider failure, not a finalized
route. The worker will:

- persist diagnostic quality and the partial geometry for authorized debugging;
- keep `processing_status = pending`;
- increment `retry_count` and assign exponential `next_retry_at`;
- clear the processing claim; and
- avoid updating post-trip distance statistics from partial geometry.

Retries are bounded. After the configured attempt budget, the route becomes
terminally `incomplete`, retains diagnostic geometry, and has no pending retry.
This prevents an infinite provider loop while making “pending” truthful.

### 4. Snapshot publication

A v2 snapshot is published only when route processing is `complete`. Partial
geometry must never become an immutable receipt/history image. A new complete
revision may replace the authorized reference to an older partial snapshot;
the existing revision ledger and retention process remain authoritative.

### 5. Client status copy

- `pending` or `processing`: “Route reconstruction in progress.”
- `complete`: existing observed/inferred coverage label.
- terminal `incomplete`: “Route unavailable · reconstruction failed.”

Clients continue rendering backend-provided geometry only. They never call
OSRM or Directions to replace completed-route evidence.

### 6. Existing-ride repair

After deployment and Railway health recovery, select completed v2 routes whose
quality reports `osrm_reconstruction_failed` or whose processing state is
`incomplete`. Requeue them through the normal finalizer by setting only the
route-processing queue fields and invalidating the old snapshot reference.

The backfill must not update fare, tax, tip, earnings, payment, or lifecycle
fields. The finalizer may update measured post-trip distance statistics only
after a complete reconstructed revision.

## Error handling and observability

- Log ride IDs and non-sensitive failure reasons; never log coordinates or
  addresses.
- Preserve provider errors as retry diagnostics rather than silently falling
  through to a partial completed state.
- Count retry attempts and incomplete terminal outcomes.
- Treat a Railway/backend outage as an upstream availability incident; queued
  route work remains durable until service recovers.

## Testing

1. A shuffled legacy trace with null `captured_at` retains every valid point in
   timestamp order and does not create false clock regressions.
2. Durable v2 clock regression remains rejected.
3. The WebSocket fallback includes `captured_at` without v2 identity fields.
4. Failed OSRM gaps schedule a bounded retry and do not publish a snapshot or
   update measured distance.
5. A later successful retry publishes one complete revision and updates only
   post-trip distance statistics.
6. Client labels distinguish active retry from terminal incomplete state.
7. A production-safe backfill query targets only affected route rows and leaves
   every settled monetary field untouched.

## Rollout

1. Restore and verify Railway backend health.
2. Deploy backend compatibility and retry changes.
3. Publish the driver OTA containing timestamp-preserving fallback behavior.
4. Publish rider and driver OTA copy changes if client status copy changes.
5. Requeue affected incomplete rides in a small batch, verify one corrected
   revision and private snapshot, then process the remainder.

