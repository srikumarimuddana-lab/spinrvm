/** Safe route-geometry conversions shared by every ride-detail surface. */

import type { RouteCoordinate, RouteGeometryStatus, RouteQuality } from '../types/api/route';

/** [lat, lng] — the order the backend persists every route coordinate in. */
export type LatLng = RouteCoordinate;

export interface NormalizedRouteSegment {
  id: string;
  coordinates: LatLng[];
}

export interface ReactNativeRouteCoordinate {
  latitude: number;
  longitude: number;
}

export interface GeoJsonMultiLineString {
  type: 'MultiLineString';
  coordinates: Array<Array<[longitude: number, latitude: number]>>;
}

type RouteSegmentLike = {
  id?: unknown;
  coordinates?: unknown;
  points?: unknown;
};

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
 *
 * Consumes the backend `coordinates` [lat, lng] shape emitted for every
 * segment variant (observed / matched / observed_fallback). A legacy `points`
 * key and a bare coordinate array are also tolerated.
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
    return [{
      id: typeof segment?.id === 'string' && segment.id ? segment.id : `segment-${index}`,
      coordinates: rawCoordinates.map(([latitude, longitude]) => [latitude, longitude] as LatLng),
    }];
  });
}

/** Convert v2 segments to independent React Native polylines. */
export function toReactNativeSegments(input: unknown): ReactNativeRouteCoordinate[][] {
  return normalizeActualRouteSegments(input).map((segment) =>
    segment.coordinates.map(([latitude, longitude]) => ({ latitude, longitude })),
  );
}

/** Convert v2 segments to MapLibre/GeoJSON longitude-latitude geometry. */
export function toGeoJsonMultiLineString(input: unknown): GeoJsonMultiLineString {
  return {
    type: 'MultiLineString',
    coordinates: normalizeActualRouteSegments(input).map((segment) =>
      // Storage is [lat, lng]; GeoJSON positions are [lng, lat] — flip here only.
      segment.coordinates.map(([latitude, longitude]) => [longitude, latitude]),
    ),
  };
}

/**
 * Canonical, approved route note for rider, driver, admin, and receipts.
 *
 * This is the SINGLE source of truth for the user-facing route string — do not
 * hand-roll this copy at a call site. It is keyed on the ride's geometry status
 * (`route_geometry_status`); the coverage percentage is derived from
 * `route_quality.coverage_ratio`. It never emits the word "verified".
 *
 *   complete              → "Actual route"
 *   pending | processing   → "Actual route is still processing"
 *   incomplete | anything  → "Route recording incomplete — {pct}% GPS coverage"
 *   else                     (or "…— GPS coverage unavailable" when unknown)
 */
export function routeQualityLabel(
  status: RouteGeometryStatus | string | null | undefined,
  quality?: RouteQuality | { coverage_ratio?: number | null } | null,
): string {
  if (status === 'complete') return 'Actual route';
  if (status === 'pending' || status === 'processing') return 'Actual route is still processing';

  // 'incomplete', 'failed', or any unknown/absent status → a coverage-qualified
  // note that never over-claims a finished route.
  const raw = quality?.coverage_ratio;
  const ratio = typeof raw === 'number' && Number.isFinite(raw) ? raw : null;
  if (ratio === null) {
    return 'Route recording incomplete — GPS coverage unavailable';
  }
  return `Route recording incomplete — ${Math.round(ratio * 100)}% GPS coverage`;
}
