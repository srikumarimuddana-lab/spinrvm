// Admin review queue for driver deactivation appeals. Backs
// docs/legal/driver-deactivation-appeals-policy.md — see
// backend/routes/admin/driver_appeals.py for the endpoints this calls.

import { request } from "./client";

export interface DriverAppeal {
    id: string;
    driver_id: string;
    appeal_type: "suspension" | "ban" | "needs_review" | "other";
    driver_message: string;
    original_reason: string | null;
    status: "pending" | "approved" | "denied";
    admin_note: string | null;
    resolved_by: string | null;
    resolved_at: string | null;
    created_at: string;
    updated_at: string;
}

export interface DriverAppealStats {
    pending: number;
    approved: number;
    denied: number;
}

export const getDriverAppeals = (status?: string) => {
    const qs = status && status !== "all" ? `?status=${encodeURIComponent(status)}` : "";
    return request<DriverAppeal[]>(`/api/admin/driver-appeals${qs}`);
};

export const getDriverAppealStats = () =>
    request<DriverAppealStats>("/api/admin/driver-appeals/stats");

export const resolveDriverAppeal = (
    appealId: string,
    data: { decision: "approved" | "denied"; admin_note?: string }
) =>
    request<{ appeal_id: string; status: string; driver_reactivated: boolean }>(
        `/api/admin/driver-appeals/${appealId}/resolve`,
        { method: "POST", body: JSON.stringify(data) }
    );
