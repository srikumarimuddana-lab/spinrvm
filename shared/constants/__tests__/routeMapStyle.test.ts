import {
  ROUTE_GRADIENT_START,
  ROUTE_GRADIENT_END,
  ROUTE_PIN_COLORS,
  routeGradientColorAt,
  buildPathGradient,
  buildStraightRouteGradient,
} from '../routeMapStyle';

describe('routeGradientColorAt', () => {
  it('is orange at the start and red at the end', () => {
    expect(routeGradientColorAt(0).toUpperCase()).toBe(ROUTE_GRADIENT_START);
    expect(routeGradientColorAt(1).toUpperCase()).toBe(ROUTE_GRADIENT_END);
  });
  it('clamps out-of-range / NaN', () => {
    expect(routeGradientColorAt(-9).toUpperCase()).toBe(ROUTE_GRADIENT_START);
    expect(routeGradientColorAt(9).toUpperCase()).toBe(ROUTE_GRADIENT_END);
    expect(routeGradientColorAt(NaN).toUpperCase()).toBe(ROUTE_GRADIENT_START);
  });
});

describe('buildPathGradient (real route, colours only)', () => {
  const path: [number, number][] = [
    [50.44, -104.62], [50.445, -104.63], [50.45, -104.64], [50.455, -104.65], [50.46, -104.66],
  ];
  it('keeps every real point and stays continuous end-to-start', () => {
    const segs = buildPathGradient(path, 2);
    // First point = route start, last point = route end (real geometry preserved).
    expect(segs[0].coordinates[0]).toEqual(path[0]);
    expect(segs[segs.length - 1].coordinates.slice(-1)[0]).toEqual(path[path.length - 1]);
    // Adjacent chunks share an endpoint (unbroken line).
    expect(segs[0].coordinates.slice(-1)[0]).toEqual(segs[1].coordinates[0]);
  });
  it('colours run orange→red across the chunks', () => {
    const segs = buildPathGradient(path, 4);
    expect(segs[0].color).not.toBe(segs[segs.length - 1].color);
  });
  it('never bridges more chunks than points and drops junk', () => {
    expect(buildPathGradient([[1, 2]])).toEqual([]);
    expect(buildPathGradient(null)).toEqual([]);
    // 2 valid points → exactly one chunk even if more requested.
    expect(buildPathGradient([[1, 2], [3, 4]], 24)).toHaveLength(1);
  });
});

describe('buildStraightRouteGradient (fallback)', () => {
  it('draws a straight gradient when no real path exists', () => {
    const segs = buildStraightRouteGradient([50.44, -104.62], [50.40, -104.66], 4);
    expect(segs).toHaveLength(4);
    expect(segs[0].coordinates[0]).toEqual([50.44, -104.62]);
  });
  it('returns [] for missing/degenerate endpoints', () => {
    expect(buildStraightRouteGradient(null, [1, 2])).toEqual([]);
    expect(buildStraightRouteGradient([1, 2], [1, 2])).toEqual([]);
  });
});

describe('marker colours', () => {
  it('are the one shared green/red/amber set', () => {
    expect(ROUTE_PIN_COLORS.pickup).toBe('#10B981');
    expect(ROUTE_PIN_COLORS.dropoff).toBe('#EF4444');
    expect(ROUTE_PIN_COLORS.completion).toBe('#F59E0B');
  });
});
