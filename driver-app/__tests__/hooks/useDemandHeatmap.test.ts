/**
 * Regression tests for the driver demand-heatmap hook.
 *
 * This hook had zero test coverage despite driving what every online driver
 * sees on the home screen and how often the whole fleet polls the backend.
 * The cases below are the ones a pre-deploy review found broken or unguarded:
 * the offline shimmer that never resolved, an unclamped server-provided poll
 * interval, and v1/v2 payload skew against older or newer backends.
 */

import { renderHook, waitFor, act } from '@testing-library/react-native';

const mockGet = jest.fn();
jest.mock('@shared/api/client', () => ({
  __esModule: true,
  default: { get: (...args: unknown[]) => mockGet(...args) },
}));

jest.mock('react-native', () => ({
  AppState: { currentState: 'active', addEventListener: jest.fn(() => ({ remove: jest.fn() })) },
}));

import { useDemandHeatmap } from '../../hooks/useDemandHeatmap';

const v1Payload = {
  enabled: true,
  points: [[52.13, -106.67, 3]],
  total_rides: 12,
  refresh_seconds: 90,
};

const v2Payload = {
  enabled: true,
  points: [[52.13, -106.67, 3]],
  cells: [{ lat: 52.13, lng: -106.67, live: 4, baseline: 0.5, scheduled: 0 }],
  surge: { multiplier: 1.5, active: true },
  forecast: [{ hour: 17, demand: 0.8 }],
  refresh_seconds: 90,
};

beforeEach(() => {
  jest.clearAllMocks();
  mockGet.mockResolvedValue({ data: v1Payload });
});

describe('polling lifecycle', () => {
  it('does not fetch while the driver is offline', async () => {
    renderHook(() => useDemandHeatmap('idle', false));
    await act(async () => {});
    expect(mockGet).not.toHaveBeenCalled();
  });

  it('reports idle (not loading) while offline, so no shimmer is shown', async () => {
    // Regression: status stayed 'loading' forever for an offline driver —
    // nothing could resolve it because no request was ever in flight — so the
    // legend rendered a permanent loading skeleton on the map.
    const { result } = renderHook(() => useDemandHeatmap('idle', false));
    await act(async () => {});
    expect(result.current.status).toBe('idle');
    expect(result.current.visible).toBe(false);
  });

  it('does not fetch while the driver is on a ride', async () => {
    renderHook(() => useDemandHeatmap('in_progress', true));
    await act(async () => {});
    expect(mockGet).not.toHaveBeenCalled();
  });

  it('fetches once the driver is online and idle', async () => {
    const { result } = renderHook(() => useDemandHeatmap('idle', true));
    await waitFor(() => expect(mockGet).toHaveBeenCalled());
    await waitFor(() => expect(result.current.status).toBe('ready'));
  });

  it('hides the overlay entirely when the area has it disabled', async () => {
    mockGet.mockResolvedValue({ data: { enabled: false, points: [] } });
    const { result } = renderHook(() => useDemandHeatmap('idle', true));
    await waitFor(() => expect(result.current.status).toBe('disabled'));
    expect(result.current.visible).toBe(false);
  });
});

describe('refresh interval clamping', () => {
  it.each([
    [1, 30],
    [0, 30],
    [-5, 30],
    [99999, 600],
  ])('clamps a server value of %s to %s seconds', async (given, expected) => {
    // Unclamped, a mistyped admin value multiplies across every online driver:
    // refresh_seconds=1 turns the fleet into 1-second pollers.
    mockGet.mockResolvedValue({ data: { ...v1Payload, refresh_seconds: given } });
    const { result } = renderHook(() => useDemandHeatmap('idle', true));
    await waitFor(() => expect(result.current.status).toBe('ready'));
    expect(result.current.refreshSeconds).toBe(expected);
  });

  it('accepts an in-range value unchanged', async () => {
    mockGet.mockResolvedValue({ data: { ...v1Payload, refresh_seconds: 120 } });
    const { result } = renderHook(() => useDemandHeatmap('idle', true));
    await waitFor(() => expect(result.current.refreshSeconds).toBe(120));
  });
});

describe('payload version skew', () => {
  it('renders v1 points when the backend sends no cells (older build)', async () => {
    const { result } = renderHook(() => useDemandHeatmap('idle', true));
    await waitFor(() => expect(result.current.status).toBe('ready'));
    expect(result.current.isV2).toBe(false);
    expect(result.current.cells.length).toBe(1);
    expect(result.current.forecast).toEqual([]);
  });

  it('renders v2 cells, surge and forecast when present', async () => {
    mockGet.mockResolvedValue({ data: v2Payload });
    const { result } = renderHook(() => useDemandHeatmap('idle', true));
    await waitFor(() => expect(result.current.isV2).toBe(true));
    expect(result.current.surge).toEqual({ multiplier: 1.5, active: true });
    expect(result.current.forecast.length).toBe(1);
  });

  it('stays in v2 mode on a quiet night with an empty cells array', async () => {
    // Regression: branching on cells.length pushed an empty-but-present v2
    // response down the v1 path, so the layer selector and forecast strip
    // vanished whenever demand happened to drop to zero.
    mockGet.mockResolvedValue({ data: { ...v2Payload, cells: [] } });
    const { result } = renderHook(() => useDemandHeatmap('idle', true));
    await waitFor(() => expect(result.current.status).toBe('empty'));
    expect(result.current.isV2).toBe(true);
    expect(result.current.forecast.length).toBe(1);
  });

  it('defaults the interval when the backend omits refresh_seconds', async () => {
    mockGet.mockResolvedValue({ data: { enabled: true, points: [] } });
    const { result } = renderHook(() => useDemandHeatmap('idle', true));
    await waitFor(() => expect(result.current.status).toBe('empty'));
    expect(result.current.refreshSeconds).toBe(90);
  });
});

describe('failure handling', () => {
  it('does not crash or render stale cells when the endpoint fails', async () => {
    // Rural Saskatchewan: a failure must never block the map the driver needs
    // to go online and take offers.
    mockGet.mockRejectedValue(new Error('network down'));
    const { result } = renderHook(() => useDemandHeatmap('idle', true));
    await waitFor(() => expect(mockGet).toHaveBeenCalled());
    expect(result.current.cells).toEqual([]);
  });

  it('hides the overlay once failures become persistent', async () => {
    // A single blip should not flip the UI; three consecutive failures mean
    // the data is genuinely unavailable, and a permanent "unavailable" pill
    // riding along on the map for the rest of a shift is not the silent
    // degradation this feature promised. Once persistent, the overlay hides.
    jest.useFakeTimers();
    try {
      mockGet.mockRejectedValue(new Error('network down'));
      const { result } = renderHook(() => useDemandHeatmap('idle', true));
      // Advance past three poll cycles (default 90s, +/-10% jitter).
      for (let i = 0; i < 3; i++) {
        await act(async () => {
          await jest.advanceTimersByTimeAsync(120_000);
        });
      }
      expect(result.current.status).toBe('error');
      expect(result.current.visible).toBe(false);
    } finally {
      jest.useRealTimers();
    }
  });
});
