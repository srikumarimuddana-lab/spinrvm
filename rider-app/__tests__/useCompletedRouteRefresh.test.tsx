import { act, renderHook } from '@testing-library/react-native';

import {
  COMPLETED_ROUTE_REFRESH_INTERVAL_MS,
  COMPLETED_ROUTE_REFRESH_LIMIT_MS,
  shouldRefreshCompletedRoute,
  useCompletedRouteRefresh,
} from '@shared/hooks/useCompletedRouteRefresh';

const pendingRide: Record<string, any> = {
  status: 'completed',
  route_schema_version: 2,
  route_geometry_status: 'pending',
  route_revision: 0,
  snapshot_revision: 0,
  actual_route_segments: [],
};

describe('completed-route refresh contract', () => {
  afterEach(() => {
    jest.useRealTimers();
  });

  it('refreshes only completed v2 routes awaiting actual geometry', () => {
    expect(shouldRefreshCompletedRoute(pendingRide)).toBe(true);
    expect(shouldRefreshCompletedRoute({ ...pendingRide, status: 'in_progress' })).toBe(false);
    expect(shouldRefreshCompletedRoute({ ...pendingRide, route_schema_version: 1 })).toBe(false);
    expect(shouldRefreshCompletedRoute({ ...pendingRide, route_geometry_status: 'complete' })).toBe(false);
    expect(shouldRefreshCompletedRoute({
      ...pendingRide,
      actual_route_segments: [{ coordinates: [[52.1, -106.6], [52.2, -106.5]] }],
    })).toBe(false);
    expect(shouldRefreshCompletedRoute({
      ...pendingRide,
      route_revision: 3,
      snapshot_revision: 3,
      route_snapshot_url: 'https://example.test/actual.png',
    })).toBe(false);
  });

  it('refreshes every three seconds and stops after sixty seconds', () => {
    jest.useFakeTimers();
    const refresh = jest.fn();

    const { unmount } = renderHook(() => useCompletedRouteRefresh(pendingRide, refresh));

    act(() => {
      jest.advanceTimersByTime(COMPLETED_ROUTE_REFRESH_INTERVAL_MS - 1);
    });
    expect(refresh).not.toHaveBeenCalled();

    act(() => {
      jest.advanceTimersByTime(1);
    });
    expect(refresh).toHaveBeenCalledTimes(1);

    act(() => {
      jest.advanceTimersByTime(COMPLETED_ROUTE_REFRESH_LIMIT_MS);
    });
    const callsAtLimit = refresh.mock.calls.length;

    act(() => {
      jest.advanceTimersByTime(COMPLETED_ROUTE_REFRESH_INTERVAL_MS * 2);
    });
    expect(refresh).toHaveBeenCalledTimes(callsAtLimit);
    unmount();
  });

  it('stops as soon as actual geometry arrives', () => {
    jest.useFakeTimers();
    const refresh = jest.fn();
    const { rerender } = renderHook(
      ({ ride }) => useCompletedRouteRefresh(ride, refresh),
      { initialProps: { ride: pendingRide } },
    );

    act(() => {
      jest.advanceTimersByTime(COMPLETED_ROUTE_REFRESH_INTERVAL_MS);
    });
    expect(refresh).toHaveBeenCalledTimes(1);

    rerender({
      ride: {
        ...pendingRide,
        route_geometry_status: 'complete',
        actual_route_segments: [{ coordinates: [[52.1, -106.6], [52.2, -106.5]] }],
      },
    });
    act(() => {
      jest.advanceTimersByTime(COMPLETED_ROUTE_REFRESH_INTERVAL_MS * 2);
    });
    expect(refresh).toHaveBeenCalledTimes(1);
  });
});
