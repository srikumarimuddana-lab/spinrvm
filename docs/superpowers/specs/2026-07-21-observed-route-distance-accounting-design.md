# Observed Route Distance Accounting Design

Date: 2026-07-21

## Problem

The route finalizer currently assigns the total reconstructed geometry distance
to completed-ride statistics. That total includes OSRM Route connectors inferred
across missing GPS intervals. For ride
`103d560e-c596-429e-92cf-035f1eb66041`, 6.425 km of observed geometry plus
14.890 km of inferred connectors overwrote the displayed actual distance with
21.315 km.

## Decision

- `observed_distance_km` is the authority for completed-ride actual-distance
  statistics and the `matched_distance_km` quality projection.
- `inferred_distance_km` remains route-quality metadata and inferred geometry
  remains available to produce a continuous map without visible gaps.
- The total reconstructed geometry distance is not treated as GPS-observed or
  actually driven distance.
- Settled fare fields and the immutable fare snapshot remain unchanged.
- Actual distance is not capped to the planned distance because a valid
  observed route may legitimately differ from the booking estimate.

This narrows and supersedes the distance-accounting statement in
`2026-07-21-actual-route-osrm-gap-reconstruction-design.md`; its reconstruction,
provenance, rendering, failure-handling, and retention decisions remain intact.

## Implementation

The route-quality projection uses reconstructed `observed_distance_km` for
`matched_distance_km`. After successful finalization, the ride-statistics
recompute receives reconstructed `observed_distance_km` instead of the combined
reconstructed distance. No schema migration is required.

## Verification

A regression test supplies observed and inferred reconstructed distances and
asserts that both the quality projection and ride-statistics recompute receive
only the observed value. Existing route finalizer tests must remain green.

