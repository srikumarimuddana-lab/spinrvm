import { renderHook } from '@testing-library/react-native';
import { cellCenter } from '../../lib/heatFalloff';
import { useVisibleHeatmapCells } from '../useVisibleHeatmapCells';
import type { HeatmapCell } from '../useDemandHeatmap';

// This suite documents/tests the exclusion-zone geometry against the legacy
// (flag-off) blob radius factor explicitly (see the comments below) — force
// it regardless of the real flag's shipped value, so these numbers stay
// exactly as originally derived rather than silently tracking whichever
// geometry happens to be live.
jest.mock('../../lib/heatFalloff', () => ({
  ...jest.requireActual('../../lib/heatFalloff'),
  SOFT_HEAT_RENDER_ENABLED: false,
}));

describe('cellCenter', () => {
  it('buckets a raw lat/lng onto the grid and returns the cell midpoint', () => {
    // cellLat=0.01, cellLng=0.01: 52.104 buckets to [52.10, 52.11), midpoint 52.105.
    const center = cellCenter(52.104, -106.596, 0.01, 0.01);
    expect(center.latitude).toBeCloseTo(52.105, 9);
    expect(center.longitude).toBeCloseTo(-106.595, 9);
  });
});

describe('useVisibleHeatmapCells — region viewport filter', () => {
  const NEAR: HeatmapCell = { lat: 52.1, lng: -106.6, weight: 5 };
  const FAR_NORTH: HeatmapCell = { lat: 52.5, lng: -106.6, weight: 5 };
  const region = { latitude: 52.1, longitude: -106.6, latitudeDelta: 0.02, longitudeDelta: 0.02 };

  it('excludes cells outside the given region bounds', () => {
    const { result } = renderHook(() => useVisibleHeatmapCells([NEAR, FAR_NORTH], region, 0.01, 0.01, null));
    expect(result.current.visibleCells).toEqual([NEAR]);
  });

  it('includes every cell when region is null (viewport not yet known)', () => {
    const { result } = renderHook(() => useVisibleHeatmapCells([NEAR, FAR_NORTH], null, 0.01, 0.01, null));
    expect(result.current.visibleCells).toHaveLength(2);
  });

  it('treats the region boundary as inclusive, not exclusive', () => {
    const onEdge: HeatmapCell = { lat: 52.11, lng: -106.6, weight: 3 };
    const { result } = renderHook(() => useVisibleHeatmapCells([onEdge], region, 0.01, 0.01, null));
    expect(result.current.visibleCells).toEqual([onEdge]);
  });

  it('drops cells with non-finite coordinates instead of crashing the native map', () => {
    const corrupt: HeatmapCell = { lat: NaN, lng: -106.6, weight: 5 };
    const infinite: HeatmapCell = { lat: 52.1, lng: Infinity, weight: 5 };
    const { result } = renderHook(() => useVisibleHeatmapCells([corrupt, infinite, NEAR], null, 0.01, 0.01, null));
    expect(result.current.visibleCells).toEqual([NEAR]);
  });

  it('sorts surviving cells by weight descending', () => {
    const low: HeatmapCell = { lat: 52.1, lng: -106.6, weight: 1 };
    const high: HeatmapCell = { lat: 52.3, lng: -106.6, weight: 10 };
    const { result } = renderHook(() => useVisibleHeatmapCells([low, high], null, 0.01, 0.01, null));
    expect(result.current.visibleCells).toEqual([high, low]);
    expect(result.current.maxWeight).toBe(10);
  });
});

describe('useVisibleHeatmapCells — driver-position exclusion', () => {
  // cellLatDeg/cellLngDeg = 0.01 -> outerRadiusM ≈ 690m (SOFT_HEAT_RENDER_ENABLED
  // is false, so the legacy 0.62 factor applies) -> excludeRadiusM ≈ 897m.
  const AT_DRIVER: HeatmapCell = { lat: 52.1, lng: -106.6, weight: 5 };
  // ~2.2 km north — well outside the exclusion radius.
  const FAR: HeatmapCell = { lat: 52.12, lng: -106.6, weight: 5 };
  const driver = { latitude: 52.1, longitude: -106.6 };

  it('drops a cell centered on the driver so its blob never rings the car icon', () => {
    const { result } = renderHook(() => useVisibleHeatmapCells([AT_DRIVER], null, 0.01, 0.01, driver));
    expect(result.current.visibleCells).toEqual([]);
  });

  it('keeps a cell that is far enough from the driver', () => {
    const { result } = renderHook(() => useVisibleHeatmapCells([FAR], null, 0.01, 0.01, driver));
    expect(result.current.visibleCells).toEqual([FAR]);
  });

  it('keeps every cell when driverLocation is not provided (back-compat)', () => {
    const { result } = renderHook(() => useVisibleHeatmapCells([AT_DRIVER, FAR], null, 0.01, 0.01, null));
    expect(result.current.visibleCells).toHaveLength(2);
  });

  it('exposes outerRadiusM sized off the given cell grid, for renderers to share', () => {
    const { result } = renderHook(() => useVisibleHeatmapCells([], null, 0.01, 0.01, null));
    // 0.01 deg * 111_320 m/deg * 0.62 (legacy factor — SOFT_HEAT_RENDER_ENABLED is false)
    expect(result.current.outerRadiusM).toBeCloseTo(690.18, 1);
  });
});
