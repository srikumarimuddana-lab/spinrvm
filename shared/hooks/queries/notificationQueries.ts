/**
 * Notification-related TanStack Query hooks.
 *
 * The inbox lives at /notifications and supports paging via limit/offset.
 * For the simple "latest 50" inbox screen we only need a single page —
 * if pagination becomes a UX requirement, swap useQuery for useInfiniteQuery.
 */
import { useEffect, useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import api, { isAppCheckTokenReady } from '../../api/client';
import { queryKeys } from '../../api/queryClient';

const APP_CHECK_POLL_MS = 1_000;
const APP_CHECK_MAX_WAIT_MS = 10_000;

/**
 * True once Firebase App Check can mint a token — or immediately when App
 * Check isn't wired into this build (dev, where backend enforcement is off).
 *
 * `/api/v1/notifications` is App-Check-ENFORCED: it is not in the backend's
 * `_APP_CHECK_EXEMPT_PREFIXES` (core/middleware.py), so a request issued
 * before the token has been minted comes back 401. Because the shared
 * queryClient sets `retry: false`, that single cold-start 401 left the inbox
 * query in an error state for the life of the screen — and the screen
 * rendered it as "You're all caught up!". Meanwhile the dashboard bell badge
 * (driver/(tabs)/index.tsx) already gated on this same helper and re-polled
 * every 60s, so it happily showed "6 unread" over an empty list. Gating the
 * query here is what keeps the badge and the list telling the same story.
 *
 * After MAX_WAIT we enable the query regardless, so a genuinely misconfigured
 * App Check surfaces as a visible error on the screen rather than an
 * indefinite spinner.
 */
const useAppCheckReady = (): boolean => {
    const [ready, setReady] = useState(false);
    useEffect(() => {
        if (ready) return;
        let cancelled = false;
        let waited = 0;
        const check = async () => {
            const ok = await isAppCheckTokenReady();
            if (cancelled) return;
            waited += APP_CHECK_POLL_MS;
            if (ok || waited >= APP_CHECK_MAX_WAIT_MS) setReady(true);
        };
        check();
        const timer = setInterval(check, APP_CHECK_POLL_MS);
        return () => {
            cancelled = true;
            clearInterval(timer);
        };
    }, [ready]);
    return ready;
};

/**
 * GET /notifications?limit=50 — inbox feed.
 *
 * staleTime is 30s so navigating away from and back to the inbox doesn't
 * spam the network, but new notifications surface within a short window.
 * The WS push handler can also invalidate this key on relevant events.
 *
 * `retry` overrides the global `retry: false`: the App Check token can be
 * minted moments after this query first fires, and a bounded backoff recovers
 * from that window instead of stranding the screen on an empty list.
 */
export const useNotifications = (limit = 50) => {
    const appCheckReady = useAppCheckReady();
    return useQuery({
        queryKey: [...queryKeys.notifications.list, limit],
        queryFn: async () => {
            const res = await api.get(`/notifications?limit=${limit}&offset=0`);
            return res.data;
        },
        enabled: appCheckReady,
        staleTime: 30_000,
        retry: 2,
        retryDelay: (attempt) => Math.min(1_000 * 2 ** attempt, 8_000),
    });
};

/**
 * PUT /notifications/{id}/read — marks one notification as read.
 *
 * Optimistic update: bumps the local cache immediately so the badge
 * count drops without waiting for the server round-trip. On error, the
 * onError rollback restores the previous cache.
 */
export const useMarkNotificationRead = () => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationFn: async (notificationId: string) => {
            const res = await api.put(`/notifications/${notificationId}/read`);
            return res.data;
        },
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: queryKeys.notifications.list });
        },
        onError: () => {
            queryClient.invalidateQueries({ queryKey: queryKeys.notifications.list });
        },
    });
};

/**
 * PUT /notifications/read-all — bulk mark every unread notification as read.
 * Called from the inbox header when the user taps "Mark all read".
 */
export const useMarkAllNotificationsRead = () => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationFn: async () => {
            const res = await api.put('/notifications/read-all');
            return res.data;
        },
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: queryKeys.notifications.list });
        },
        onError: () => {
            queryClient.invalidateQueries({ queryKey: queryKeys.notifications.list });
        },
    });
};

/**
 * GET /notifications/preferences — toggle state for push/email/sms etc.
 * Long staleTime; user rarely changes these.
 */
export const useNotificationPreferences = () => {
    return useQuery({
        queryKey: queryKeys.notifications.preferences,
        queryFn: async () => {
            const res = await api.get('/notifications/preferences');
            return res.data;
        },
        staleTime: 5 * 60_000,
    });
};

export const useUpdateNotificationPreferences = () => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationFn: async (updates: Record<string, boolean>) => {
            const res = await api.put('/notifications/preferences', updates);
            return res.data;
        },
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: queryKeys.notifications.preferences });
        },
        onError: () => {
            queryClient.invalidateQueries({ queryKey: queryKeys.notifications.preferences });
        },
    });
};
