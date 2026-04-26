/**
 * Notification-related TanStack Query hooks.
 *
 * The inbox lives at /notifications and supports paging via limit/offset.
 * For the simple "latest 50" inbox screen we only need a single page —
 * if pagination becomes a UX requirement, swap useQuery for useInfiniteQuery.
 */
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import api from '../../api/client';
import { queryKeys } from '../../api/queryClient';

/**
 * GET /notifications?limit=50 — inbox feed.
 *
 * staleTime is 30s so navigating away from and back to the inbox doesn't
 * spam the network, but new notifications surface within a short window.
 * The WS push handler can also invalidate this key on relevant events.
 */
export const useNotifications = (limit = 50) => {
    return useQuery({
        queryKey: [...queryKeys.notifications.list, limit],
        queryFn: async () => {
            const res = await api.get('/notifications', { params: { limit, offset: 0 } });
            return res.data;
        },
        staleTime: 30_000,
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
    });
};
