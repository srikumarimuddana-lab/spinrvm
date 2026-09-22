/** Safe route-geometry conversions shared by every ride-detail surface. */

export type LatLng = readonly [latitude: number, longitude: number];

export type RouteSegmentPhase = 'navigating_to_pickup' | 'arrived_at_pickup' | 'trip_in_progress';

export interface NormalizedRouteSegment {
  id: string;
  coordinates: LatLng[];
  provider?: string;
  geometryKind: 'observed' | 'inferred';
  gapReason?: 'missing_start' | 'internal_gap' | 'missing_tail';
  /** Tracking phase the segment belongs to. Absent on untagged (pre-phase-
   * tagging) segments, which are trip_in_progress by construction. Non-trip
   * phases only arrive when the server's pickup-leg flag is on; surfaces
   * render them dashed, never joined into the trip line. */
  phase?: RouteSegmentPhase;
}

export interface ReactNativeRouteCoordinate {
  latitude: number;
  longitude: number;
}

export interface ReactNativeRouteSection {
  id: string;
  coordinates: ReactNativeRouteCoordinate[];
  provider?: string;
  geometryKind: 'observed' | 'inferred';
  gapReason?: 'missing_start' | 'internal_gap' | 'missing_tail';
  phase?: RouteSegmentPhase;
}

export interface GeoJsonMultiLineString {
  type: 'MultiLineString';
  coordinates: Array<Array<[longitude: number, latitude: number]>>;
}

type RouteSegmentLike = {
  id?: unknown;
  coordinates?: unknown;
  points?: unknown;
  provider?: unknown;
  geometry_kind?: unknown;
  gap_reason?: unknown;
  phase?: unknown;
};

const GAP_REASONS = new Set(['missing_start', 'internal_gap', 'missing_tail']);
const SEGMENT_PHASES = new Set(['navigating_to_pickup', 'arrived_at_pickup', 'trip_in_progress']);

function validCoordinate(value: unknown): value is readonly [number, number] {
  return (
    Array.isArray(value) &&
    value.length >= 2 &&
    typeof value[0] === 'number' &&
    Number.isFinite(value[0]) &&
    value[0] >= -90 &&
    value[0] <= 90 &&
    typeof value[1] === 'number' &&
    Number.isFinite(value[1]) &&
    value[1] >= -180 &&
    value[1] <= 180
  );
}

/**
 * Normalize each durable route segment without ever flattening its boundary.
 * A malformed segment is rejected wholesale so invalid GPS cannot create an
 * artificial chord between its neighbouring segments.
 */
export function normalizeActualRouteSegments(input: unknown): NormalizedRouteSegment[] {
  if (!Array.isArray(input)) return [];

  return input.flatMap((rawSegment, index) => {
    const segment = rawSegment as RouteSegmentLike;
    const rawCoordinates = Array.isArray(rawSegment)
      ? rawSegment
      : Array.isArray(segment?.coordinates)
        ? segment.coordinates
        : segment?.points;
    if (!Array.isArray(rawCoordinates) || rawCoordinates.length < 2 || !rawCoordinates.every(validCoordinate)) {
      return [];
    }
    const geometryKind = segment?.geometry_kind === 'inferred' ? 'inferred' : 'observed';
    const gapReason =
      geometryKind === 'inferred' && typeof segment?.gap_reason === 'string' && GAP_REASONS.has(segment.gap_reason)
        ? segment.gap_reason as ReactNativeRouteSection['gapReason']
        : undefined;
    const phase =
      typeof segment?.phase === 'string' && SEGMENT_PHASES.has(segment.phase)
        ? (segment.phase as RouteSegmentPhase)
        : undefined;
    return [{
      id: typeof segment?.id === 'string' && segment.id ? segment.id : `segment-${index}`,
      coordinates: rawCoordinates.map(([latitude, longitude]) => [latitude, longitude] as LatLng),
      provider: typeof segment?.provider === 'string' ? segment.provider : undefined,
      geometryKind,
      gapReason,
      phase,
    }];
  });
}

/** Convert v2 segments to independent native polylines with provenance. */
export function toReactNativeRouteSections(input: unknown): ReactNativeRouteSection[] {
  return normalizeActualRouteSegments(input).map((segment) => ({
    id: segment.id,
    coordinates: segment.coordinates.map(([latitude, longitude]) => ({ latitude, longitude })),
    provider: segment.provider,
    geometryKind: segment.geometryKind,
    gapReason: segment.gapReason,
    phase: segment.phase,
  }));
}

/** Convert v2 segments to independent React Native polylines. */
export function toReactNativeSegments(input: unknown): ReactNativeRouteCoordinate[][] {
  return toReactNativeRouteSections(input).map((segment) => segment.coordinates);
}

/** Convert v2 segments to MapLibre/GeoJSON longitude-latitude geometry. */
export function toGeoJsonMultiLineString(input: unknown): GeoJsonMultiLineString {
  return {
    type: 'MultiLineString',
    coordinates: normalizeActualRouteSegments(input).map((segment) =>
      segment.coordinates.map(([latitude, longitude]) => [longitude, latitude]),
    ),
  };
}

function toFiniteNumber(value: unknown): number | undefined {
  if (typeof value === 'boolean' || value == null) return undefined;
  const n = typeof value === 'number' ? value : typeof value === 'string' && value.trim() !== '' ? Number(value) : NaN;
  return Number.isFinite(n) ? n : undefined;
}

/**
 * Coerce a stored decoded polyline (`planned_route_polyline`, `road_polyline`,
 * phase trails) to `{lat, lng}[]`.
 *
 * Mirrors `normalize_polyline_points()` in `backend/utils/route_snapshot.py`:
 * the column contract is `[[lat, lng], …]`, but legacy import briefly wrote
 * `{lat, lng}` objects, and some JSONB rows still arrive as numeric strings.
 * A `[lng, lat]` pair is swapped when the first component cannot be a latitude.
 */
export function normalizeDecodedPolyline(
  value: unknown,
): { lat: number; lng: number; timestamp?: string }[] {
  if (!Array.isArray(value)) return [];
  const points: { lat: number; lng: number; timestamp?: string }[] = [];
  for (const raw of value) {
    let lat: number | undefined;
    let lng: number | undefined;
    let timestamp: string | undefined;
    if (Array.isArray(raw) && raw.length >= 2) {
      lat = toFiniteNumber(raw[0]);
      lng = toFiniteNumber(raw[1]);
      if (typeof raw[2] === 'string' && raw[2]) timestamp = raw[2];
    } else if (raw && typeof raw === 'object') {
      const row = raw as Record<string, unknown>;
      lat = toFiniteNumber(row.lat ?? row.latitude);
      lng = toFiniteNumber(row.lng ?? row.longitude ?? row.lon);
      if (typeof row.timestamp === 'string' && row.timestamp) timestamp = row.timestamp;
    }
    if (lat == null || lng == null) continue;
    if (Math.abs(lat) > 90 && Math.abs(lng) <= 90) {
      const swapped = lat;
      lat = lng;
      lng = swapped;
    }
    if (Math.abs(lat) > 90 || Math.abs(lng) > 180) continue;
    points.push(timestamp ? { lat, lng, timestamp } : { lat, lng });
  }
  return points;
}

/** Plain, approved quality copy for rider, driver, admin, and receipts. */
export function routeQualityLabel(quality: unknown): string {
  const value = quality as {
    coverage_ratio?: unknown;
    coverage_pct?: unknown;
    missing_tail?: unknown;
    incomplete_reason?: unknown;
    observed_distance_ratio?: unknown;
    inferred_distance_ratio?: unknown;
    failed_gaps?: unknown;
    reconstruction_status?: unknown;
    distance_basis?: unknown;
  } | undefined;
  if (value?.reconstruction_status === 'retrying') return 'Route reconstruction in progress';
  if (value?.reconstruction_status === 'failed') return 'Route unavailable · reconstruction failed';
  // When GPS was too incomplete to trust, the displayed distance is the booked
  // estimate, not a measured value — say so plainly rather than implying a
  // precise GPS figure (the honest-labeling fix for the incident).
  if (value?.distance_basis === 'planned_estimated') return 'Distance estimated from booking · GPS incomplete';
  // The mirror case: GPS was complete but the road reconstruction came out
  // implausibly long (ride SPR-EG7X86, 2026-09-12: 15.1 km for a 9.2 km
  // trip), so the finalizer published the booked distance instead. The
  // observed/inferred ratios below belong to the discarded reconstruction
  // and must not be shown beside a figure that is not GPS-measured.
  if (value?.distance_basis === 'planned_capped') return 'Distance from booking · GPS route implausible';
  // The third case, added with the gap-connector guards: enough of the
  // distance came from routed gap fill that the published total would have
  // been part measurement and part guess, so the booking was published
  // instead. The same warning as planned_capped applies — the ratios below
  // describe the reconstruction that was rejected, not the figure shown.
  if (value?.distance_basis === 'planned_guess_deviation') return 'Distance from booking · GPS route partly inferred';
  const observedRatio =
    typeof value?.observed_distance_ratio === 'number' ? value.observed_distance_ratio : undefined;
  const inferredRatio =
    typeof value?.inferred_distance_ratio === 'number' ? value.inferred_distance_ratio : undefined;
  if (observedRatio !== undefined && inferredRatio !== undefined) {
    if ((Array.isArray(value?.failed_gaps) && value.failed_gaps.length > 0) || value?.incomplete_reason === 'osrm_reconstruction_failed') {
      return 'Route incomplete · OSRM reconstruction pending';
    }
    const observed = Math.round(Math.max(0, Math.min(1, observedRatio)) * 100);
    const inferred = Math.round(Math.max(0, Math.min(1, inferredRatio)) * 100);
    if (inferred > 0) {
      return `Route reconstructed · ${observed}% GPS observed · ${inferred}% inferred`;
    }
    return `Route verified · ${observed}% GPS observed`;
  }
  const ratio =
    typeof value?.coverage_ratio === 'number'
      ? value.coverage_ratio
      : typeof value?.coverage_pct === 'number'
        ? value.coverage_pct / 100
        : undefined;
  const coverage = ratio === undefined ? 'GPS coverage unavailable' : `${Math.round(ratio * 100)}% GPS coverage`;
  if (value?.missing_tail || typeof value?.incomplete_reason === 'string') {
    return `Route incomplete · ${coverage}`;
  }
  return `Route verified · ${coverage}`;
}
