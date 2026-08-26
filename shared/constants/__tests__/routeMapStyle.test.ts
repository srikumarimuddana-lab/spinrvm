import {
  ROUTE_GRADIENT_START,
  ROUTE_GRADIENT_END,
  ROUTE_PIN_COLORS,
  routeGradientColorAt,
  buildPathGradient,
  buildStraightRouteGradient,
  routePinSvg,
  ROUTE_PIN_GEOMETRY,
  ROUTE_PIN_SPEC,
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

import { buildMultiPathGradient } from '../routeMapStyle';

describe('buildMultiPathGradient (v2 sections — no gap chord)', () => {
  const A: [number, number][] = [[50.44, -104.62], [50.445, -104.625], [50.45, -104.63]];
  const B: [number, number][] = [[50.50, -104.70], [50.505, -104.705], [50.51, -104.71]];
  it('never bridges across sections (no false chord over the GPS gap)', () => {
    const segs = buildMultiPathGradient([A, B]);
    // No sub-polyline spans the ~6 km jump between section A and section B.
    for (const s of segs) {
      const lats = s.coordinates.map((c) => c[0]);
      expect(Math.max(...lats) - Math.min(...lats)).toBeLessThan(0.03);
    }
  });
  it('colours continuously across sections and drops empty ones', () => {
    const segs = buildMultiPathGradient([A, B]);
    expect(segs[0].color).not.toBe(segs[segs.length - 1].color);
    expect(buildMultiPathGradient([[], [[1, 2]]])).toEqual([]);
    expect(buildMultiPathGradient(null)).toEqual([]);
  });
});

describe('routePinSvg — THE marker every surface draws', () => {
  it('uses the shared fill for each kind', () => {
    expect(routePinSvg('pickup')).toContain(ROUTE_PIN_COLORS.pickup);
    expect(routePinSvg('dropoff')).toContain(ROUTE_PIN_COLORS.dropoff);
    expect(routePinSvg('completion')).toContain(ROUTE_PIN_COLORS.completion);
  });

  it('draws a distinct glyph per kind — colour alone is not the difference', () => {
    // A colour-blind rider on the tracking page has to be able to tell the two
    // ends of their ride apart, so pickup and drop-off differ in SHAPE too.
    expect(routePinSvg('pickup')).toContain('<circle');
    expect(routePinSvg('dropoff')).toContain('<rect');
    expect(routePinSvg('completion')).toContain('<polyline');
  });

  it('renders no text — no surface may depend on a font being present', () => {
    for (const kind of ['pickup', 'dropoff', 'completion'] as const) {
      expect(routePinSvg(kind)).not.toContain('<text');
    }
  });

  it('scales to the requested size and keeps the glyph inside the disc', () => {
    const svg = routePinSvg('pickup', 60);
    expect(svg).toContain('width="60"');
    expect(svg).toContain('viewBox="0 0 60 60"');
    // The white dot never reaches the ring.
    const dotR = Number(/r="([\d.]+)" fill="#FFFFFF"/.exec(svg)?.[1]);
    expect(dotR).toBeGreaterThan(0);
    expect(dotR).toBeLessThan(30 - ROUTE_PIN_GEOMETRY.ringWidth * 2);
  });

  it('every kind in ROUTE_PIN_SPEC renders', () => {
    for (const kind of Object.keys(ROUTE_PIN_SPEC) as (keyof typeof ROUTE_PIN_SPEC)[]) {
      expect(routePinSvg(kind)).toMatch(/^<svg[\s\S]+<\/svg>$/);
    }
  });
});
