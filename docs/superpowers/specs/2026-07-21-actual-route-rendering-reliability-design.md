# Actual Route Rendering Reliability Design

## Problem

Completed-ride maps can show pickup, destination, and completion markers without
showing a route line. The markers and the route are separate data products:

- pickup and destination are booking coordinates;
- completion is a final driver GPS fix when one is available;
- the actual route is a set of timestamp-ordered driver GPS segments.

Production evidence shows two failure modes:

1. A ride with 55% GPS coverage has drawable actual segments, but the mobile app
   gives a generated snapshot image precedence over those segments. A snapshot
   containing only the base map and markers therefore hides valid geometry.
2. A newer ride has no drawable segment and correctly cannot produce an actual
   route line, even though its booking and completion markers remain visible.

The backend also allows insufficient geometry to be classified as complete and
can publish a marker-only snapshot. Its route-finalizer contract test currently
detects a related status-classification regression.

## Product Decision

Completed rides display only recorded route evidence. The product must never
connect booking or completion markers to manufacture a route, and must never
substitute a directions-provider route for missing GPS history.

When evidence is partial, the app draws only independently verified segments.
When no segment has at least two valid timestamped GPS points, the app shows the
markers and an "Actual route unavailable" state without a polyline.

## Architecture

### Driver GPS capture

Durable recording starts directly when the backend confirms the ride has entered
`in_progress`, including both normal start and OTP start flows. Dashboard effects
remain an idempotent recovery mechanism, not the primary trigger. Recording
failures are surfaced through existing non-fatal diagnostics and route-health UI;
they never roll back an already-started ride.

Each point retains its ride ID, recording session, sequence number, sensor
timestamp, server receipt timestamp, and integrity metadata. Delayed batches
within the completed-ride retention window continue to requeue finalization.

### Backend route finalization

The finalizer orders valid GPS evidence by capture timestamp and preserves every
capture gap as a segment boundary. Pickup, destination, and completion coordinates
are validation anchors only:

- evidence must fall inside the ride lifecycle window;
- the completion fix validates the tail and helps detect an incomplete route;
- impossible jumps and invalid coordinates remain excluded;
- measured distance is the sum of validated route segments;
- pickup-to-completion road distance is a sanity comparison, not billable or
  display geometry.

A route is drawable only when at least one projected segment contains two or more
valid coordinates. If not drawable, finalization records an explicit incomplete
reason and does not publish a marker-only route snapshot as verified evidence.
Fare settlement remains immutable.

### Mobile completed-ride maps

Rider completion, rider history, and driver history render
`actual_route_segments` directly as independent native polylines. A v2 snapshot
must not replace available segment geometry in the app. Snapshots remain useful
for email receipts and exported PDFs.

The map refits when segment data arrives after the initial render. It never
flattens boundaries or draws a chord across a GPS gap. Legacy pre-v2 rides retain
their existing explicitly labelled planned-route behavior.

## Error and Empty States

- `pending` or `processing`: show "Actual route processing" and poll for the
  existing bounded interval.
- drawable partial or complete evidence: draw each actual segment and show its
  truthful GPS coverage label.
- no drawable evidence after finalization: show markers plus "Actual route
  unavailable"; do not display a line.
- snapshot loading/rendering failure: does not suppress native segment geometry.
- capture/upload failure: preserve durable points for retry and emit non-sensitive
  diagnostics without logging coordinates.

Historical rides with no stored intermediate GPS evidence cannot be reconstructed
truthfully and remain unavailable.

## Testing

1. Backend regression test: zero or one usable GPS point is incomplete and cannot
   publish a verified marker-only snapshot.
2. Backend regression test: two or more observed points remain drawable when a
   road-matching provider is unavailable.
3. Driver-store tests: both ride-start paths initialize durable recording
   immediately and report initialization failure without reverting ride state.
4. Rider and driver rendering tests: actual segments take precedence over
   snapshots and are refitted when asynchronous route data arrives.
5. Existing segmentation, route-contract, completion, and focused mobile route
   suites remain green.

## Deployment and Verification

The backend correction deploys through the normal `main` backend deployment. The
mobile JavaScript changes require a production EAS OTA publication. No native
dependency change is required. Verification uses a new completed ride and checks
that:

- actual segments appear without a planned or alternative route;
- partial gaps remain visibly separated;
- displayed GPS distance matches validated timestamped evidence;
- a no-evidence ride shows markers and the unavailable label without a line.
