/**
 * Tests for the shared, canonical route-geometry helpers. These lock the exact
 * user-facing copy and the [lat,lng] -> [lng,lat] GeoJSON flip so every ride
 * surface (rider, driver, admin, receipts) stays in lockstep. Keep them fast
 * and pure — no React Native / DOM imports.
 */
import {
  routeQualityLabel,
  normalizeActualRouteSegments,
  toReactNativeSegments,
  toGeoJsonMultiLineString,
} from '../routeSegments';
import {
  isObservedRouteSegment,
  isMatchedRouteSegment,
  isObservedFallbackRouteSegment,
  type ActualRouteSegment,
} from '../../types/api/route';

describe('routeQualityLabel', () => {
  it('returns the plain "Actual route" note when geometry is complete', () => {
    expect(routeQualityLabel('complete', { coverage_ratio: 0.87 })).toBe('Actual route');
  });

  it.each(['pending', 'processing'] as const)('reports still-processing for %s', (status: 'pending' | 'processing') => {
    expect(routeQualityLabel(status, undefined)).toBe('Actual route is still processing');
  });

  it('reports rounded GPS coverage when incomplete', () => {
    expect(routeQualityLabel('incomplete', { coverage_ratio: 0.54 })).toBe(
      'Route recording incomplete — 54% GPS coverage',
    );
    expect(routeQualityLabel('incomplete', { coverage_ratio: 0 })).toBe(
      'Route recording incomplete — 0% GPS coverage',
    );
  });

  it('reports unavailable coverage when the ratio is missing/nullish', () => {
    expect(routeQualityLabel('incomplete', { coverage_ratio: null })).toBe(
      'Route recording incomplete — GPS coverage unavailable',
    );
    expect(routeQualityLabel('incomplete', undefined)).toBe(
      'Route recording incomplete — GPS coverage unavailable',
    );
  });

  it('never over-claims for unknown/failed/absent statuses', () => {
    expect(routeQualityLabel('failed', { coverage_ratio: 0.5 })).toBe(
      'Route recording incomplete — 50% GPS coverage',
    );
    expect(routeQualityLabel(undefined, undefined)).toBe(
      'Route recording incomplete — GPS coverage unavailable',
    );
  });

  it('never emits the word "verified"', () => {
    const inputs: Array<Parameters<typeof routeQualityLabel>> = [
      ['complete', { coverage_ratio: 1 }],
      ['incomplete', { coverage_ratio: 0.9 }],
      ['pending', undefined],
      ['failed', {}],
      [null, null],
    ];
    for (const [status, quality] of inputs) {
      expect(routeQualityLabel(status, quality)).not.toMatch(/verified/i);
    }
  });
});

describe('normalizeActualRouteSegments', () => {
  // One of each backend variant, plus a malformed segment that must be dropped.
  const segments = [
    { boundary_reason: 'gap', coordinates: [[50.45, -104.61], [50.451, -104.612]] },
    { source_segment_index: 0, chunk_index: 0, provider: 'osrm_match', coordinates: [[50.46, -104.62], [50.461, -104.621]] },
    { source_segment_index: 1, provider: 'observed_fallback', coordinates: [[50.47, -104.63], [50.471, -104.631]] },
    { boundary_reason: 'bad', coordinates: [[999, 999]] },
  ];

  it('keeps each valid segment independent and drops malformed ones', () => {
    const out = normalizeActualRouteSegments(segments);
    expect(out).toHaveLength(3);
    expect(out[0].coordinates).toEqual([[50.45, -104.61], [50.451, -104.612]]);
  });

  it('preserves [lat,lng] for React Native polylines', () => {
    const rn = toReactNativeSegments(segments);
    expect(rn[0][0]).toEqual({ latitude: 50.45, longitude: -104.61 });
  });

  it('flips to [lng,lat] for GeoJSON without joining segments', () => {
    const geo = toGeoJsonMultiLineString(segments);
    expect(geo.type).toBe('MultiLineString');
    expect(geo.coordinates).toHaveLength(3);
    expect(geo.coordinates[0][0]).toEqual([-104.61, 50.45]);
  });

  it('returns empty geometry for non-array input', () => {
    expect(normalizeActualRouteSegments(undefined)).toEqual([]);
    expect(toGeoJsonMultiLineString(null).coordinates).toEqual([]);
  });
});

describe('actual-route segment guards', () => {
  const observed: ActualRouteSegment = { boundary_reason: 'gap', coordinates: [] };
  const matched: ActualRouteSegment = { source_segment_index: 0, chunk_index: 0, provider: 'osrm_match', coordinates: [] };
  const fallback: ActualRouteSegment = { source_segment_index: 1, provider: 'observed_fallback', coordinates: [] };

  it('discriminates observed vs matched vs observed_fallback', () => {
    expect(isObservedRouteSegment(observed)).toBe(true);
    expect(isMatchedRouteSegment(observed)).toBe(false);
    expect(isObservedFallbackRouteSegment(observed)).toBe(false);

    expect(isMatchedRouteSegment(matched)).toBe(true);
    expect(isObservedRouteSegment(matched)).toBe(false);
    expect(isObservedFallbackRouteSegment(matched)).toBe(false);

    expect(isObservedFallbackRouteSegment(fallback)).toBe(true);
    expect(isMatchedRouteSegment(fallback)).toBe(false);
    expect(isObservedRouteSegment(fallback)).toBe(false);
  });
});
