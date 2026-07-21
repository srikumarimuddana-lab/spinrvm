import { useEffect, useRef } from 'react';

import { normalizeActualRouteSegments } from '../utils/routeSegments';

export const COMPLETED_ROUTE_REFRESH_INTERVAL_MS = 3_000;
export const COMPLETED_ROUTE_REFRESH_LIMIT_MS = 60_000;

type CompletedRouteLike = {
  status?: unknown;
  route_schema_version?: unknown;
  route_geometry_status?: unknown;
  route_revision?: unknown;
  snapshot_revision?: unknown;
  route_snapshot_url?: unknown;
  actual_route_segments?: unknown;
};

function hasRevisionMatchedSnapshot(ride: CompletedRouteLike): boolean {
  const routeRevision = Number(ride.route_revision || 0);
  return (
    routeRevision > 0 &&
    Number(ride.snapshot_revision || 0) === routeRevision &&
    typeof ride.route_snapshot_url === 'string' &&
    ride.route_snapshot_url.length > 0
  );
}

export function shouldRefreshCompletedRoute(
  ride: CompletedRouteLike | null | undefined,
): boolean {
  if (!ride) return false;
  return (
    ride.status === 'completed' &&
    Number(ride.route_schema_version || 0) >= 2 &&
    (ride.route_geometry_status === 'pending' || ride.route_geometry_status === 'processing') &&
    normalizeActualRouteSegments(ride.actual_route_segments).length === 0 &&
    !hasRevisionMatchedSnapshot(ride)
  );
}

export function useCompletedRouteRefresh(
  ride: CompletedRouteLike | null | undefined,
  refresh: () => void | Promise<void>,
): void {
  const refreshRef = useRef(refresh);
  refreshRef.current = refresh;
  const shouldRefresh = shouldRefreshCompletedRoute(ride);

  useEffect(() => {
    if (!shouldRefresh) return;

    const interval = setInterval(() => {
      void refreshRef.current();
    }, COMPLETED_ROUTE_REFRESH_INTERVAL_MS);
    const limit = setTimeout(() => {
      clearInterval(interval);
    }, COMPLETED_ROUTE_REFRESH_LIMIT_MS);

    return () => {
      clearInterval(interval);
      clearTimeout(limit);
    };
  }, [shouldRefresh]);
}
