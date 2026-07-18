/**
 * Actual-route geometry contract.
 *
 * These types mirror EXACTLY what the backend emits — do not add fields the
 * backend never returns. Sources of truth:
 *   - backend/utils/route_finalizer.py :: _observed_projection / _matched_projection / _quality_projection
 *   - backend/repositories/ride_repo.py :: _project_route_detail
 *
 * Coordinate order is [lat, lng] everywhere the backend persists it — observed
 * points, matched polylines (route_distance.py returns [[lat, lng], ...]), and
 * the per-segment observed fallback all use [lat, lng]. The [lng, lat] flip for
 * GeoJSON happens only in utils/routeSegments.ts, never in storage.
 */

export type RouteCoordinate = readonly [lat: number, lng: number];

/**
 * `route_geometry_status` on a ride detail == `ride_routes.processing_status`
 * (or `"pending"` when unset). The finalizer emits pending / processing /
 * complete / incomplete; `failed` is retained for legacy `save_status` rows.
 */
export type RouteGeometryStatus =
  | 'pending'
  | 'processing'
  | 'complete'
  | 'incomplete'
  | 'failed';

/**
 * Raw-GPS segment, emitted by `_observed_projection`. Surfaced as
 * `actual_route_segments` when no road-matched projection exists yet.
 *
 * Discriminant: it is the ONLY variant that carries `boundary_reason`, and it
 * carries neither `provider` nor `source_segment_index`.
 */
export interface ObservedRouteSegment {
  /** Why this segment ended (capture gap / session boundary). */
  boundary_reason: string;
  coordinates: RouteCoordinate[];
}

/**
 * Road-matched segment from a routing provider (`osrm_match` / `google_roads`),
 * emitted by `_matched_projection` for a successfully matched observed segment.
 *
 * Discriminant: carries `source_segment_index` AND `chunk_index`, with a
 * `provider` that is NOT `'observed_fallback'`.
 */
export interface MatchedRouteSegment {
  /** Index of the observed segment this matched geometry was derived from. */
  source_segment_index: number;
  /** Overlapping-chunk index within the observed segment. */
  chunk_index: number;
  /** Provider that produced the match, e.g. `osrm_match` / `google_roads`. */
  provider: string;
  coordinates: RouteCoordinate[];
}

/**
 * Per-segment fallback used when road matching failed for that observed
 * segment — the raw GPS is preserved rather than dropped. Emitted by
 * `_matched_projection` with `provider` pinned to `'observed_fallback'` and no
 * `chunk_index`.
 *
 * Discriminant: `provider === 'observed_fallback'`.
 */
export interface ObservedFallbackRouteSegment {
  source_segment_index: number;
  provider: 'observed_fallback';
  coordinates: RouteCoordinate[];
}

/**
 * `actual_route_segments` is a heterogeneous array: either all observed
 * segments (pre road-match) OR a mix of matched + observed-fallback segments
 * (post road-match — see `_project_route_detail`, which serves
 * `road_matched_segments` first, else `observed_segments`).
 *
 * Never join coordinates across segments — every boundary is a real capture gap
 * or a separate matched chunk. Use the runtime guards below to narrow.
 */
export type ActualRouteSegment =
  | ObservedRouteSegment
  | MatchedRouteSegment
  | ObservedFallbackRouteSegment;

/** Runtime narrowing: observed segments are the only ones with `boundary_reason`. */
export function isObservedRouteSegment(
  segment: ActualRouteSegment,
): segment is ObservedRouteSegment {
  return typeof (segment as ObservedRouteSegment).boundary_reason === 'string';
}

/** Runtime narrowing: fallback segments pin `provider` to the literal `'observed_fallback'`. */
export function isObservedFallbackRouteSegment(
  segment: ActualRouteSegment,
): segment is ObservedFallbackRouteSegment {
  return (segment as ObservedFallbackRouteSegment).provider === 'observed_fallback';
}

/** Runtime narrowing: matched segments carry a numeric `source_segment_index` from a real provider. */
export function isMatchedRouteSegment(
  segment: ActualRouteSegment,
): segment is MatchedRouteSegment {
  return (
    typeof (segment as ObservedRouteSegment).boundary_reason !== 'string' &&
    typeof (segment as MatchedRouteSegment).source_segment_index === 'number' &&
    (segment as MatchedRouteSegment).provider !== 'observed_fallback'
  );
}

/**
 * Route quality/coverage summary — mirrors `_quality_projection`.
 *
 * NOTE: this is the FINALIZED shape (`route_geometry_status` complete /
 * incomplete). In transient states (`pending` / `processing`, or a scheduled
 * retry) the backend may persist only a partial object such as
 * `{ finalization_reason }`, and `_project_route_detail` can return `{}`.
 * Treat every field defensively — this is why `Ride.route_quality` is optional
 * and `routeQualityLabel` reads coverage with a runtime guard.
 */
export interface RouteQuality {
  /** Fraction (0–1) of the lifecycle covered by usable GPS. */
  coverage_ratio: number;
  point_count: number;
  segment_count: number;
  rejected_point_count: number;
  max_gap_seconds: number;
  missing_tail: boolean;
  completion_distance_m: number | null;
  completion_tolerance_m: number | null;
  /**
   * Provider used for the matched distance. `null` when no provider matched any
   * segment (backend `compute_segmented_road_route` returns `provider: None`).
   */
  distance_provider: string | null;
  matched_distance_km: number;
  matching_failure_count: number;
  /** `'complete'` | `'provider_partial_failure'` | `'provider_failure'` | `null`. */
  finalization_reason: string | null;
  /** Only present when the finalizer set one (e.g. `missing_completion_fix`). */
  incomplete_reason?: string;
}
