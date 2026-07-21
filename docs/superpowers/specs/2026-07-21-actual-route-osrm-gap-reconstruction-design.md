# Actual Route OSRM Gap Reconstruction Design

Date: 2026-07-21

## Problem

A completed ride can report `100% GPS coverage` while its displayed geometry
starts away from pickup, stops before the completion fix, or contains spatial
gaps. The current coverage ratio measures only the time between the first and
last accepted breadcrumb relative to the ride lifecycle. It does not measure
spatial continuity or endpoint coverage.

The route finalizer currently preserves this evidence boundary intentionally:
it map-matches each continuous GPS segment independently and never connects
separate segments. That avoids falsely presenting a straight chord as driven
geometry, but it also means the completed map may show only a short fragment.

The completed route must instead extend from the ride pickup guardrail to the
authorized completion point. Missing start, middle, or tail intervals must be
reconstructed through OSRM without reordering the trip. Reconstructed distance
contributes to post-trip statistics only and must never alter the settled fare.

## Goals

- Produce a chronological, road-following route from pickup to completion.
- Preserve directly GPS-supported geometry as observed evidence.
- Reconstruct missing intervals explicitly as inferred geometry.
- Calculate post-trip distance from both matched and inferred geometry.
- Replace misleading time-only coverage copy with spatially meaningful route
  quality data.
- Remain replay-safe when late GPS breadcrumbs trigger a new route revision.
- Degrade honestly when OSRM is unavailable.

## Non-goals

- Repricing or resettling a completed ride.
- Treating an OSRM-inferred path as raw GPS evidence.
- Changing the booking-time planned route.
- Using OSRM Trip service to optimize or reorder stops.
- Persisting additional raw GPS history beyond the existing retention policy.

## Root Cause

`route_segments._coverage_ratio()` compares only timestamps. A trace whose
first and last timestamps span the lifecycle can therefore receive `1.0` even
when the first coordinate is far from pickup or the trace contains spatial
breaks.

`compute_segmented_road_route()` then sends each observed segment to OSRM Match
separately. `route_finalizer._matched_projection()` preserves those segment
boundaries and does not add pickup, completion, or gap connectors. The clients
correctly render the returned arrays independently, exposing the incomplete
geometry visible in the production screenshot.

## OSRM Service Roles

Each OSRM service has one bounded responsibility:

- **Nearest** snaps pickup and completion guardrails to the routable street
  network. The original authorized coordinates remain the displayed markers.
- **Match** reconstructs the most plausible road geometry for continuous,
  timestamped GPS observations. Multiple matchings remain separate until the
  gap reconstruction stage evaluates their boundary.
- **Route** connects ordered boundary pairs where spatial evidence is missing:
  pickup to first matched point, matched segment to matched segment, and last
  matched point to completion.
- **Trip is not used.** Trip solves a travelling-salesman-style ordering
  problem. Even with fixed endpoints, it is not the authority for the ride's
  chronological sequence.

Only the configured self-hosted `osrm_url` may process completed trip traces.
The public demo fallback remains limited to the existing light live-routing
calls and must not receive durable post-trip traces.

## Reconstruction Pipeline

1. Load durable breadcrumbs ordered by immutable device capture timestamp.
2. Validate, reject replayed/invalid points, and split observations using the
   existing time, displacement, session, and speed boundaries.
3. Resolve the pickup guardrail from `rides.pickup_lat/pickup_lng` and the end
   guardrail from `ride_routes.completion_point`. If the authorized completion
   coordinate is absent, finalization remains incomplete and retries according
   to the existing policy.
4. Snap both guardrails with OSRM Nearest. A snap is accepted only when it is
   no more than 75 metres from the original guardrail. The unsnapped coordinates
   remain audit and marker truth.
5. Send each continuous observed segment to OSRM Match with timestamps,
   accuracy radiuses, bearings when available, `gaps=split`, and GeoJSON full
   overview.
6. Build an ordered list of matched or observed-fallback sections. Each section
   retains its original source segment index and provider.
7. Compare adjacent boundaries in chronological order:
   - snapped pickup to the first section start;
   - each section end to the next section start;
   - the final section end to snapped completion.
8. If a boundary pair is already within 30 metres, deduplicate the shared
   endpoint and do not call Route.
9. Otherwise call OSRM Route for that pair with `alternatives=false`,
   `overview=full`, and `geometries=geojson`. Store the returned connector as
   `provider=osrm_inferred` with a reason of `missing_start`, `internal_gap`, or
   `missing_tail`.
10. Reject a connector whose distance is shorter than its straight-line
    boundary distance or greater than the larger of five times that distance
    and that distance plus two kilometres. Process at most 20 connectors per
    revision; a route exceeding the limit remains incomplete for investigation.
11. Publish the ordered section list as the route revision. Never flatten
    different provenance into one unlabelled array.

The connector builder is deterministic for the same guardrails, observations,
and provider responses. A late breadcrumb causes the existing re-finalization
flow to create a new revision; any now-unnecessary inferred connector disappears
from the new projection.

## Route Projection Contract

Each public actual-route section contains:

```json
{
  "coordinates": [[50.45, -104.62], [50.451, -104.621]],
  "provider": "osrm_match",
  "geometry_kind": "observed",
  "gap_reason": null
}
```

Allowed `geometry_kind` values are:

- `observed`: supported by timestamped GPS and emitted by OSRM Match or the
  observed fallback.
- `inferred`: emitted by OSRM Route to connect an explicitly detected missing
  interval.

`gap_reason` is present only for inferred sections and is one of
`missing_start`, `internal_gap`, or `missing_tail`.

Existing clients that read only `coordinates` remain compatible. Updated
clients use `geometry_kind` to style and label each section.

## Distance and Quality

The finalizer computes:

- `observed_distance_km`: sum of GPS-supported matched/observed sections.
- `inferred_distance_km`: sum of OSRM Route connectors.
- `actual_distance_km`: observed plus inferred post-trip route distance.
- `observed_distance_ratio`: observed distance divided by total reconstructed
  distance, clamped to `[0, 1]`.
- `inferred_distance_ratio`: inferred distance divided by total reconstructed
  distance.
- `endpoint_start_verified` and `endpoint_end_verified`.
- `inferred_gap_count`, with start, internal, and tail reason counts.
- Existing point/rejection/max-time-gap diagnostics.

The old timestamp span remains available as `temporal_coverage_ratio` for
diagnostics but is no longer displayed as GPS route coverage. The rider and
driver UI display observed versus inferred route coverage based on distance.

`actual_distance_km` and phase/statistics projections are updated after
finalization. Fare components, charged total, driver earnings, taxes, and
settlement records remain immutable.

## Rendering

Completed-ride maps render sections in the backend-provided order:

- observed geometry uses the existing solid actual-route stroke;
- inferred geometry uses a visually distinct dashed stroke;
- pickup, destination, and authorized completion markers remain independent
  guardrails;
- the viewport includes the complete reconstructed geometry and all markers.

The quality label must not say `100% GPS coverage` when OSRM supplied any
connector. Example copy:

- `Route reconstructed · 72% GPS observed · 28% inferred`
- `Route verified · 100% GPS observed`
- `Route incomplete · OSRM reconstruction pending`

The booking destination remains a marker and receipt address. The actual route
ends at the authorized completion point, which may differ from that destination.

## Failure Handling

- Nearest failure: use the original endpoint only when it is already inside the
  continuity tolerance of the adjacent geometry; otherwise mark the endpoint
  unresolved.
- Match failure: retain the existing observed fallback for that continuous
  segment and classify it as observed.
- Route connector failure: do not draw a straight line. Persist the available
  sections, mark processing incomplete, record the failed gap reason, and use
  the existing replay-safe retry schedule.
- Invalid or excessive provider geometry: reject the connector and surface an
  actionable finalization failure; never silently substitute the planned route.
- Late breadcrumbs: requeue finalization through the existing atomic revision
  flow.

Logs contain ride identifiers and non-sensitive reason codes only. Raw GPS
coordinates must not appear in logs, Sentry, or analytics.

## Testing Strategy

Backend tests cover:

- a temporally complete trace that starts away from pickup no longer reports
  `100% GPS observed`;
- missing start, internal gap, and missing tail each produce ordered Route
  connectors with the correct reason;
- close boundaries deduplicate without a provider call;
- Nearest snaps endpoints while public markers retain original coordinates;
- Match sub-traces remain ordered and are never passed to Trip;
- distance equals observed plus inferred geometry and cannot update fare fields;
- connector failure leaves a visible gap, marks the route incomplete, and
  schedules retry;
- late evidence replaces obsolete inferred geometry in a newer revision.

Shared and mobile tests cover:

- route-section normalization preserves provenance;
- rider completion, rider history, and driver history render inferred sections
  with the inferred stroke;
- the status label uses distance-based observed/inferred ratios;
- map fitting includes pickup and completion guardrails;
- no completed screen calls Directions or reconstructs a route client-side.

## Rollout and Compatibility

No schema migration is required because route sections and quality are stored
in existing JSON fields. The backend contract is additive. Deploy the backend
first, then mobile clients. Older clients continue to render every section with
their existing actual-route stroke; updated clients distinguish provenance.

Existing finalized rides are unchanged until re-finalized. A bounded operational
backfill may later requeue affected rides, but it is outside this implementation
unless explicitly authorized.
