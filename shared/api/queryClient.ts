/**
 * TanStack Query setup, shared by every Spinr app.
 *
 * Why TanStack Query:
 * - Per-key in-memory cache → navigation between screens is instant once a
 *   query has resolved once; the second mount serves cached data immediately
 *   while a background refetch keeps it fresh.
 * - Built-in dedupe, so two screens fetching the same key during the same
 *   tick share a single network request.
 * - Stale-while-revalidate: stale data renders right away, fresh data
 *   replaces it when the server responds.
 * - WebSocket pushes can call `queryClient.invalidateQueries(...)` to mark
 *   the relevant key stale, triggering a refetch only on screens that are
 *   actually mounted.
 *
 * Persistence (AsyncStorage):
 * - Cache is serialised to AsyncStorage on change, so a cold app start can
 *   render the last-known state before the first network call returns.
 *   The driver/rider sees their last-known driver row, earnings, and
 *   notifications BEFORE login completes.
 * - We rotate the persisted cache when the app version changes
 *   (`buster: appVersion`) so a JS update doesn't ship an inconsistent
 *   shape from the previous bundle.
 *
 * Conservative defaults are set here. Each hook can override staleTime /
 * gcTime as needed (e.g. active ride uses staleTime=0; config uses 10 min).
 */
import { QueryClient } from '@tanstack/react-query';
import { createAsyncStoragePersister } from '@tanstack/query-async-storage-persister';
import AsyncStorage from '@react-native-async-storage/async-storage';

export const queryClient = new QueryClient({
    defaultOptions: {
        queries: {
            // Most data is OK if it's been seen within the last minute.
            // Per-hook overrides handle fast-moving (active ride) and
            // slow-moving (config, vehicle types) data.
            staleTime: 60_000,
            // Keep evicted queries in memory for 30 minutes — long enough
            // that bouncing between two tabs doesn't trigger a refetch.
            gcTime: 30 * 60_000,
            // The axios client already retries on 5xx with refresh-token
            // logic for 401, so a second client-side retry layer here would
            // only confuse error handling. Bail after the first failure.
            retry: false,
            // Refetch on app foreground so the driver doesn't see stale
            // earnings / notifications after a long background.
            refetchOnWindowFocus: true,
            refetchOnReconnect: true,
        },
        mutations: {
            retry: false,
        },
    },
});

export const asyncStoragePersister = createAsyncStoragePersister({
    storage: AsyncStorage,
    // Keep the persisted cache under a stable, app-prefixed key so
    // multiple Spinr apps on the same device don't collide.
    key: 'spinr-query-cache',
    // Only persist queries that have actually resolved successfully —
    // never persist errors or loading states.
    serialize: JSON.stringify,
    deserialize: JSON.parse,
});

/**
 * Cache-buster used by PersistQueryClientProvider.
 *
 * Bump (or replace with the app's version string) whenever the shape of
 * any persisted query result changes in a way that's not backwards-compat,
 * e.g. a renamed field. Otherwise the persisted cache from the previous
 * bundle can hydrate into the new bundle and crash a screen with
 * `undefined is not an object`.
 */
export const QUERY_CACHE_BUSTER = '1';

/**
 * Centralised query keys — every hook uses one of these so invalidation
 * elsewhere (e.g. WebSocket message handlers, mutations) is type-safe and
 * does not drift away from where the data is actually fetched.
 */
export const queryKeys = {
    driver: {
        me: ['driver', 'me'] as const,
        config: ['driver', 'config'] as const,
        earnings: (period: string) => ['driver', 'earnings', period] as const,
        activeRide: ['driver', 'activeRide'] as const,
        demandHeatmap: ['driver', 'demandHeatmap'] as const,
    },
    notifications: {
        list: ['notifications', 'list'] as const,
        preferences: ['notifications', 'preferences'] as const,
    },
} as const;
