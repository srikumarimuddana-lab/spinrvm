// Zoho Desk integration: config, sync, tickets/threads/replies/tags,
// agents/departments, Spinr-local service-area tagging (not synced to
// Zoho), and the AI reply-suggestion draft. Extracted from the
// monolithic lib/api.ts as part of the per-domain split.

import { request } from "./client";

/* ── Zoho Desk support tickets ──────────────────────────────────────────
 * Native admin UI proxied to Zoho Desk via the backend. Secrets are
 * write-only; getZohoConfig returns presence flags only. Gated by the
 * `support_tickets` RBAC module (config endpoints require any admin).
 */
export interface ZohoConfigStatus {
    enabled: boolean;
    auto_sync_enabled?: boolean;
    data_center: string;
    org_id: string;
    default_department_id: string;
    default_from_email: string;
    helpdesk_email_signature: string;
    helpdesk_signature_enabled: boolean;
    helpdesk_signature_preview: string;
    has_client_id: boolean;
    has_client_secret: boolean;
    has_refresh_token: boolean;
    connected: boolean;
    last_synced_at?: string | null;
    last_sync_count?: number | null;
    updated_at?: string | null;
}
export interface ZohoConfigUpdate {
    enabled?: boolean;
    auto_sync_enabled?: boolean;
    data_center?: string;
    org_id?: string;
    default_department_id?: string;
    default_from_email?: string;
    helpdesk_email_signature?: string;
    helpdesk_signature_enabled?: boolean;
    client_id?: string;
    client_secret?: string;
    refresh_token?: string;
}
export interface ZohoTicketsResponse {
    data: any[];
}
export interface ZohoDashboard {
    total: number | null;
    total_available: boolean;
    open: number;
    by_status: Record<string, number>;
    recent: any[];
    sample_size: number;
    approximate: boolean;
}
export interface ZohoTrends {
    sample_size: number;
    approximate: boolean;
    volume: Array<{ date: string; opened: number; closed: number }>;
    by_status: Record<string, number>;
    by_priority: Record<string, number>;
    by_channel: Record<string, number>;
    by_category: Record<string, number>;
    by_classification: Record<string, number>;
    by_tag: Record<string, number>;
    top_contacts: Array<{ name: string; count: number }>;
    stats: {
        opened: number;
        closed: number;
        open_now: number;
        avg_resolution_hours: number | null;
        median_resolution_hours: number | null;
        resolved_sample: number;
    };
}

export const getZohoConfig = () =>
    request<ZohoConfigStatus>("/api/admin/support-tickets/config");
export const updateZohoConfig = (body: ZohoConfigUpdate) =>
    request<ZohoConfigStatus>("/api/admin/support-tickets/config", {
        method: "PUT",
        body: JSON.stringify(body),
    });
export const testZohoConnection = () =>
    request<{ ok: boolean; departments: any[] }>("/api/admin/support-tickets/config/test", {
        method: "POST",
    });
export const syncDeskTickets = () =>
    request<{ upserted?: number; skipped?: string }>("/api/admin/support-tickets/sync", {
        method: "POST",
    });

export const getDeskDashboard = (departmentId?: string) => {
    const qs = departmentId ? `?department_id=${encodeURIComponent(departmentId)}` : "";
    return request<ZohoDashboard>(`/api/admin/support-tickets/dashboard${qs}`);
};
export const getDeskTrends = (opts?: { days?: number; departmentId?: string; assigneeId?: string }) => {
    const sp = new URLSearchParams();
    if (opts?.days) sp.set("days", String(opts.days));
    if (opts?.departmentId) sp.set("department_id", opts.departmentId);
    if (opts?.assigneeId) sp.set("assignee_id", opts.assigneeId);
    const qs = sp.toString();
    return request<ZohoTrends>(`/api/admin/support-tickets/trends${qs ? `?${qs}` : ""}`);
};

export const getDeskTickets = (opts?: {
    from?: number;
    limit?: number;
    status?: string;
    departmentId?: string;
    assigneeId?: string;
    priority?: string;
    channel?: string;
    sortBy?: string;
}) => {
    const sp = new URLSearchParams();
    if (opts?.from) sp.set("from", String(opts.from));
    if (opts?.limit) sp.set("limit", String(opts.limit));
    if (opts?.status) sp.set("status", opts.status);
    if (opts?.departmentId) sp.set("department_id", opts.departmentId);
    if (opts?.assigneeId) sp.set("assignee_id", opts.assigneeId);
    if (opts?.priority) sp.set("priority", opts.priority);
    if (opts?.channel) sp.set("channel", opts.channel);
    if (opts?.sortBy) sp.set("sort_by", opts.sortBy);
    const qs = sp.toString();
    return request<ZohoTicketsResponse>(`/api/admin/support-tickets/tickets${qs ? `?${qs}` : ""}`);
};
export interface CreateDeskTicket {
    subject: string;
    description?: string;
    email?: string;
    first_name?: string;
    last_name?: string;
    phone?: string;
    priority?: string;
    channel?: string;
    category?: string;
    department_id?: string;
}
export const createDeskTicket = (body: CreateDeskTicket) =>
    request<any>(`/api/admin/support-tickets/tickets`, {
        method: "POST",
        body: JSON.stringify(body),
    });
export const searchDeskTickets = (opts: {
    q: string;
    from?: number;
    limit?: number;
    departmentId?: string;
    status?: string;
    priority?: string;
    assigneeId?: string;
}) => {
    const sp = new URLSearchParams();
    sp.set("q", opts.q);
    if (opts.from) sp.set("from", String(opts.from));
    if (opts.limit) sp.set("limit", String(opts.limit));
    if (opts.departmentId) sp.set("department_id", opts.departmentId);
    if (opts.status) sp.set("status", opts.status);
    if (opts.priority) sp.set("priority", opts.priority);
    if (opts.assigneeId) sp.set("assignee_id", opts.assigneeId);
    return request<ZohoTicketsResponse>(`/api/admin/support-tickets/search?${sp.toString()}`);
};
export const getDeskTicket = (id: string) =>
    request<any>(`/api/admin/support-tickets/tickets/${id}`);
export const getDeskTicketThreads = (id: string) =>
    request<ZohoTicketsResponse>(`/api/admin/support-tickets/tickets/${id}/threads`);
export const getDeskThread = (ticketId: string, threadId: string) =>
    request<any>(`/api/admin/support-tickets/tickets/${ticketId}/threads/${threadId}`);
export const replyDeskTicket = (id: string, body: { content: string; to?: string; channel?: string }) =>
    request<any>(`/api/admin/support-tickets/tickets/${id}/reply`, {
        method: "POST",
        body: JSON.stringify(body),
    });
export const commentDeskTicket = (id: string, body: { content: string; is_public?: boolean }) =>
    request<any>(`/api/admin/support-tickets/tickets/${id}/comment`, {
        method: "POST",
        body: JSON.stringify(body),
    });
export const updateDeskTicket = (
    id: string,
    body: {
        status?: string;
        priority?: string;
        assigneeId?: string;
        departmentId?: string;
        category?: string;
        subCategory?: string;
        classification?: string;
    },
) =>
    request<any>(`/api/admin/support-tickets/tickets/${id}`, {
        method: "PATCH",
        body: JSON.stringify(body),
    });
export const updateDeskTicketTags = (id: string, body: { add?: string[]; remove?: string[] }) =>
    request<{ ok: boolean }>(`/api/admin/support-tickets/tickets/${id}/tags`, {
        method: "POST",
        body: JSON.stringify(body),
    });
export const getDeskAgents = () =>
    request<ZohoTicketsResponse>("/api/admin/support-tickets/agents");
export const getDeskDepartments = () =>
    request<ZohoTicketsResponse>("/api/admin/support-tickets/departments");

/* ── Service area (Spinr-local; not synced to Zoho) ─────────────────────── */
export interface DeskServiceArea {
    id: string;
    name?: string;
    city?: string;
    province?: string;
}
export interface DeskTicketServiceArea {
    service_area_id?: string | null;
    service_area_name?: string | null;
    service_area_source?: "auto" | "manual" | null;
    service_area_assigned_at?: string | null;
    service_area_assigned_by?: string | null;
    needs_assignment?: boolean;
    suggested?: { service_area_id: string; service_area_name?: string; matched_user_id?: string } | null;
}
export const getDeskServiceAreas = () =>
    request<{ data: DeskServiceArea[] }>("/api/admin/support-tickets/service-areas");
export const getDeskTicketServiceArea = (id: string) =>
    request<DeskTicketServiceArea>(`/api/admin/support-tickets/tickets/${id}/service-area`);
export const setDeskTicketServiceArea = (id: string, service_area_id: string | null) =>
    request<DeskTicketServiceArea>(`/api/admin/support-tickets/tickets/${id}/service-area`, {
        method: "PUT",
        body: JSON.stringify({ service_area_id }),
    });

/* ── AI reply suggestion (draft only; agent reviews + sends) ────────────── */
export const aiSuggestDeskReply = (id: string, instruction?: string) =>
    request<{ reply: string; provider?: string; model?: string }>(
        `/api/admin/support-tickets/tickets/${id}/ai-suggest-reply`,
        {
            method: "POST",
            // Optional agent guidance steering the draft. Omit the body when
            // there's nothing to add so the endpoint behaves exactly as before.
            ...(instruction && instruction.trim()
                ? { body: JSON.stringify({ instruction: instruction.trim() }) }
                : {}),
        },
    );

