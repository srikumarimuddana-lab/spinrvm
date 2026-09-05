import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import { Circle } from 'react-native-maps';
import { HeatmapCells } from '../../components/dashboard/HeatmapCells';
import type { HeatmapCell } from '../../hooks/useDemandHeatmap';

// This app's jest-expo preset defaults Platform.OS to 'ios', so
// USE_NATIVE_GRADIENT (computed once at HeatmapCells' module-load time) is
// false here — the iOS soft-blob path (two <Circle> elements per surviving
// cell) renders, not the Android native <Heatmap>. The region-filter logic
// under test runs identically before either renderer branch, so counting
// Circle pairs is exactly as valid a proxy for "which cells survived" as
// counting Heatmap points would be on Android.
jest.mock('react-native-maps', () => {
  const ReactActual = require('react');
  return {
    __esModule: true,
    Heatmap: (props: any) => ReactActual.createElement('Heatmap', props),
    Circle: (props: any) => ReactActual.createElement('Circle', props),
  };
});

jest.mock('@shared/theme/ThemeContext', () => ({
  useTheme: () => ({ colors: { heatmapRamp: ['#a', '#b', '#c', '#d', '#e'] } }),
}));

function surviving(cells: HeatmapCell[], region?: any, cellLatDeg = 0.01, cellLngDeg = 0.01) {
  let renderer!: TestRenderer.ReactTestRenderer;
  act(() => {
    renderer = TestRenderer.create(
      <HeatmapCells cells={cells} region={region} cellLatDeg={cellLatDeg} cellLngDeg={cellLngDeg} />,
    );
  });
  // 2 Circle elements per surviving cell (outer + inner blob) — divide back
  // down to a cell count so assertions read naturally.
  return renderer.root.findAllByType(Circle as any).length / 2;
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
