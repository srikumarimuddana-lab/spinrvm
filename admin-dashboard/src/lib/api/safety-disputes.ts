// Promotions CRUD, ride disputes, the safety incident queue, and support
// tickets. Extracted from the monolithic lib/api.ts as part of the
// per-domain split. (deleteDispute lives in api/rides.ts — see its header
// comment; the rest of the disputes CRUD is here.)

import { request } from "./client";

/* ── Promotions ─────────────────────────────── */
export const getPromotions = (opts: {
    limit?: number;
    offset?: number;
    promo_type?: "public" | "private";
    status?: "active" | "inactive" | "not_expired" | "expired";
    search?: string;
} = {}) => {
    const sp = new URLSearchParams();
    if (opts.limit != null) sp.set("limit", String(opts.limit));
    if (opts.offset != null) sp.set("offset", String(opts.offset));
    if (opts.promo_type) sp.set("promo_type", opts.promo_type);
    if (opts.status) sp.set("status", opts.status);
    if (opts.search) sp.set("search", opts.search);
    const qs = sp.toString();
    return request<any[]>(`/api/admin/promotions${qs ? `?${qs}` : ""}`);
};

export const createPromotion = (data: any) =>
    request<any>("/api/admin/promotions", {
        method: "POST",
        body: JSON.stringify(data),
    });

export const updatePromotion = (id: string, data: any) =>
    request<any>(`/api/admin/promotions/${id}`, {
        method: "PUT",
        body: JSON.stringify(data),
    });

export const deletePromotion = (id: string) =>
    request<any>(`/api/admin/promotions/${id}`, { method: "DELETE" });

/* ── Disputes ───────────────────────────────── */
export const getDisputes = (opts: { limit?: number; offset?: number; status?: string } = {}) => {
    const sp = new URLSearchParams();
    if (opts.limit != null) sp.set("limit", String(opts.limit));
    if (opts.offset != null) sp.set("offset", String(opts.offset));
    if (opts.status && opts.status !== "all") sp.set("status", opts.status);
    const qs = sp.toString();
    return request<any[]>(`/api/admin/disputes${qs ? `?${qs}` : ""}`);
};

export const getDisputeStats = () =>
    request<{ open: number; under_review: number; resolved: number; rejected: number; total_refunded: number }>(
        "/api/admin/disputes/stats"
    );

export const getDisputeDetails = (id: string) =>
    request<any>(`/api/admin/disputes/${id}`);

/* ── Chargebacks (card-network disputes, C23) ──
   Distinct from the rider-raised `disputes` above — these come from
   Stripe's dispute webhooks (`stripe_disputes` table), not rider-filed
   refund requests. Read-only: chargebacks are resolved via the Stripe
   Dashboard today. */
export interface Chargeback {
    id: string;
    stripe_dispute_id: string;
    ride_id: string | null;
    ride_code: string | null;
    amount_cents: number;
    reason: string;
    status: string;
    evidence_due_by: string | null;
    evidence_submitted_at: string | null;
    days_remaining: number | null;
    created_at: string;
    updated_at: string;
}

export const getChargebacks = (opts: { limit?: number; offset?: number; status?: string } = {}) => {
    const sp = new URLSearchParams();
    if (opts.limit != null) sp.set("limit", String(opts.limit));
    if (opts.offset != null) sp.set("offset", String(opts.offset));
    if (opts.status && opts.status !== "all") sp.set("status", opts.status);
    const qs = sp.toString();
    return request<Chargeback[]>(`/api/admin/disputes/chargebacks${qs ? `?${qs}` : ""}`);
};

// GET /api/admin/rides/{ride_id}/dispute-pack (C23 item 4) returns a binary
// zip, not JSON -- can't go through request<T>(), same reason
// downloadDriverStatement (api/drivers.ts) fetches directly with a manual
// Authorization header instead.
export async function downloadDisputeEvidencePack(
    rideId: string,
    rideCode: string | null,
): Promise<{ blob: Blob; filename: string }> {
    const { useAuthStore } = await import("@/store/authStore");
    const token = useAuthStore.getState().token;
    const headers: Record<string, string> = {};
    if (token) headers["Authorization"] = `Bearer ${token}`;
    const res = await fetch(`/api/admin/rides/${rideId}/dispute-pack`, { headers });
    if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `Could not generate evidence pack (${res.status})`);
    }
    return { blob: await res.blob(), filename: `dispute_evidence_${rideCode || rideId}.zip` };
}

// POST /api/admin/disputes/{dispute_id}/submit-evidence (C23 item 5) --
// ships dark behind an app_settings flag and requires confirm:true; see
// routes/admin/dispute_evidence_submission.py's module docstring for the
// full safety-gate rationale. super_admin only.
export interface SubmitDisputeEvidenceResult {
    submitted: boolean;
    stripe_dispute_id: string;
    dispute_id: string;
}

export const submitDisputeEvidence = (disputeId: string, uncategorizedText?: string) =>
    request<SubmitDisputeEvidenceResult>(`/api/admin/disputes/${disputeId}/submit-evidence`, {
        method: "POST",
        body: JSON.stringify({
            confirm: true,
            ...(uncategorizedText ? { uncategorized_text: uncategorizedText } : {}),
        }),
    });

export const createDispute = (data: any) =>
    request<any>("/api/admin/disputes", {
        method: "POST",
        body: JSON.stringify(data),
    });

export const updateDispute = (id: string, data: any) =>
    request<any>(`/api/admin/disputes/${id}`, {
        method: "PUT",
        body: JSON.stringify(data),
    });

/* ── Safety Queue ───────────────────────────── */
export type SafetyStatus = "open" | "in_progress" | "resolved" | "closed" | "duplicate";
export type SafetySeverity = "sev1" | "sev2" | "sev3";
export type SafetyRole = "rider" | "driver" | "system";

export interface SafetyIncident {
    id: string;
    reported_by_user_id: string | null;
    role: SafetyRole;
    category: string;
    description: string;
    status: SafetyStatus;
    severity: SafetySeverity | null;
    ride_id: string | null;
    latitude: number | null;
    longitude: number | null;
    location_accuracy: number | null;
    assigned_to_admin_id: string | null;
    resolved_at: string | null;
    resolved_by: string | null;
    resolution_notes: string | null;
    // Migration 279 — set when this incident was merged into another
    // (status becomes "duplicate"); null otherwise.
    merged_into_incident_id: string | null;
    reported_at: string;
    created_at: string;
    updated_at: string;
    reporter_name?: string | null;
}

export interface SafetyIncidentListResponse {
    items: SafetyIncident[];
    total: number;
    offset: number;
    limit: number;
    open_count: number | null;
}

// Evidence photo attached to an incident (migration 340). `url` is a
// short-lived signed URL minted per request — the backend stores only the
// storage key, so these expire and must not be cached or persisted.
// `url` is null when signing failed: the photo EXISTS but could not be
// served, which the UI must show rather than silently omit.
export interface SafetyIncidentPhoto {
    id: string;
    content_type: string | null;
    created_at: string | null;
    url: string | null;
}

export interface SafetyIncidentDetail {
    incident: SafetyIncident;
    reporter: {
        id: string | null;
        name: string | null;
        email: string | null;
        phone: string | null;
        role: string | null;
    } | null;
    ride: {
        id: string | null;
        ride_code: string | null;
        status: string | null;
        rider_id: string | null;
        driver_id: string | null;
        pickup_address: string | null;
        dropoff_address: string | null;
        total_fare: number | null;
        started_at: string | null;
        completed_at: string | null;
    } | null;
    // Optional: an older backend (pre-migration-340 deploy) omits this key
    // entirely, so every consumer must tolerate undefined.
    photos?: SafetyIncidentPhoto[];
}

export const getSafetyIncidents = (params?: {
    status?: SafetyStatus;
    severity?: SafetySeverity;
    role?: SafetyRole;
    category?: string;
    ride_id?: string;
    search?: string;
    limit?: number;
    offset?: number;
}) => {
    const sp = new URLSearchParams();
    if (params?.status) sp.set("status", params.status);
    if (params?.severity) sp.set("severity", params.severity);
    if (params?.role) sp.set("role", params.role);
    if (params?.category) sp.set("category", params.category);
    if (params?.ride_id) sp.set("ride_id", params.ride_id);
    if (params?.search) sp.set("search", params.search);
    if (params?.limit != null) sp.set("limit", String(params.limit));
    if (params?.offset != null) sp.set("offset", String(params.offset));
    const qs = sp.toString();
    return request<SafetyIncidentListResponse>(`/api/admin/safety/incidents${qs ? `?${qs}` : ""}`);
};

export const getSafetyIncident = (id: string) =>
    request<SafetyIncidentDetail>(`/api/admin/safety/incidents/${id}`);

export const updateSafetyIncident = (
    id: string,
    body: Partial<{
        status: SafetyStatus;
        severity: SafetySeverity;
        assigned_to_admin_id: string;
        resolution_notes: string;
    }>,
) =>
    request<{ updated: boolean; incident: SafetyIncident }>(
        `/api/admin/safety/incidents/${id}`,
        { method: "PATCH", body: JSON.stringify(body) },
    );

// Corporate + admin portal review, round 2: "safety incidents can't be
// created or merged from the admin side."
export const createSafetyIncident = (body: {
    category: string;
    description: string;
    role?: SafetyRole;
    reported_by_user_id?: string;
    ride_id?: string;
    severity?: SafetySeverity;
    reported_at?: string;
}) =>
    request<{ incident: SafetyIncident }>("/api/admin/safety/incidents", {
        method: "POST",
        body: JSON.stringify(body),
    });

export const mergeSafetyIncident = (id: string, canonicalIncidentId: string) =>
    request<{ merged: boolean; incident: SafetyIncident }>(
        `/api/admin/safety/incidents/${id}/merge`,
        { method: "POST", body: JSON.stringify({ canonical_incident_id: canonicalIncidentId }) },
    );


/* ── Support Tickets ────────────────────────── */
export const getTickets = (opts: {
    limit?: number;
    offset?: number;
    status?: string;
    service_area_id?: string;
} = {}) => {
    const sp = new URLSearchParams();
    if (opts.limit != null) sp.set("limit", String(opts.limit));
    if (opts.offset != null) sp.set("offset", String(opts.offset));
    if (opts.status) sp.set("status", opts.status);
    if (opts.service_area_id) sp.set("service_area_id", opts.service_area_id);
    const qs = sp.toString();
    return request<any[]>(`/api/admin/tickets${qs ? `?${qs}` : ""}`);
};

export const getTicketDetails = (id: string) =>
    request<any>(`/api/admin/tickets/${id}`);

export const createTicket = (data: any) =>
    request<any>("/api/admin/tickets", {
        method: "POST",
        body: JSON.stringify(data),
    });

export const updateTicket = (id: string, data: any) =>
    request<any>(`/api/admin/tickets/${id}`, {
        method: "PUT",
        body: JSON.stringify(data),
    });

export const replyToTicket = (id: string, message: string) =>
    request<any>(`/api/admin/tickets/${id}/reply`, {
        method: "POST",
        body: JSON.stringify({ message }),
    });

export const closeTicket = (id: string) =>
    request<any>(`/api/admin/tickets/${id}/close`, { method: "POST" });

export const deleteTicket = (id: string) =>
    request<any>(`/api/admin/tickets/${id}`, { method: "DELETE" });

