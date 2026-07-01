/**
 * Driver-side TanStack Query hooks.
 *
 * Each hook owns one server endpoint and is the *only* place that endpoint
 * is fetched in the app. Components should never call `api.get(...)` for
 * data covered by one of these hooks — call the hook instead so dedupe,
 * cache, and invalidation behave as a single coherent system.
 *
 * Mutations live next to the queries they invalidate so the cache stays
 * consistent: if a write changes a server resource, the relevant
 * queryClient.invalidateQueries call is co-located with the mutation, not
 * sprinkled across screens.
 */
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import api from '../../api/client';
import { queryKeys } from '../../api/queryClient';

/**
 * GET /drivers/me — the driver's own row. Source of truth for vehicle
 * fields, verification status, service area, etc. Used by the home screen,
 * profile, payout, vehicle-info, and onboarding-status fallbacks.
 *
 * staleTime is short (30s) because admin-side changes (verification flips,
 * suspensions) need to surface quickly when the driver foregrounds the app.
 * The persisted cache means the screen still renders instantly on cold
 * start with the last-known row.
 */
export const useDriverMe = () => {
    return useQuery({
        queryKey: queryKeys.driver.me,
        queryFn: async () => {
            const res = await api.get('/drivers/me');
            return res.data;
        },
        staleTime: 30_000,
    });
};

/**
 * PUT /drivers/me — partial update of the driver row (vehicle info,
 * preferences, etc.). On success we invalidate /drivers/me AND /auth/me
 * (so the onboarding state machine recomputes); the screens listening on
 * either will refetch.
 */
export const useUpdateDriverMe = () => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationFn: async (updates: Record<string, any>) => {
            const res = await api.put('/drivers/me', updates);
            return res.data;
        },
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: queryKeys.driver.me });
            // Onboarding status is derived from the driver row inside
            // /auth/me — invalidate so the GO button gating refreshes.
            queryClient.invalidateQueries({ queryKey: ['auth', 'me'] });
        },
    });
};

/**
 * GET /drivers/config — feature flags, fee config, vehicle types list, etc.
 *
 * Long staleTime because this rarely changes and is fetched on most screens.
 * Pull-to-refresh on a screen calls refetch() to force a fresh load when
 * an admin pushed a new config that the driver wants to see now.
 */
export const useDriverConfig = () => {
    return useQuery({
        queryKey: queryKeys.driver.config,
        queryFn: async () => {
            const res = await api.get('/drivers/config');
            return res.data;
        },
        staleTime: 10 * 60_000,
        gcTime: 60 * 60_000,
    });
};

/**
 * GET /drivers/earnings?period=... — daily / weekly / monthly aggregates.
 *
 * Each period is its own cache key so switching tabs in the earnings UI
 * does not invalidate the others. staleTime=60s feels live without
 * hammering the API.
 */
export const useDriverEarnings = (period: 'today' | 'week' | 'month' | string) => {
    return useQuery({
        queryKey: queryKeys.driver.earnings(period),
        queryFn: async () => {
            const res = await api.get(`/drivers/earnings?period=${encodeURIComponent(period)}`);
            return res.data;
        },
        staleTime: 60_000,
    });
};

/** One weighted cell of the demand heatmap, ready for react-native-maps. */
export interface DemandHeatmapPoint {
    latitude: number;
    longitude: number;
    weight: number;
}

export interface DemandHeatmapData {
    enabled: boolean;
    points: DemandHeatmapPoint[];
    /** Server-directed polling cadence (per-service-area admin setting). */
    refreshSeconds: number;
}

/** Fallback cadence when the server omits refresh_seconds (older backend). */
export const HEATMAP_DEFAULT_REFRESH_SECONDS = 300;
/** Floor mirrors the backend clamp so a bad payload can't hammer the API. */
export const HEATMAP_MIN_REFRESH_SECONDS = 30;

/**
 * Normalize the raw /drivers/demand-heatmap payload ([[lat,lng,weight],...])
 * into typed points + a clamped refresh cadence. Exported for unit tests.
 */
export const normalizeDemandHeatmap = (raw: any): DemandHeatmapData => {
    const enabled = !!raw?.enabled;
    const refreshRaw = Number(raw?.refresh_seconds);
    const refreshSeconds = Number.isFinite(refreshRaw) && refreshRaw > 0
        ? Math.max(HEATMAP_MIN_REFRESH_SECONDS, refreshRaw)
        : HEATMAP_DEFAULT_REFRESH_SECONDS;
    const points: DemandHeatmapPoint[] = enabled
        ? (Array.isArray(raw?.points) ? raw.points : [])
            .filter((p: any) => Array.isArray(p) && typeof p[0] === 'number' && typeof p[1] === 'number')
            .map((p: number[]) => ({ latitude: p[0], longitude: p[1], weight: p[2] || 1 }))
        : [];
    return { enabled, points, refreshSeconds };
};

/**
 * GET /drivers/demand-heatmap — weighted demand cells for the driver's
 * service area, or {enabled:false} when the admin has the overlay off.
 *
 * Polling cadence is server-driven: each response carries refresh_seconds
 * (a per-service-area admin setting, migration 202), and refetchInterval
 * reads it off the last payload — so ops can retune the cadence without an
 * app release. `enabled` should be false while the driver is on a ride;
 * the overlay is an idle-positioning aid, not an in-trip distraction.
 */
export const useDemandHeatmap = (enabled = true) => {
    return useQuery({
        queryKey: queryKeys.driver.demandHeatmap,
        queryFn: async () => {
            const res = await api.get('/drivers/demand-heatmap');
            return normalizeDemandHeatmap(res.data);
        },
        enabled,
        refetchInterval: (query) => {
            const secs = query.state.data?.refreshSeconds ?? HEATMAP_DEFAULT_REFRESH_SECONDS;
            return secs * 1000;
        },
        // Consider fresh until the next poll tick; a remount inside the
        // window renders from cache instead of double-fetching.
        staleTime: HEATMAP_MIN_REFRESH_SECONDS * 1000,
    });
};

/**
 * GET /drivers/rides/active — the driver's currently-assigned ride, if any.
 *
 * staleTime is 0 because ride state transitions (rider cancellation,
 * pickup completion) MUST be reflected immediately. The WS handler also
 * calls queryClient.invalidateQueries(queryKeys.driver.activeRide) on every
 * `ride_status_changed` message, so the cache is push-driven; this hook is
 * the polling fallback.
 */
export const useActiveRide = (enabled = true) => {
    return useQuery({
        queryKey: queryKeys.driver.activeRide,
        queryFn: async () => {
            const res = await api.get('/drivers/rides/active');
            return res.data;
        },
        staleTime: 0,
        enabled,
    });
};
