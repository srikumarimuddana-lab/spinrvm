# Trip route integrity runbook

## Purpose

This runbook explains how Spinr records, finalizes, presents, and investigates
an actual trip route. It is designed to prevent a GPS outage from being shown
as an invented route.

## What the July 17 ride indicates

The lifecycle timestamps show a ride start at 4:45 p.m. and completion at
5:34 p.m.: a 49-minute lifecycle. The former route card reported 4.04 km / 13
minutes because it was derived from the limited GPS trail that reached the
server, not from the lifecycle clock. The map then mixed that partial trail
with a reconstructed or flattened path, which created an implausible diagonal.

This is an evidence-quality issue, not proof that the rider travelled only 13
minutes. Do not use a route line alone to override lifecycle timestamps, fare,
or a driver/rider statement.

## Current behavior and contingency path

1. The driver records foreground and background trip points to Expo SQLite
   before upload. Each point has capture time, session, and sequence number.
2. Batches upload idempotently. A restart retries unacknowledged points from
   the local outbox.
3. Completion requires a fresh location fix near the recorded completion point;
   an off-route completion needs a driver reason. A missing or rejected tail is
   explicitly marked.
4. The finalizer orders accepted points by capture timestamp and splits a route
   at a session change or GPS time gap. It never joins those segments.
5. Matching and map rendering preserve these segment boundaries. A snapshot is
   published only for the matching route revision.
6. If a route is incomplete or a snapshot is stale, the rider app, driver app,
   admin replay, email receipt, and PDF say so. They show recorded segments or
   a labelled planned route; they do not request a replacement directions route.
7. During an active ride, the GPS-gap monitor opens one timestamp-only audit
   event when reporting exceeds the configured threshold (30 seconds by
   default), and resolves it after capture resumes.

No mobile system can guarantee GPS reception, background execution, or network
availability 100% of the time. This design guarantees the important product
property: missing evidence is visible as missing, durable evidence is retried,
and no gap is silently rendered as travel.

## Investigate one ride

Use an authenticated admin or service-role backend request; never copy raw
coordinates into a ticket, analytics event, or log.

1. Open the admin ride detail and record the ride ID.
2. Compare `ride_started_at` and `ride_completed_at`. This is the authoritative
   lifecycle duration.
3. Inspect the versioned route fields returned with the detail:

   - `actual_route_segments` — each is an independent captured/matched line.
   - `route_quality.coverage_ratio`, `max_gap_seconds`, and `missing_tail`.
   - `route_geometry_status` — `complete`, `incomplete`, or still processing.
   - `route_revision` and `snapshot_revision` — a snapshot is actual only when
     they match.
4. Review `ride_location_gap_events` through a backend/admin support query.
   It contains only ride/driver IDs and timestamps. An open/resolved event
   confirms when reporting stopped and resumed without exposing a location.
5. If `missing_tail` is true, tell the customer that the route capture was
   incomplete; use lifecycle duration and the fare record for billing review.
   Do not draw or describe a synthetic route as the actual path.
6. If a late client upload arrives, wait for the route finalizer to issue a
   newer `route_revision`. The receipt and snapshot automatically stay on the
   last matching revision until the new snapshot publishes.

## Escalation

Escalate to engineering when any of these occurs:

- `route_geometry_status` remains `pending` past the finalizer retry window.
- A completion-point rejection occurs without an accompanying driver reason.
- GPS-gap events are elevated across multiple active rides (possible location
  provider, app release, or backend ingestion incident).
- A displayed snapshot revision differs from the route revision.

Capture only: ride ID, route revision, processing status, coverage percentage,
gap duration, mobile app version, and timestamp range. Do not attach raw GPS
coordinates to the incident.

## Release validation

Run these cases before enabling the rollout mode for a production cohort:

1. Complete a 40+ minute trip with uninterrupted location reporting; verify
   lifecycle duration, complete quality, ordered segments, and matching image
   revision on rider, driver, admin, email, and PDF surfaces.
2. Force a 60-second background/offline gap, restart the driver app, then
   reconnect. Verify SQLite retries retained points, the gap is not bridged,
   and a gap event resolves after reporting resumes.
3. Complete while far from the captured completion fix. Verify the driver must
   provide a reason and the route is marked incomplete rather than extended.
4. Upload points out of order and replay the same batch. Verify timestamp order
   and uniqueness by session/sequence remain stable.
5. Publish late points after finalization. Verify a new route revision is made,
   a stale snapshot is not labelled actual, and the current revision replaces
   it only after snapshot publication.
6. Advance a test route beyond three years and run both retention functions in
   dry-run then mutation mode. Verify route segments, completion point, and
   snapshot URL are cleared while scalar audit/fare data remains.
