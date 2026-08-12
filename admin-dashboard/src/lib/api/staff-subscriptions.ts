// Staff management, Spinr Pass subscription plans/payments, audit logs,
// and quests/bonus challenges. Extracted from the monolithic lib/api.ts
// as part of the per-domain split.

import { request } from "./client";
import { useAuthStore } from "@/store/authStore";

/* ── Staff Management ──────────────────────── */
export const getStaff = () =>
    request<any[]>("/api/admin/staff");

export const createStaff = (data: { email: string; password: string; first_name: string; last_name: string; role: string; modules?: string[] }) =>
    request<any>("/api/admin/staff", { method: "POST", body: JSON.stringify(data) });

export const updateStaff = (id: string, data: any) =>
    request<any>(`/api/admin/staff/${id}`, { method: "PUT", body: JSON.stringify(data) });

export const deleteStaff = (id: string) =>
    request<any>(`/api/admin/staff/${id}`, { method: "DELETE" });

// Lost-phone recovery: super_admin clears a staff member's MFA so they can
// re-enroll. Backend revokes the target's sessions and audit-logs the action.
export const resetStaffMfa = (id: string) =>
    request<{ success: boolean }>(`/api/admin/staff/${id}/mfa-reset`, { method: "POST" });

export const getStaffModules = () =>
    request<{ modules: string[]; role_presets: Record<string, string[]> }>("/api/admin/staff/modules/list");

/* ── Spinr Pass — Subscription Plans ──────── */
export const getSubscriptionPlans = () =>
    request<any[]>("/api/admin/subscription-plans");

export const createSubscriptionPlan = (data: any) =>
    request<any>("/api/admin/subscription-plans", { method: "POST", body: JSON.stringify(data) });

export const updateSubscriptionPlan = (id: string, data: any) =>
    request<any>(`/api/admin/subscription-plans/${id}`, { method: "PUT", body: JSON.stringify(data) });

export const deleteSubscriptionPlan = (id: string) =>
    request<any>(`/api/admin/subscription-plans/${id}`, { method: "DELETE" });

export const getDriverSubscriptions = (status?: string) =>
    request<any[]>(`/api/admin/driver-subscriptions${status ? `?status=${status}` : ''}`);

export const getAdminSubscriptionPayments = (opts: {
    limit?: number;
    offset?: number;
    driver_id?: string;
    plan_id?: string;
    billing_reason?: string;
    start_date?: string;
    end_date?: string;
} = {}) => {
    const sp = new URLSearchParams();
    if (opts.limit) sp.set("limit", String(opts.limit));
    if (opts.offset) sp.set("offset", String(opts.offset));
    if (opts.driver_id) sp.set("driver_id", opts.driver_id);
    if (opts.plan_id) sp.set("plan_id", opts.plan_id);
    if (opts.billing_reason) sp.set("billing_reason", opts.billing_reason);
    if (opts.start_date) sp.set("start_date", opts.start_date);
    if (opts.end_date) sp.set("end_date", opts.end_date);
    return request<any>(`/api/admin/subscription/payments?${sp.toString()}`);
};

/** Download the Spinr Pass invoice PDF for one subscription payment and trigger
 *  a browser save. Returns true on success. The request<T> helper assumes JSON,
 *  so use a raw authed fetch like getRideRouteMapDataUrl. */
export const downloadSubscriptionInvoice = async (paymentId: string): Promise<boolean> => {
    const token = useAuthStore.getState().token;
    try {
        const res = await fetch(`/api/admin/subscription/payments/${paymentId}/invoice.pdf`, {
            headers: token ? { Authorization: `Bearer ${token}` } : {},
        });
        if (!res.ok) return false;
        const blob = await res.blob();
        const cd = res.headers.get("content-disposition") || "";
        const filename = cd.match(/filename="?([^"]+)"?/)?.[1] || `SpinrPass_Invoice_${paymentId}.pdf`;
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(url);
        return true;
    } catch (e) {
        if (process.env.NODE_ENV === "development") console.log("Invoice download failed:", e);
        return false;
    }
};

/** Email the Spinr Pass invoice to the driver's address on file (admin-triggered). */
export const resendAdminSubscriptionInvoice = (paymentId: string) =>
    request<{ success: boolean }>(`/api/admin/subscription/payments/${paymentId}/resend-invoice`, {
        method: "POST",
    });

export const updateSubscriptionTaxConfig = (
    areaId: string,
    config: { enabled: boolean; province: string; gst_rate: number; pst_rate: number; hst_rate: number },
) =>
    request<any>(`/api/admin/service-areas/${areaId}/subscription-tax`, {
        method: "PUT",
        body: JSON.stringify(config),
    });

/* ── Audit Logs ──────────────────────────── */
export const getAuditLogs = (opts: {
    limit?: number;
    offset?: number;
    action?: string;
    entity_type?: string;
    search?: string;
} = {}) => {
    const sp = new URLSearchParams();
    sp.set("limit", String(opts.limit ?? 50));
    sp.set("offset", String(opts.offset ?? 0));
    if (opts.action) sp.set("action", opts.action);
    if (opts.entity_type) sp.set("entity_type", opts.entity_type);
    if (opts.search) sp.set("search", opts.search);
    return request<any[]>(`/api/admin/audit-logs?${sp.toString()}`);
};

// Corporate + admin portal review, round 2: "no 'who touched the most'
// rollup views — every threat hunt needs raw SQL."
export const getAuditLogTopActors = (opts: { days?: number; limit?: number } = {}) => {
    const sp = new URLSearchParams();
    sp.set("days", String(opts.days ?? 7));
    sp.set("limit", String(opts.limit ?? 20));
    return request<{
        days: number;
        window_start: string;
        rows_scanned: number;
        rows_scanned_capped: boolean;
        actors: Array<{
            actor_id: string;
            action_count: number;
            top_actions: Array<{ action: string; count: number }>;
        }>;
    }>(`/api/admin/audit-logs/top-actors?${sp.toString()}`);
};

/* ── Quests / Bonus Challenges ──────────── */
export const getQuests = (isActive?: boolean) =>
    request<any[]>(`/api/v1/quests/admin/list${isActive !== undefined ? `?is_active=${isActive}` : ''}`);

export const createQuest = (data: any) =>
    request<any>("/api/v1/quests/admin/create", { method: "POST", body: JSON.stringify(data) });

export const updateQuest = (id: string, data: any) =>
    request<any>(`/api/v1/quests/admin/${id}`, { method: "PATCH", body: JSON.stringify(data) });

export const getQuestParticipants = (questId: string) =>
    request<any[]>(`/api/v1/quests/admin/${questId}/participants`);

