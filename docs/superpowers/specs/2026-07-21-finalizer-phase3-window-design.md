# Finalizer Phase 3 Window Design

Date: 2026-07-21

## Problem

The completed-route finalizer loads every `driver_location_history` row carrying
the ride ID and passes all of them to segmentation. `segment_route()` validates
ordering and continuity but intentionally does not assign lifecycle phases.
Consequently, pre-start and post-completion coordinates can reach OSRM Match.

For ride `c90f649d-db44-44c4-9cd6-c3931ea41538`, strict Phase 3 evidence measured
0.017 km, while the finalizer produced a 0.389 km OSRM-matched detour. The
analyzer found one Phase 2 point and two post-completion points associated with
the ride.

## Decision

- The finalizer filters location rows by immutable capture time before calling
  `segment_route()`.
- Only rows satisfying
  `ride_started_at <= captured_at <= ride_completed_at` are eligible for the
  completed passenger route.
- Rows with invalid capture timestamps continue into segmentation so existing
  rejection diagnostics remain visible; they cannot become observed sections.
- A completed ride missing either lifecycle boundary fails finalization loudly
  as a contract violation.
- The separately stored authorized completion point remains the endpoint
  guardrail and is not replaced by a post-completion breadcrumb.
- `segment_route()` remains lifecycle-agnostic because the analyzer uses it for
  Phase 1, Phase 2, and Phase 3 buckets independently.

## Verification

A finalizer regression test supplies one pre-start point, two in-window points,
and one post-completion point. The mocked road matcher must receive exactly one
observed segment containing only the two in-window points. Existing route
finalizer, reconstruction, and projection tests must remain green.

No migration is required. Existing rides change only when explicitly
re-finalized.

