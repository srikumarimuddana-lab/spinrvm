# Completed Ride Actual-Route-Only Design

## Goal

After a ride is completed, every rider and driver map must display only route geometry derived from timestamped GPS evidence captured during that trip. A booking-time planned route must never appear as a substitute for the completed route.

## Existing System

The backend route-integrity v2 pipeline already:

- stores immutable device capture timestamps separately from receipt time;
- assigns points to the trip using server-recorded ride milestones;
- orders evidence by capture time;
- splits GPS evidence at time, distance, and recording-session gaps;
- road-matches each segment independently;
- publishes a snapshot tied to the finalized route revision; and
- hides stale snapshots while a newer route revision is pending.

The remaining issue is in the mobile presentation layer. Completed-ride screens currently fall back to `planned_route_polyline` when `actual_route_segments` has not arrived. That can make an alternative booking route appear after the trip and can be mistaken for an actual route.

## Behavior

For a completed ride with `route_schema_version >= 2`:

1. A revision-matched snapshot is preferred when available.
2. Otherwise, each `actual_route_segments` entry is rendered as an independent polyline. Segment boundaries are never flattened or connected.
3. When finalization is `pending` or `processing`, the screen shows pickup and drop-off markers with an `Actual route processing` status. It draws no planned polyline.
4. The screen refreshes the ride periodically for a bounded period so finalized geometry can replace the processing state without requiring navigation away and back.
5. If finalization finishes as `incomplete`, only verified actual segments are drawn and the existing GPS-coverage label explains the limitation.
6. If no trustworthy actual segment is available after processing, the map remains marker-only and reports that the actual route is unavailable. It does not synthesize a route from Directions and does not show the planned route.

Legacy rides with `route_schema_version < 2` retain the existing explicitly labelled planned-route preview because they have no v2 evidence contract.

## Surfaces

The contract applies consistently to:

- the rider completion screen;
- rider ride details/history; and
- driver ride details/history.

The backend and receipt snapshot pipeline require no behavioral change for this task because they already expose revision-matched, segmented actual-route data.

## Refresh Contract

Polling is active only when all of the following are true:

- the ride is completed;
- route schema version is v2 or newer;
- route geometry status is `pending` or `processing`; and
- neither a revision-matched snapshot nor actual route segments are available.

The client refreshes every three seconds for at most sixty seconds. Polling stops immediately when actual geometry arrives, processing reaches a terminal state, the screen unmounts, or the time limit is reached. Refresh failures leave the current marker-only state visible and are retried only within the existing bound.

## Testing

Regression tests will assert that completed v2 screens:

- do not select or draw `planned_route_polyline` as fallback geometry;
- render actual segments independently;
- accept only a revision-matched actual snapshot;
- expose a marker-only processing/unavailable state; and
- use bounded polling that stops on finalized geometry.

Existing backend route-finalizer and snapshot boundary tests remain the authority for timestamp ordering, gap preservation, and revision matching.
