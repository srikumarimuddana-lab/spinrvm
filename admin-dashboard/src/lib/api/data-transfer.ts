// Data Transfer module (backend/routes/admin/data_transfer_export.py):
// cross-entity search/export/import, SGI forms, compliance report
// downloads (GST/PST remittance, insurance billing, driver roster,
// airport trips), and background export job tracking. Extracted from the
// monolithic lib/api.ts as part of the per-domain split — this was the
// last remaining section. Reports are download-only (no email-delivery
// option) — an admin downloads and forwards through their own channel.

import { request } from "./client";
import { useAuthStore } from "@/store/authStore";

/* ── Data Transfer module (backend/routes/admin/data_transfer_*.py) ─────── */
export interface DataTransferEntityRow {
    id: string;
    user_id?: string;
    full_name?: string;
    email?: string;
    phone?: string;
    created_at?: string;
    role?: string;
    vehicle_plate?: string;
    status?: string;
}
export interface DataTransferSearchResult {
    rows: DataTransferEntityRow[];
    total_count: number;
    page: number;
    page_size: number;
}
export interface DataTransferSearchParams {
    q?: string;
    entityType?: "driver" | "rider";
    dateFrom?: string;
    dateTo?: string;
    // Exact match on drivers.status/users.status -- free-text, not a shared
    // enum, since the two tables' status vocabularies aren't identical (see
    // backend/routes/admin/data_transfer_search.py's status param comment).
    status?: string;
    // Only meaningful when entityType is "driver" (or omitted/"all", where it
    // still only narrows the driver rows) -- `users` has no service_area_id
    // column, see data_transfer_search.py's driver-only branch.
    serviceAreaId?: string;
    page?: number;
    pageSize?: number;
}
export const searchDataTransferEntities = (params: DataTransferSearchParams) => {
    const qs = new URLSearchParams();
    if (params.q) qs.set("q", params.q);
    if (params.entityType) qs.set("entity_type", params.entityType);
    if (params.dateFrom) qs.set("date_from", params.dateFrom);
    if (params.dateTo) qs.set("date_to", params.dateTo);
    if (params.status) qs.set("status", params.status);
    if (params.serviceAreaId) qs.set("service_area_id", params.serviceAreaId);
    qs.set("page", String(params.page ?? 1));
    qs.set("page_size", String(params.pageSize ?? 50));
    return request<DataTransferSearchResult>(`/api/admin/data-transfer/search?${qs.toString()}`);
};

export type DataTransferExportFormat = "zip" | "csv" | "json" | "excel";
export interface DataTransferExportEntityRef {
    entity_type: "driver" | "rider";
    entity_id: string;
}
// The export route is backgrounded (see backend/routes/admin/data_transfer_export.py) --
// it returns only a job_id immediately; the caller polls getDataTransferJob
// until status leaves "pending", then fetches the download link separately.
export interface DataTransferExportQueuedResult {
    job_id: string;
    status: "pending";
    requested_count: number;
}
/** ACTION_ITEMS.md B10 dual-approval gate — returned instead of
 * DataTransferExportQueuedResult when settings.dual_approval_exports_enabled
 * is on and this export needs a second admin's sign-off before the job
 * even starts. Check `"approval_required" in result` to distinguish. */
export interface ExportApprovalRequiredResult {
    approval_required: true;
    request_id: string;
    status: "pending" | "approved" | "denied" | "expired" | "consumed";
    row_count: number;
}
export interface DataTransferExportScopeOptions {
    /** Which document types appear in the bundle at all (as metadata rows).
     * Omit for every type. */
    docTypes?: string[];
    // PIA recommendation R-B (ACTION_ITEMS.md B11) — default true (current
    // full-fidelity behavior) on both; the backend's ExportRequest defaults
    // match, so omitting these entirely is still safe/backward-compatible.
    includeRideGps?: boolean;
    includeDocumentBytes?: boolean;
    /** Which document types have their actual FILE (scan/image/PDF) bundled,
     * as opposed to a metadata row only. Overrides includeDocumentBytes on
     * the backend when present; pass `[]` for metadata-only. Omit entirely to
     * keep the older all-or-nothing includeDocumentBytes behavior. */
    docFileTypes?: string[];
}
export const exportDataTransferEntities = (
    entities: DataTransferExportEntityRef[],
    format: DataTransferExportFormat,
    reason: string,
    options?: DataTransferExportScopeOptions,
) =>
    request<DataTransferExportQueuedResult | ExportApprovalRequiredResult>("/api/admin/data-transfer/export", {
        method: "POST",
        body: JSON.stringify({
            entities,
            format,
            doc_types: options?.docTypes ?? null,
            reason,
            include_ride_gps: options?.includeRideGps ?? true,
            include_document_bytes: options?.includeDocumentBytes ?? true,
            // null (not undefined) so an explicit empty array survives
            // JSON.stringify and reaches the backend as "metadata only"
            // rather than being dropped from the payload entirely.
            doc_file_types: options?.docFileTypes ?? null,
        }),
    });

export interface DataTransferImportReportItem {
    entity_id: string;
    field: string;
    message: string;
}
export interface DataTransferImportReport {
    can_commit: boolean;
    counts: { entities: number; new: number; existing_match: number; conflict: number };
    warnings: DataTransferImportReportItem[];
    errors: DataTransferImportReportItem[];
}
export interface DataTransferImportCommitResult extends DataTransferImportReport {
    committed: boolean;
    created_users?: number;
    created_drivers?: number;
    updated_users?: number;
    updated_drivers?: number;
    documents_replayed?: number;
    insurance_periods_replayed?: number;
}
export const adminValidateDataTransferImport = (file: File) => {
    const fd = new FormData();
    fd.append("bundle_zip", file);
    return request<DataTransferImportReport>("/api/admin/data-transfer/import/validate", {
        method: "POST",
        body: fd,
    });
};
export const adminCommitDataTransferImport = (file: File, batch?: string, updateExisting?: boolean) => {
    const fd = new FormData();
    fd.append("bundle_zip", file);
    if (batch) fd.append("batch", batch);
    if (updateExisting) fd.append("update_existing", "true");
    return request<DataTransferImportCommitResult>("/api/admin/data-transfer/import/commit", {
        method: "POST",
        body: fd,
    });
};

export type SgiFormType = "driver_details" | "vehicle_details";

/** A driver who left but is still filed with the regulator. Spinr stops
 *  dispatching the moment they delete their account, but SGI keeps listing
 *  them as an active passenger-for-hire driver until the D00032 removal row
 *  is filed — and their vehicle until D00033. */
export interface SgiRemovalQueueEntry {
    /** users.id — the id the generate endpoint and Search & Select take. */
    entity_id: string | null;
    driver_id: string;
    name: string;
    license_plate: string;
    regulatory_authority: string | null;
    /** Date the driver actually stopped, used as the removal's effective date. */
    effective_date: string | null;
    driver_form_filed_at: string | null;
    vehicle_form_filed_at: string | null;
    driver_form_outstanding: boolean;
    vehicle_form_outstanding: boolean;
}
export interface SgiRemovalQueue {
    drivers: SgiRemovalQueueEntry[];
    count: number;
    /** Queue entries with no linked users row — they cannot be selected for
     *  form generation, so they would never clear on their own. */
    unresolvable: number;
}
export const getSgiRemovalQueue = (includeFiled = false) =>
    request<SgiRemovalQueue>(
        `/api/admin/data-transfer/sgi-forms/removal-queue${includeFiled ? "?include_filed=true" : ""}`,
    );
// PDF binary response — can't use the generic request<T>() helper (it always
// calls res.json()). Mirrors fetchKybDocumentBlob's manual fetch + auth
// header pattern, adding the CSRF header this call needs since it's a POST.
export async function generateSgiForm(
    formType: SgiFormType,
    driverIds: string[],
    action: "add" | "remove" | "change" = "add",
): Promise<Blob> {
    const store = useAuthStore.getState();
    const headers: Record<string, string> = { "Content-Type": "application/json" };
    if (store.token) headers["Authorization"] = `Bearer ${store.token}`;
    if (store.csrfToken) headers["X-CSRF-Token"] = store.csrfToken;
    const res = await fetch("/api/admin/data-transfer/sgi-forms/generate", {
        method: "POST",
        headers,
        body: JSON.stringify({ form_type: formType, driver_ids: driverIds, action }),
    });
    if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `Could not generate form (${res.status})`);
    }
    return res.blob();
}

export interface SgiSupportingDocumentsResult {
    blob: Blob;
    /** Documents listed in the bundle, and how many of those actually have a
     *  file in it. Read from the response headers so the caller can say
     *  "12 of 14 included" without unzipping; the ZIP's own documents.csv
     *  carries the per-document reason. */
    listed: number;
    included: number;
}

/** ZIP of the selected drivers' supporting scans, for filing alongside a
 *  D00032/D00033 submission. Binary response, so same manual authed-fetch
 *  pattern as generateSgiForm above. */
export async function downloadSgiSupportingDocuments(
    driverIds: string[],
    reason: string,
    docTypes?: string[],
): Promise<SgiSupportingDocumentsResult> {
    const store = useAuthStore.getState();
    const headers: Record<string, string> = { "Content-Type": "application/json" };
    if (store.token) headers["Authorization"] = `Bearer ${store.token}`;
    if (store.csrfToken) headers["X-CSRF-Token"] = store.csrfToken;
    const res = await fetch("/api/admin/data-transfer/sgi-forms/documents", {
        method: "POST",
        headers,
        body: JSON.stringify({ driver_ids: driverIds, reason, doc_types: docTypes ?? null }),
    });
    if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `Could not download documents (${res.status})`);
    }
    return {
        blob: await res.blob(),
        listed: Number(res.headers.get("X-Documents-Listed") ?? 0),
        included: Number(res.headers.get("X-Documents-Included") ?? 0),
    };
}

export interface SgiPackageResult {
    blob: Blob;
    checksIncluded: number;
    checksMissing: number;
}

/** The complete SGI submission: both filled forms plus each driver's criminal
 *  record check, in one ZIP. Binary response, so same manual authed-fetch
 *  pattern as generateSgiForm. */
export async function downloadSgiSubmissionPackage(
    driverIds: string[],
    reason: string,
    action: "add" | "remove" | "change" = "add",
): Promise<SgiPackageResult> {
    const store = useAuthStore.getState();
    const headers: Record<string, string> = { "Content-Type": "application/json" };
    if (store.token) headers["Authorization"] = `Bearer ${store.token}`;
    if (store.csrfToken) headers["X-CSRF-Token"] = store.csrfToken;
    const res = await fetch("/api/admin/data-transfer/sgi-forms/package", {
        method: "POST",
        headers,
        body: JSON.stringify({ driver_ids: driverIds, reason, action }),
    });
    if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `Could not build the submission package (${res.status})`);
    }
    return {
        blob: await res.blob(),
        checksIncluded: Number(res.headers.get("X-Checks-Included") ?? 0),
        checksMissing: Number(res.headers.get("X-Checks-Missing") ?? 0),
    };
}

// ─── Compliance & Tax Reporting ────────────────────────────────────────

export type ComplianceReportFormat = "pdf" | "csv" | "xlsx" | "docx";

const COMPLIANCE_FILE_EXTENSIONS: Record<ComplianceReportFormat, string> = {
    pdf: "pdf",
    csv: "csv",
    xlsx: "xlsx",
    docx: "docx",
};

/** Shared GET-and-download for the Compliance & Tax Reporting endpoints —
 *  both return a branded PDF/CSV/Excel/Word file, not JSON, so they use a
 *  raw authed fetch like generateSgiForm rather than request<T>. */
async function downloadComplianceReport(path: string, fallbackFilename: string): Promise<Blob> {
    const store = useAuthStore.getState();
    const headers: Record<string, string> = {};
    if (store.token) headers["Authorization"] = `Bearer ${store.token}`;
    const res = await fetch(path, { headers });
    // ACTION_ITEMS.md B10 dual-approval gate: a 202 is in fetch's `ok`
    // range, so without this check the JSON {"approval_required": true,
    // ...} body would silently be treated as file bytes and downloaded as
    // a corrupt "report". Check the content-type, not just status, since a
    // 202 is otherwise indistinguishable from a real (2xx) file response.
    if (res.status === 202 && (res.headers.get("content-type") || "").includes("application/json")) {
        throw new Error(
            "This report needs a different admin's approval before it can be generated — it's been added to the Export Approvals queue.",
        );
    }
    if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `Could not generate report (${res.status})`);
    }
    return res.blob();
}

/** date_from/date_to (YYYY-MM-DD) are optional -- the backend defaults an
 *  omitted side to the current calendar month (1st through today). Every
 *  date-ranged Compliance report shares this shape. */
function dateWindowParams(dateFrom?: string, dateTo?: string): Record<string, string> {
    const params: Record<string, string> = {};
    if (dateFrom) params.date_from = dateFrom;
    if (dateTo) params.date_to = dateTo;
    return params;
}

export async function downloadGstPstRemittance(
    format: ComplianceReportFormat,
    dateFrom?: string,
    dateTo?: string,
): Promise<{ blob: Blob; filename: string }> {
    const sp = new URLSearchParams({ ...dateWindowParams(dateFrom, dateTo), format });
    const blob = await downloadComplianceReport(
        `/api/admin/compliance/gst-pst-remittance?${sp.toString()}`,
        "gst_pst_remittance",
    );
    return { blob, filename: `gst_pst_remittance.${COMPLIANCE_FILE_EXTENSIONS[format]}` };
}

/** Driver roster (formerly Knight Archer Driver Onboarding) — name,
 *  license number, license class, and current status for every onboarded
 *  driver (or filtered to one status). Originally built for Knight
 *  Archer's monthly active-driver update; renamed once the report content
 *  turned out generically useful beyond that one consumer. */
export async function downloadDriverRoster(
    format: ComplianceReportFormat,
    status?: string,
): Promise<{ blob: Blob; filename: string }> {
    const sp = new URLSearchParams({ format });
    if (status) sp.set("status", status);
    const blob = await downloadComplianceReport(`/api/admin/compliance/driver-roster?${sp.toString()}`, "driver_roster");
    return { blob, filename: `driver_roster.${COMPLIANCE_FILE_EXTENSIONS[format]}` };
}

/** T4A filer handoff — per-driver annual earnings + Stripe-verified legal
 *  name/address for drivers at or above the $500 CRA threshold, for
 *  handoff to a third-party tax filer. NEVER includes the SIN itself —
 *  see routes/admin/compliance.py's module-section docstring for why. */
export async function downloadT4aFilerHandoff(
    year: number,
    format: ComplianceReportFormat,
): Promise<{ blob: Blob; filename: string }> {
    const sp = new URLSearchParams({ year: String(year), format });
    const blob = await downloadComplianceReport(`/api/admin/compliance/t4a-filer-handoff?${sp.toString()}`, "t4a_filer_handoff");
    return { blob, filename: `t4a_filer_handoff_${year}.${COMPLIANCE_FILE_EXTENSIONS[format]}` };
}

/** SGI usage-based insurance billing — per-trip, per-phase insured km
 *  (Period 2+3) at SGI's contracted rate ($0.11/km, fixed server-side —
 *  no rate to enter). */
export async function downloadInsuranceBillingSgi(
    format: ComplianceReportFormat,
    dateFrom?: string,
    dateTo?: string,
): Promise<{ blob: Blob; filename: string }> {
    const sp = new URLSearchParams({ ...dateWindowParams(dateFrom, dateTo), format });
    const blob = await downloadComplianceReport(
        `/api/admin/compliance/insurance-billing-sgi?${sp.toString()}`,
        "insurance_billing_sgi",
    );
    return { blob, filename: `insurance_billing_sgi.${COMPLIANCE_FILE_EXTENSIONS[format]}` };
}

/** Knight Archer usage-based insurance billing — per-trip, per-phase
 *  insured km (Period 2+3) at Knight Archer's contracted rate
 *  ($0.011/km, fixed server-side). */
export async function downloadInsuranceBillingKnightArcher(
    format: ComplianceReportFormat,
    dateFrom?: string,
    dateTo?: string,
): Promise<{ blob: Blob; filename: string }> {
    const sp = new URLSearchParams({ ...dateWindowParams(dateFrom, dateTo), format });
    const blob = await downloadComplianceReport(
        `/api/admin/compliance/insurance-billing-knight-archer?${sp.toString()}`,
        "insurance_billing_knight_archer",
    );
    return { blob, filename: `insurance_billing_knight_archer.${COMPLIANCE_FILE_EXTENSIONS[format]}` };
}

/** Completed rides with an airport pickup or dropoff, for airport
 *  ground-transportation program reporting. */
export async function downloadAirportTrips(
    format: ComplianceReportFormat,
    dateFrom?: string,
    dateTo?: string,
): Promise<{ blob: Blob; filename: string }> {
    const sp = new URLSearchParams({ ...dateWindowParams(dateFrom, dateTo), format });
    const blob = await downloadComplianceReport(`/api/admin/compliance/airport-trips?${sp.toString()}`, "airport_trips");
    return { blob, filename: `airport_trips.${COMPLIANCE_FILE_EXTENSIONS[format]}` };
}

export interface DataTransferJob {
    id: string;
    requested_by_admin_id?: string;
    entity_type: string;
    entity_ids: string[];
    entity_count: number;
    doc_type_filter?: string[] | null;
    format: string;
    reason?: string | null;
    status: "pending" | "completed" | "failed";
    error_message?: string | null;
    created_at: string;
    completed_at?: string | null;
    expires_at?: string | null;
}
export const listDataTransferJobs = (limit = 50) =>
    request<{ jobs: DataTransferJob[] }>(`/api/admin/data-transfer/jobs?limit=${limit}`);
export const getDataTransferJob = (jobId: string) =>
    request<DataTransferJob>(`/api/admin/data-transfer/jobs/${jobId}`);
export const regenerateDataTransferJobDownload = (jobId: string) =>
    request<{ download_url: string }>(`/api/admin/data-transfer/jobs/${jobId}/download`);
