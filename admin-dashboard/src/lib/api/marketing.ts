// Cloud messaging (push campaigns), marketing suppression list, and promo
// usage/stats. Extracted from the monolithic lib/api.ts as part of the
// per-domain split.

import { request } from "./client";

/* ── Cloud Messaging (merged with Notifications) ── */
export const getCloudMessages = (status?: string, audience?: string) => {
    const params = new URLSearchParams();
    if (status) params.set('status', status);
    if (audience) params.set('audience', audience);
    return request<any[]>(`/api/admin/cloud-messaging?${params.toString()}`);
};

export const sendCloudMessage = (data: {
    title: string;
    description: string;
    audience: string;
    channels: string[];
    type?: string;
    particular_ids?: string[];
    scheduled_at?: string;
    is_marketing?: boolean;
    service_area_id?: string;
}) =>
    request<any>("/api/admin/cloud-messaging/send", {
        method: "POST",
        body: JSON.stringify(data),
    });

export const getCloudMessageStats = () =>
    request<any>("/api/admin/cloud-messaging/stats");

export const deleteCloudMessage = (id: string) =>
    request<any>(`/api/admin/cloud-messaging/${id}`, { method: "DELETE" });

/* ── Marketing audience preview + suppression list ──────────────────── */

export const getCloudMessageAudiencePreview = (audience: string, serviceAreaId?: string) => {
    const params = new URLSearchParams({ audience });
    if (serviceAreaId) params.set("service_area_id", serviceAreaId);
    return request<{
        audience: string;
        audience_total: number;
        email_opted_in: number | null;
        sms_opted_in: number | null;
        push_opted_in: number | null;
    }>(`/api/admin/cloud-messaging/audience-preview?${params.toString()}`);
};

export const getMarketingSuppressions = (channel?: string) => {
    const params = new URLSearchParams();
    if (channel) params.set("channel", channel);
    return request<any[]>(`/api/admin/marketing/suppressions?${params.toString()}`);
};

export const addMarketingSuppression = (data: { channel: string; target: string; reason?: string }) =>
    request<any>("/api/admin/marketing/suppressions", { method: "POST", body: JSON.stringify(data) });

export const deleteMarketingSuppression = (id: string) =>
    request<any>(`/api/admin/marketing/suppressions/${id}`, { method: "DELETE" });

/* ── Promotions Usage & Stats ──────────────────── */
export const getPromoUsage = (params?: { promo_id?: string; date_from?: string; date_to?: string; limit?: number; offset?: number }) => {
    const sp = new URLSearchParams();
    if (params?.promo_id) sp.set('promo_id', params.promo_id);
    if (params?.date_from) sp.set('date_from', params.date_from);
    if (params?.date_to) sp.set('date_to', params.date_to);
    if (params?.limit) sp.set('limit', params.limit.toString());
    if (params?.offset) sp.set('offset', params.offset.toString());
    return request<any[]>(`/api/admin/promotions/usage?${sp.toString()}`);
};

export const getPromoStats = (range?: string) => {
    const sp = new URLSearchParams();
    if (range) sp.set('range', range);
    return request<any>(`/api/admin/promotions/stats?${sp.toString()}`);
};

