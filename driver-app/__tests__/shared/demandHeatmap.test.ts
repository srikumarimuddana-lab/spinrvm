/**
 * Pins the demand-heatmap payload normalization in
 * shared/hooks/queries/driverQueries.ts::normalizeDemandHeatmap.
 *
 * The driver home screen renders whatever this function returns straight
 * into react-native-maps' <Heatmap>, and its refreshSeconds drives the
 * polling cadence — so malformed payloads must degrade to "no overlay",
 * never to a crash or a runaway poll loop.
 *
 * Note: imports the real shared module via a relative path to bypass
 * driver-app's `@shared/*` → `__mocks__/@shared/*` module-name mapper.
 */

// driverQueries imports the real API client (expo-secure-store etc.) — stub
// it; normalizeDemandHeatmap itself is pure and never touches it.
jest.mock('../../../shared/api/client', () => ({
  __esModule: true,
  default: { get: jest.fn(), post: jest.fn(), put: jest.fn(), delete: jest.fn() },
}));

import {
  normalizeDemandHeatmap,
  HEATMAP_DEFAULT_REFRESH_SECONDS,
  HEATMAP_MIN_REFRESH_SECONDS,
} from '../../../shared/hooks/queries/driverQueries';

describe('normalizeDemandHeatmap', () => {
  it('maps [lat,lng,weight] tuples into typed points', () => {
    const out = normalizeDemandHeatmap({
      enabled: true,
      points: [[52.13, -106.67, 4], [52.2, -106.7, 1]],
      refresh_seconds: 120,
    });
    expect(out.enabled).toBe(true);
    expect(out.points).toEqual([
      { latitude: 52.13, longitude: -106.67, weight: 4 },
      { latitude: 52.2, longitude: -106.7, weight: 1 },
    ]);
    expect(out.refreshSeconds).toBe(120);
  });

  it('defaults missing weight to 1', () => {
    const out = normalizeDemandHeatmap({ enabled: true, points: [[52.13, -106.67]] });
    expect(out.points[0].weight).toBe(1);
  });

  it('returns no points when the admin has the heatmap disabled', () => {
    const out = normalizeDemandHeatmap({
      enabled: false,
      points: [[52.13, -106.67, 3]], // must be ignored even if present
    });
    expect(out.enabled).toBe(false);
    expect(out.points).toEqual([]);
  });

  it('drops malformed point entries instead of crashing the map', () => {
    const out = normalizeDemandHeatmap({
      enabled: true,
      points: [[52.13, -106.67, 2], null, 'x', [], ['a', 'b'], [52.2]],
    });
    expect(out.points).toEqual([{ latitude: 52.13, longitude: -106.67, weight: 2 }]);
  });

  it('falls back to the default cadence when refresh_seconds is absent or garbage', () => {
    expect(normalizeDemandHeatmap({ enabled: true }).refreshSeconds)
      .toBe(HEATMAP_DEFAULT_REFRESH_SECONDS);
    expect(normalizeDemandHeatmap({ enabled: true, refresh_seconds: 'soon' }).refreshSeconds)
      .toBe(HEATMAP_DEFAULT_REFRESH_SECONDS);
    expect(normalizeDemandHeatmap({ enabled: true, refresh_seconds: -5 }).refreshSeconds)
      .toBe(HEATMAP_DEFAULT_REFRESH_SECONDS);
  });

  it('clamps a too-fast cadence to the minimum so the app cannot hammer the API', () => {
    const out = normalizeDemandHeatmap({ enabled: true, refresh_seconds: 1 });
    expect(out.refreshSeconds).toBe(HEATMAP_MIN_REFRESH_SECONDS);
  });

  it('survives a completely empty/undefined payload', () => {
    const out = normalizeDemandHeatmap(undefined);
    expect(out).toEqual({
      enabled: false,
      points: [],
      refreshSeconds: HEATMAP_DEFAULT_REFRESH_SECONDS,
      colorTheme: 'inferno',
    });
  });

  it('passes known color themes through and maps unknown ones to the default', () => {
    expect(normalizeDemandHeatmap({ enabled: true, color_theme: 'ocean' }).colorTheme).toBe('ocean');
    expect(normalizeDemandHeatmap({ enabled: true, color_theme: 'viridis' }).colorTheme).toBe('viridis');
    expect(normalizeDemandHeatmap({ enabled: true, color_theme: 'rainbow' }).colorTheme).toBe('inferno');
    expect(normalizeDemandHeatmap({ enabled: true }).colorTheme).toBe('inferno');
  });
});
