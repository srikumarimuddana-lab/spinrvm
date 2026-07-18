/** Safe route-geometry conversions shared by every ride-detail surface. */

export type LatLng = readonly [latitude: number, longitude: number];

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
      segment.coordinates.map(([latitude, longitude]) => [longitude, latitude]),
    ),
  };
}

/** Plain, approved quality copy for rider, driver, admin, and receipts. */
export function routeQualityLabel(quality: unknown): string {
  const value = quality as { coverage_ratio?: unknown; coverage_pct?: unknown; missing_tail?: unknown } | undefined;
  const ratio =
    typeof value?.coverage_ratio === 'number'
      ? value.coverage_ratio
      : typeof value?.coverage_pct === 'number'
        ? value.coverage_pct / 100
        : undefined;
  const coverage = ratio === undefined ? 'GPS coverage unavailable' : `${Math.round(ratio * 100)}% GPS coverage`;
  if (value?.missing_tail) return `Route incomplete · ${coverage}`;
  return `Route verified · ${coverage}`;
}
