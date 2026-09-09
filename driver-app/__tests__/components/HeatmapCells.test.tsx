import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import { Circle } from 'react-native-maps';
import { HeatmapCells } from '../../components/dashboard/HeatmapCells';
import type { HeatmapCell } from '../../hooks/useDemandHeatmap';

// This app's jest-expo preset defaults Platform.OS to 'ios', so
// USE_NATIVE_GRADIENT (computed once at HeatmapCells' module-load time) is
// false here — the iOS soft-blob path (BLOB_LAYERS.length <Circle> elements
// per surviving cell) renders, not the Android native <Heatmap>. The
// region-filter logic under test runs identically before either renderer
// branch, so counting Circle groups is exactly as valid a proxy for "which
// cells survived" as counting Heatmap points would be on Android.
jest.mock('react-native-maps', () => {
  const ReactActual = require('react');
  return {
    __esModule: true,
    Heatmap: (props: any) => ReactActual.createElement('Heatmap', props),
    Circle: (props: any) => ReactActual.createElement('Circle', props),
  };
});

// Real 6-digit hex values (the actual light-theme ramp, shared/theme/index.ts)
// so rampColorForRatio's interpolation math is meaningfully assertable below
// — a placeholder like '#a' isn't valid hex and would interpolate to NaN.
jest.mock('@shared/theme/ThemeContext', () => ({
  useTheme: () => ({ colors: { heatmapRamp: ['#ffe3e0', '#ffb3ac', '#ff7a6e', '#ff3b30', '#b71c1c'] } }),
}));

function surviving(
  cells: HeatmapCell[],
  region?: any,
  cellLatDeg = 0.01,
  cellLngDeg = 0.01,
  driverLocation?: { latitude: number; longitude: number } | null,
) {
  let renderer!: TestRenderer.ReactTestRenderer;
  act(() => {
    renderer = TestRenderer.create(
      <HeatmapCells
        cells={cells}
        region={region}
        cellLatDeg={cellLatDeg}
        cellLngDeg={cellLngDeg}
        driverLocation={driverLocation}
      />,
    );
  });
  // BLOB_LAYERS.length (3) Circle elements per surviving cell — divide back
  // down to a cell count so assertions read naturally.
  return renderer.root.findAllByType(Circle as any).length / 3;
}

describe('HeatmapCells — region viewport filter', () => {
  const NEAR: HeatmapCell = { lat: 52.1, lng: -106.6, weight: 5 };
  const FAR_NORTH: HeatmapCell = { lat: 52.5, lng: -106.6, weight: 5 };
  const region = { latitude: 52.1, longitude: -106.6, latitudeDelta: 0.02, longitudeDelta: 0.02 };

  it('excludes cells outside the given region bounds', () => {
    expect(surviving([NEAR, FAR_NORTH], region)).toBe(1);
  });

  it('includes every cell when region is null (viewport not yet known)', () => {
    expect(surviving([NEAR, FAR_NORTH], null)).toBe(2);
  });

  it('includes every cell when region is omitted entirely', () => {
    expect(surviving([NEAR, FAR_NORTH])).toBe(2);
  });

  it('treats the region boundary as inclusive, not exclusive', () => {
    // region spans lat [52.09, 52.11] — a cell exactly AT the max edge must
    // still be shown, not dropped by an off-by-one comparison.
    const onEdge: HeatmapCell = { lat: 52.11, lng: -106.6, weight: 3 };
    expect(surviving([onEdge], region)).toBe(1);
  });

  it('drops cells with non-finite coordinates instead of crashing the native map', () => {
    const corrupt: HeatmapCell = { lat: NaN, lng: -106.6, weight: 5 };
    const infinite: HeatmapCell = { lat: 52.1, lng: Infinity, weight: 5 };
    expect(surviving([corrupt, infinite, NEAR], null)).toBe(1);
  });

  it('renders nothing when every cell is filtered out', () => {
    let renderer!: TestRenderer.ReactTestRenderer;
    act(() => {
      renderer = TestRenderer.create(
        <HeatmapCells cells={[FAR_NORTH]} region={region} cellLatDeg={0.01} cellLngDeg={0.01} />,
      );
    });
    expect(renderer.root.findAllByType(Circle as any)).toHaveLength(0);
  });
});

describe('HeatmapCells — driver-position exclusion (2026-09-09, "concentric circles around the car icon")', () => {
  // cellLatDeg/cellLngDeg = 0.01 -> outerRadiusM ≈ 1002m -> excludeRadiusM ≈ 1302m.
  const AT_DRIVER: HeatmapCell = { lat: 52.1, lng: -106.6, weight: 5 };
  // ~2.2 km north — well outside the exclusion radius.
  const FAR: HeatmapCell = { lat: 52.12, lng: -106.6, weight: 5 };

  it('drops a cell centered on the driver so its blob never rings the car icon', () => {
    expect(surviving([AT_DRIVER], undefined, 0.01, 0.01, { latitude: 52.1, longitude: -106.6 })).toBe(0);
  });

  it('keeps a cell that is far enough from the driver', () => {
    expect(surviving([FAR], undefined, 0.01, 0.01, { latitude: 52.1, longitude: -106.6 })).toBe(1);
  });

  it('keeps every cell when driverLocation is not provided (back-compat)', () => {
    expect(surviving([AT_DRIVER, FAR], undefined, 0.01, 0.01, null)).toBe(2);
  });
});

describe('HeatmapCells — layered blob + continuous color ramp (2026-09-09, "clean minimalistic" gradient style)', () => {
  function renderCells(cells: HeatmapCell[]) {
    let renderer!: TestRenderer.ReactTestRenderer;
    act(() => {
      renderer = TestRenderer.create(
        <HeatmapCells cells={cells} cellLatDeg={0.01} cellLngDeg={0.01} />,
      );
    });
    return renderer.root.findAllByType(Circle as any);
  }

  const alphaOf = (fillColor: string) => Number(fillColor.match(/,([\d.]+)\)$/)?.[1]);

  it('renders BLOB_LAYERS.length circles per cell, widest/palest first, narrowest/densest last', () => {
    const circles = renderCells([{ lat: 52.1, lng: -106.6, weight: 5 }]);
    expect(circles).toHaveLength(3);

    const radii = circles.map((c) => c.props.radius as number);
    expect(radii[0]).toBeGreaterThan(radii[1]);
    expect(radii[1]).toBeGreaterThan(radii[2]);

    const alphas = circles.map((c) => alphaOf(c.props.fillColor as string));
    expect(alphas[0]).toBeLessThan(alphas[1]);
    expect(alphas[1]).toBeLessThan(alphas[2]);
  });

  it('interpolates color continuously with weight ratio instead of snapping to a fixed bucket', () => {
    // ratio 0.1 vs ratio 1.0 relative to the max in this render — must land
    // on genuinely different, non-bucketed colors. visibleCells sorts by
    // weight DESCENDING, so the weight=10 cell renders first.
    const circles = renderCells([
      { lat: 52.1, lng: -106.6, weight: 1 },
      { lat: 52.3, lng: -106.6, weight: 10 },
    ]);
    // 2 cells * 3 layers = 6 circles, in render order: weight=10 cell's
    // layers, then weight=1 cell's.
    expect(circles).toHaveLength(6);
    const highInnerColor = circles[2].props.fillColor as string;
    const lowInnerColor = circles[5].props.fillColor as string;
    expect(lowInnerColor).not.toBe(highInnerColor);
    // ratio=1.0 lands exactly on the ramp's last stop (#b71c1c = 183,28,28) —
    // proof the interpolation, not just the boost, is driving the color.
    expect(highInnerColor).toMatch(/^rgba\(183,28,28,/);
  });

  it('gives the busiest cells (top ramp tier) a stronger opacity than a mid-tier cell', () => {
    // visibleCells sorts by weight descending, so weight=10 renders first.
    const circles = renderCells([
      { lat: 52.1, lng: -106.6, weight: 5 },
      { lat: 52.3, lng: -106.6, weight: 10 }, // ratio 1.0 — past the 0.8 boost threshold
    ]);
    const hotInnerAlpha = alphaOf(circles[2].props.fillColor as string);
    const midInnerAlpha = alphaOf(circles[5].props.fillColor as string);
    expect(hotInnerAlpha).toBeGreaterThan(midInnerAlpha);
  });
});
