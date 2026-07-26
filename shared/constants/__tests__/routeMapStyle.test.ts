import {
  ROUTE_GRADIENT_START,
  ROUTE_GRADIENT_END,
  ROUTE_PIN_COLORS,
  routeGradientColorAt,
  buildStraightRouteGradient,
} from '../routeMapStyle';

describe('routeGradientColorAt', () => {
  it('is orange at the pickup end and red at the destination end', () => {
    expect(routeGradientColorAt(0).toUpperCase()).toBe(ROUTE_GRADIENT_START);
    expect(routeGradientColorAt(1).toUpperCase()).toBe(ROUTE_GRADIENT_END);
  });
  it('clamps out-of-range and non-finite t', () => {
    expect(routeGradientColorAt(-5).toUpperCase()).toBe(ROUTE_GRADIENT_START);
    expect(routeGradientColorAt(5).toUpperCase()).toBe(ROUTE_GRADIENT_END);
    expect(routeGradientColorAt(NaN).toUpperCase()).toBe(ROUTE_GRADIENT_START);
  });
  it('blends toward red as t increases', () => {
    const mid = routeGradientColorAt(0.5);
    expect(mid).not.toBe(ROUTE_GRADIENT_START);
    expect(mid).not.toBe(ROUTE_GRADIENT_END);
  });
});

describe('buildStraightRouteGradient', () => {
  const A: [number, number] = [50.44, -104.62];
  const B: [number, number] = [50.40, -104.66];

  it('returns a straight chain of N segments pickup→destination', () => {
    const segs = buildStraightRouteGradient(A, B, 10);
    expect(segs).toHaveLength(10);
    // First point is pickup, last point is destination — always a straight line.
    expect(segs[0].coordinates[0]).toEqual(A);
    expect(segs[segs.length - 1].coordinates[1][0]).toBeCloseTo(B[0]);
    expect(segs[segs.length - 1].coordinates[1][1]).toBeCloseTo(B[1]);
    // Adjacent segments join end-to-start (continuous line, no gaps).
    expect(segs[1].coordinates[0]).toEqual(segs[0].coordinates[1]);
  });

  it('colours run orange→red across the segments', () => {
    const segs = buildStraightRouteGradient(A, B, 4);
    expect(segs[0].color).not.toBe(segs[segs.length - 1].color);
  });

  it('returns [] for missing endpoints or a zero-length line', () => {
    expect(buildStraightRouteGradient(null, B)).toEqual([]);
    expect(buildStraightRouteGradient(A, undefined)).toEqual([]);
    expect(buildStraightRouteGradient(A, A)).toEqual([]);
  });
});

describe('marker colours', () => {
  it('are the one shared green/red/amber set', () => {
    expect(ROUTE_PIN_COLORS.pickup).toBe('#10B981');
    expect(ROUTE_PIN_COLORS.dropoff).toBe('#EF4444');
    expect(ROUTE_PIN_COLORS.completion).toBe('#F59E0B');
  });
});
