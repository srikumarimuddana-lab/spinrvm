// Driver document verification (approve/reject uploaded docs, manual admin
// upload) and driver notes. Extracted from the monolithic lib/api.ts as part
// of the per-domain split — see lib/api/client.ts for the shared fetch
// client this and every other domain module imports.

import { useAuthStore } from "@/store/authStore";

import { request } from "./client";

/* ── Driver Document Verification ────────── */
export const getDriverDocuments = (driverId: string) =>
    request<any[]>(`/api/admin/documents/drivers/${driverId}`);

/* ── Saving a document to disk ─────────────── */

const MIME_EXTENSIONS: Record<string, string> = {
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/gif": "gif",
    "image/webp": "webp",
    "application/pdf": "pdf",
};

/** Filename-safe slug: keeps letters/digits, collapses everything else to `_`. */
function slugify(value: string): string {
    return (value || "")
        .trim()
        .replace(/[^a-zA-Z0-9]+/g, "_")
        .replace(/^_+|_+$/g, "")
        .slice(0, 60);
}

/**
 * Download a driver document to disk.
 *
 * Goes through the backend's `/documents/{id}/view` rather than the stored
 * `document_url`. Two reasons that matter here: the stored URL is a signed
 * Supabase URL that expires (an hour after it was minted), and it can't
 * carry the admin's auth header — the backend route streams the bytes with
 * the service-role key, so it works regardless of the signature's age.
 *
 * A plain `<a download>` can't be used for the same reason: it issues an
 * unauthenticated request. Hence fetch-to-blob, then save.
 *
 * The filename is built for the thing admins actually do with these —
 * attaching them to an email to SGI — so it identifies the driver and the
 * document type rather than being a bare UUID.
 */
export async function downloadDriverDocument(
    documentId: string,
    driverName: string,
    documentType: string,
): Promise<void> {
    const store = useAuthStore.getState();
    const headers: Record<string, string> = {};
    if (store.token) headers["Authorization"] = `Bearer ${store.token}`;

    const res = await fetch(`/api/admin/documents/${documentId}/view`, { headers });
    if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `Could not download document (${res.status})`);
    }
    const blob = await res.blob();

    const ext = MIME_EXTENSIONS[blob.type] || "bin";
    const parts = [slugify(driverName), slugify(documentType)].filter(Boolean);
    const filename = `${parts.join("_") || "document"}.${ext}`;

    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
}

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
