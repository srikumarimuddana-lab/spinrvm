// Driver document verification (approve/reject uploaded docs, manual admin
// upload) and driver notes. Extracted from the monolithic lib/api.ts as part
// of the per-domain split — see lib/api/client.ts for the shared fetch
// client this and every other domain module imports.

import { request } from "./client";

/* ── Driver Document Verification ────────── */
export const getDriverDocuments = (driverId: string) =>
    request<any[]>(`/api/admin/documents/drivers/${driverId}`);

export type RejectTemplate =
    | "blurry_image"
    | "wrong_document_type"
    | "expired"
    | "information_unclear"
    | "other";

export interface ReviewDocumentOptions {
    notify?: boolean;
    notifyTemplate?: RejectTemplate;
}

export const reviewDocument = (
    docId: string,
    status: string,
    reason?: string,
    expiryDate?: string,
    options?: ReviewDocumentOptions,
) =>
    request<any>(`/api/admin/documents/${docId}/review`, {
        method: "POST",
        body: JSON.stringify({
            status,
            rejection_reason: reason,
            expiry_date: expiryDate,
            ...(options?.notify !== undefined ? { notify: options.notify } : {}),
            ...(options?.notifyTemplate ? { notify_template: options.notifyTemplate } : {}),
        }),
    });

/* ── Manual Admin Document Upload ─────────── */
export interface AdminUploadDocumentInput {
    driverId: string;
    requirementKey: string;
    file: File;
    side?: "front" | "back";
    expiryDate?: string;
    status?: "pending" | "approved";
}

/** Upload a document on a driver's behalf (pending, or committed straight to approved). */
export const adminUploadDriverDocument = (input: AdminUploadDocumentInput) => {
    const fd = new FormData();
    fd.append("file", input.file);
    fd.append("driver_id", input.driverId);
    fd.append("requirement_key", input.requirementKey);
    if (input.side) fd.append("side", input.side);
    if (input.expiryDate) fd.append("expiry_date", input.expiryDate);
    fd.append("status", input.status ?? "pending");
    return request<Record<string, unknown>>("/api/admin/documents/upload", {
        method: "POST",
        body: fd,
    });
};

/* ── Driver Notes ──────────────────────────── */
export const getDriverNotes = (driverId: string) =>
    request<any[]>(`/api/admin/drivers/${driverId}/notes`);

export const addDriverNote = (driverId: string, note: string, category: string = "general") =>
    request<any>(`/api/admin/drivers/${driverId}/notes`, {
        method: "POST",
        body: JSON.stringify({ note, category }),
    });

export const deleteDriverNote = (noteId: string) =>
    request<any>(`/api/admin/drivers/notes/${noteId}`, { method: "DELETE" });
