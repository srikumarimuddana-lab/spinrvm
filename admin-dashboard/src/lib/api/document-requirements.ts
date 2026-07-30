// Document requirement config, paginated pending-documents queue, and
// payout retry (single + bulk). Extracted from the monolithic lib/api.ts
// as part of the per-domain split.

import { request } from "./client";

/* ── Document Requirements (A-P4-1) ─────── */
export const getDocumentRequirements = () =>
    request<any[]>("/api/admin/documents/requirements");

export const createDocumentRequirement = (data: {
    name: string;
    description?: string;
    document_type?: string;
    is_required?: boolean;
    applicable_to?: string;
}) => request<any>("/api/admin/documents/requirements", { method: "POST", body: JSON.stringify(data) });

export const updateDocumentRequirement = (id: string, data: Partial<{
    name: string;
    description: string;
    document_type: string;
    is_required: boolean;
    applicable_to: string;
}>) => request<any>(`/api/admin/documents/requirements/${id}`, { method: "PUT", body: JSON.stringify(data) });

export const deleteDocumentRequirement = (id: string) =>
    request<any>(`/api/admin/documents/requirements/${id}`, { method: "DELETE" });

/* ── Pending Documents paginated (A-P4-4) ── */
export const getPendingDocuments = (params?: { limit?: number; cursor?: string; status?: string }) => {
    const sp = new URLSearchParams();
    if (params?.limit) sp.set("limit", String(params.limit));
    if (params?.cursor) sp.set("cursor", params.cursor);
    if (params?.status) sp.set("status", params.status);
    const qs = sp.toString();
    return request<{ items: any[]; next_cursor: string | null }>(`/api/admin/documents/pending${qs ? `?${qs}` : ""}`);
};

/* ── Payout retry (A-P4-2) ──────────────── */
export const retryPayout = (id: string) =>
    request<any>(`/api/admin/payouts/${id}/retry`, { method: "POST" });

export interface BulkRetryPayoutsRequest {
    payout_ids?: string[];
    since?: string;
    service_area_id?: string;
    max_to_retry?: number;
}
export interface BulkRetryPayoutsResponse {
    retried: number;
    skipped: number;
    failed_to_initiate: number;
    details: Array<{ payout_id: string; status: string; reason?: string }>;
}
export const bulkRetryPayouts = (body: BulkRetryPayoutsRequest) =>
    request<BulkRetryPayoutsResponse>(`/api/admin/payouts/bulk-retry`, {
        method: "POST",
        body: JSON.stringify(body),
    });

