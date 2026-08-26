"use client";

import { useState } from "react";
import { Download, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select";
import { useToast } from "@/components/ui/use-toast";
import { InfoHint as Hint } from "@/components/info-hint";
import {
    exportDataTransferEntities,
    getDataTransferJob,
    regenerateDataTransferJobDownload,
    searchDataTransferEntities,
    type DataTransferExportEntityRef,
    type DataTransferExportFormat,
    type DataTransferExportQueuedResult,
} from "@/lib/api";
import { inferEntityType, type EntitySelectionState } from "@/components/data-transfer/useEntitySelection";

// Same cap as the backend's MAX_ENTITIES_PER_EXPORT (data_transfer_export.py)
// — kept in sync here so "select all matching" resolution can warn before
// hitting the 422 the server would otherwise return.
const MAX_ENTITIES_PER_EXPORT = 100;

// Mirrors ExportRequest.reason's Field(min_length=10, max_length=200) on the
// backend (PIA recommendation R-C, ACTION_ITEMS.md B11) — validated
// client-side too so the Export button's disabled state gives immediate
// feedback instead of a round-trip 422.
const REASON_MIN_LENGTH = 10;
const REASON_MAX_LENGTH = 200;

const DOC_TYPE_OPTIONS = [
    "drivers_license",
    "insurance",
    "vehicle_inspection",
    "background_check",
    "drivers_abstract",
    "work_authorization",
];

const DOC_TYPE_LABELS: Record<string, string> = {
    drivers_license: "Driver's Licence",
    insurance: "Insurance",
    vehicle_inspection: "Vehicle Inspection",
    background_check: "Background / Criminal Record Check",
    drivers_abstract: "Driver's Abstract",
    work_authorization: "Work Authorization",
};

/** Resolve the current selection into concrete entity refs. For explicit
 * selections this is a straight map; for "select all matching filter" it
 * re-queries the search endpoint (capped at MAX_ENTITIES_PER_EXPORT) since
 * the backend export route takes explicit refs, not a filter descriptor. */
async function resolveSelection(
    selection: EntitySelectionState,
): Promise<{ refs: DataTransferExportEntityRef[]; truncated: boolean }> {
    if (selection.selectAllMatching) {
        const result = await searchDataTransferEntities({
            ...selection.selectAllMatching,
            page: 1,
            pageSize: MAX_ENTITIES_PER_EXPORT,
        });
        return {
            refs: result.rows.map((row) => ({ entity_type: inferEntityType(row), entity_id: row.id })),
            truncated: result.total_count > MAX_ENTITIES_PER_EXPORT,
        };
    }
    return { refs: Array.from(selection.selectedRefs.values()), truncated: false };
}

const POLL_INTERVAL_MS = 2000;
const POLL_TIMEOUT_MS = 5 * 60 * 1000; // 5 minutes — a very large batch could legitimately take a while

/** The export route is backgrounded — it returns a job_id immediately, not
 * a download link. Poll the job until it leaves "pending", then mint a
 * fresh download link (jobs never store their signed URL — see
 * data_transfer_jobs.py's GET .../download). */
async function pollJobUntilDone(jobId: string): Promise<{ status: "completed" | "failed"; errorMessage?: string }> {
    const deadline = Date.now() + POLL_TIMEOUT_MS;
    while (Date.now() < deadline) {
        const job = await getDataTransferJob(jobId);
        if (job.status !== "pending") {
            return { status: job.status as "completed" | "failed", errorMessage: job.error_message ?? undefined };
        }
        await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL_MS));
    }
    throw new Error("Export is taking longer than expected — check the Jobs & History tab for its status.");
}

export function ExportTab({ selection }: { selection: EntitySelectionState }) {
    const { toast } = useToast();
    const [format, setFormat] = useState<DataTransferExportFormat>("zip");
    // Every document type starts selected, metadata AND file, restoring the
    // behavior PIA R-B specifies and its own implementation change-log
    // recorded: "Both admin-dashboard Export-tab checkboxes default to
    // checked (current full-fidelity behavior unchanged for anyone who
    // doesn't touch them) ... opt-in-to-exclude, default unchanged."
    //
    // That default was silently inverted to unchecked by a56b59b -- a commit
    // titled for an unrelated ADR-010 docs change, carrying no Change Impact
    // entry -- which is why exports had been arriving with no scans in them
    // and reading as a broken feature. This is a restoration of the approved
    // design, not a loosening of it: the export is still opt-in-to-exclude,
    // and the per-document grid below makes excluding far more precise than
    // the single global toggle R-B originally shipped.
    const [docTypes, setDocTypes] = useState<Set<string>>(new Set(DOC_TYPE_OPTIONS));
    const [docFileTypes, setDocFileTypes] = useState<Set<string>>(new Set(DOC_TYPE_OPTIONS));
    const [reason, setReason] = useState("");
    // Default OFF (PIPEDA data-minimization) -- exact GPS traces are among
    // the most sensitive things this export can carry; an admin who
    // genuinely needs them opts in deliberately per export.
    const [includeRideGps, setIncludeRideGps] = useState(false);
    const [loading, setLoading] = useState(false);

    const reasonValid = reason.trim().length >= REASON_MIN_LENGTH && reason.length <= REASON_MAX_LENGTH;

    // Selecting document types but no files at all is the state that gets
    // reported as a broken export: the ZIP builds, the documents are listed
    // in documents.csv, and not one scan/image/PDF is in it. Files are opt-in
    // (PIPEDA data minimization, PIA R-B) — but that is no longer silent.
    const metadataOnlyDocuments = format === "zip" && docTypes.size > 0 && docFileTypes.size === 0;

    /** Unchecking a document type must also drop its file request — a file
     * for a type that isn't in the export would be silently ignored by the
     * backend and leaves the UI claiming something it isn't sending. */
    const toggleDocType = (docType: string) => {
        setDocTypes((prev) => {
            const next = new Set(prev);
            if (next.has(docType)) next.delete(docType);
            else next.add(docType);
            return next;
        });
        setDocFileTypes((prev) => {
            if (!prev.has(docType)) return prev;
            const next = new Set(prev);
            next.delete(docType);
            return next;
        });
    };

    /** Requesting a file implies including the document — check both rather
     * than leaving an unreachable "file but not included" state. */
    const toggleDocFileType = (docType: string) => {
        setDocFileTypes((prev) => {
            const next = new Set(prev);
            if (next.has(docType)) next.delete(docType);
            else next.add(docType);
            return next;
        });
        setDocTypes((prev) => (prev.has(docType) ? prev : new Set(prev).add(docType)));
    };

    const allFilesSelected = DOC_TYPE_OPTIONS.every((t) => docFileTypes.has(t));
    const toggleAllFiles = () => {
        if (allFilesSelected) {
            setDocFileTypes(new Set());
        } else {
            setDocFileTypes(new Set(DOC_TYPE_OPTIONS));
            setDocTypes(new Set(DOC_TYPE_OPTIONS));
        }
    };

    const hasSelection = selection.selectAllMatching !== null || selection.selectedRefs.size > 0;

    const onExport = async () => {
        setLoading(true);
        try {
            const { refs, truncated } = await resolveSelection(selection);
            if (refs.length === 0) {
                toast({ title: "Nothing to export", description: "Select at least one record first." });
                return;
            }
            if (truncated) {
                toast({
                    title: "Selection truncated",
                    description: `Only the first ${MAX_ENTITIES_PER_EXPORT} matching records were exported — narrow your filter to export the rest in a separate batch.`,
                });
            }
            const docTypeFilter =
                format === "zip" && docTypes.size < DOC_TYPE_OPTIONS.length ? Array.from(docTypes) : undefined;
            const queued = await exportDataTransferEntities(refs, format, reason.trim(), {
                docTypes: docTypeFilter,
                includeRideGps,
                // Always send the explicit per-type list (even empty) so the
                // backend uses the per-type path rather than falling back to
                // its all-or-nothing include_document_bytes default of true.
                docFileTypes: format === "zip" ? Array.from(docFileTypes) : [],
            });

            // ACTION_ITEMS.md B10 dual-approval gate: with the flag on, a
            // large export doesn't start a job at all until a different
            // admin approves it from the Export Approvals queue.
            if ("approval_required" in queued) {
                toast({
                    title: "Approval required",
                    description:
                        "This export needs a different admin's approval before it can run — it's been added to the Export Approvals queue.",
                });
                return;
            }
            const job: DataTransferExportQueuedResult = queued;

            toast({
                title: "Export queued",
                description: `Preparing ${job.requested_count} record(s)… this may take a moment for large batches.`,
            });

            const outcome = await pollJobUntilDone(job.job_id);
            if (outcome.status === "failed") {
                toast({
                    title: "Export failed",
                    description: outcome.errorMessage || "Unknown error",
                    variant: "destructive",
                });
                return;
            }

            const { download_url } = await regenerateDataTransferJobDownload(queued.job_id);
            toast({
                title: "Export ready",
                // Repeated at download time, not just next to the checkbox —
                // the ZIP lands minutes later and the admin has usually
                // scrolled away by then; without this, "no files in the ZIP"
                // reads as a failed export.
                description: metadataOnlyDocuments
                    ? "Download starting… Note: document metadata only — no files (see README.txt in the ZIP)."
                    : "Download starting…",
            });
            if (typeof window !== "undefined") {
                window.open(download_url, "_blank");
            }
        } catch (e: any) {
            toast({ title: "Export failed", description: e?.message ?? "Unknown error", variant: "destructive" });
        } finally {
            setLoading(false);
        }
    };

    return (
        <div className="space-y-4">
            <div className="flex items-center gap-3">
                <span className="text-sm font-medium">Format</span>
                <Select value={format} onValueChange={(v) => setFormat(v as DataTransferExportFormat)}>
                    <SelectTrigger className="w-[180px]">
                        <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                        <SelectItem value="zip">ZIP (profile + documents + history)</SelectItem>
                        <SelectItem value="csv">CSV (profile summary)</SelectItem>
                        <SelectItem value="excel">Excel (profile summary)</SelectItem>
                        <SelectItem value="json">JSON (profile summary)</SelectItem>
                    </SelectContent>
                </Select>
            </div>

            {format === "zip" && (
                <div className="space-y-2">
                    <div className="flex items-center gap-1.5">
                        <span className="text-sm font-medium">Documents to export</span>
                        <Hint text="Everything is selected by default. Untick any document type you don't need, or untick just its File box to export the record without the uploaded scan — narrowing the export to what this transfer genuinely requires is the data-minimizing choice." />
                    </div>

                    <div className="overflow-x-auto rounded-md border">
                        <table className="w-full text-sm">
                            <thead className="bg-muted/50 text-xs text-muted-foreground">
                                <tr>
                                    <th className="px-3 py-2 text-left font-medium">Document</th>
                                    <th className="w-32 px-3 py-2 text-center font-medium">
                                        Metadata
                                        <div className="font-normal">record only</div>
                                    </th>
                                    <th className="w-40 px-3 py-2 text-center font-medium">
                                        File
                                        <div className="font-normal">scan / image / PDF</div>
                                        <button
                                            type="button"
                                            className="mt-0.5 font-normal underline underline-offset-2 hover:no-underline"
                                            onClick={toggleAllFiles}
                                        >
                                            {allFilesSelected ? "clear all" : "select all"}
                                        </button>
                                    </th>
                                </tr>
                            </thead>
                            <tbody>
                                {DOC_TYPE_OPTIONS.map((docType) => (
                                    <tr key={docType} className="border-t">
                                        <td className="px-3 py-2">
                                            {DOC_TYPE_LABELS[docType] ?? docType.replace(/_/g, " ")}
                                        </td>
                                        <td className="px-3 py-2 text-center">
                                            <input
                                                type="checkbox"
                                                aria-label={`Include ${DOC_TYPE_LABELS[docType] ?? docType} metadata`}
                                                checked={docTypes.has(docType)}
                                                onChange={() => toggleDocType(docType)}
                                            />
                                        </td>
                                        <td className="px-3 py-2 text-center">
                                            <input
                                                type="checkbox"
                                                aria-label={`Include ${DOC_TYPE_LABELS[docType] ?? docType} file`}
                                                checked={docFileTypes.has(docType)}
                                                onChange={() => toggleDocFileType(docType)}
                                            />
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>

                    {metadataOnlyDocuments && (
                        <div
                            role="status"
                            className="rounded-md border border-warning bg-warning/10 px-3 py-2 text-xs text-warning"
                        >
                            <span className="font-medium">
                                This ZIP will contain document metadata only — no scans, images, or PDFs.
                            </span>{" "}
                            Tick a box in the <span className="font-medium">File</span> column for each document
                            whose actual file you need.
                        </div>
                    )}
                    {docFileTypes.size > 0 && (
                        <div className="text-xs text-muted-foreground">
                            Exporting the file for {docFileTypes.size} document type
                            {docFileTypes.size === 1 ? "" : "s"}; the rest are metadata rows only. Files land in each
                            driver&rsquo;s <code className="rounded bg-muted px-1">documents/</code> folder.
                        </div>
                    )}
                </div>
            )}

            <div className="space-y-2">
                <label htmlFor="export-reason" className="text-sm font-medium">
                    Reason for this export <span className="text-muted-foreground">(required)</span>
                </label>
                <textarea
                    id="export-reason"
                    className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                    rows={2}
                    maxLength={REASON_MAX_LENGTH}
                    placeholder="e.g. seeding staging with 20 realistic driver profiles"
                    value={reason}
                    onChange={(e) => setReason(e.target.value)}
                />
                <div className="text-xs text-muted-foreground">
                    {reason.length}/{REASON_MAX_LENGTH} — a short business justification, kept with the export
                    job for accountability. Minimum {REASON_MIN_LENGTH} characters.
                </div>
            </div>

            <div className="space-y-2">
                <div className="flex items-center gap-1.5">
                    <span className="text-sm font-medium">Ride data to include</span>
                    <Hint text="Off by default (PIPEDA data minimization) — turn it on only when this specific export genuinely needs exact coordinates. Document files are selected per document type above." />
                </div>
                <div className="flex flex-col gap-2">
                    <label className="flex items-center gap-2 text-sm">
                        <input
                            type="checkbox"
                            checked={includeRideGps}
                            onChange={(e) => setIncludeRideGps(e.target.checked)}
                        />
                        Exact pickup/dropoff GPS coordinates
                    </label>
                </div>
                <div className="text-xs text-muted-foreground">
                    Leaving this off reduces sensitivity for lower-risk exports (e.g. seeding a UI-only staging
                    environment) — ride counts stay the same, only the coordinates are dropped.
                </div>
            </div>

            <div className="text-sm text-muted-foreground">
                {selection.selectAllMatching
                    ? "All records matching your Search & Select filter will be exported."
                    : hasSelection
                      ? `${selection.selectedRefs.size} record(s) selected.`
                      : "No records selected — go to Search & Select first."}
            </div>

            <div className="flex flex-col items-start gap-1.5">
                <Button onClick={onExport} disabled={!hasSelection || !reasonValid || loading}>
                    {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
                    Export
                </Button>
                {/* Explains exactly why the button is disabled instead of
                    leaving it a silent no-op — the two prerequisites (a
                    selection, a 10+ character reason) were previously only
                    documented in a hover tooltip / helper text elsewhere on
                    the page, which admins kept missing. */}
                {(!hasSelection || !reasonValid) && !loading && (
                    <p className="text-xs text-muted-foreground">
                        {!hasSelection
                            ? "Select records in Search & Select to enable Export."
                            : "Add a reason (10+ characters) above to enable Export."}
                    </p>
                )}
            </div>
        </div>
    );
}
