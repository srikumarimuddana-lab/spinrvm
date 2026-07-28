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
import {
    exportDataTransferEntities,
    getDataTransferJob,
    regenerateDataTransferJobDownload,
    searchDataTransferEntities,
    type DataTransferExportEntityRef,
    type DataTransferExportFormat,
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
    // Background Check is the default-checked document type; everything else
    // starts unchecked. An admin exporting a full profile for another
    // environment usually wants a specific, deliberate document set rather
    // than everything by default -- Background Check is the one most
    // consistently needed for a regulator/insurer-facing transfer.
    const [docTypes, setDocTypes] = useState<Set<string>>(new Set(["background_check"]));
    const [reason, setReason] = useState("");
    const [includeRideGps, setIncludeRideGps] = useState(true);
    const [includeDocumentBytes, setIncludeDocumentBytes] = useState(true);
    const [loading, setLoading] = useState(false);

    const reasonValid = reason.trim().length >= REASON_MIN_LENGTH && reason.length <= REASON_MAX_LENGTH;

    const toggleDocType = (docType: string) => {
        setDocTypes((prev) => {
            const next = new Set(prev);
            if (next.has(docType)) next.delete(docType);
            else next.add(docType);
            return next;
        });
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
                includeDocumentBytes,
            });
            toast({
                title: "Export queued",
                description: `Preparing ${queued.requested_count} record(s)… this may take a moment for large batches.`,
            });

            const outcome = await pollJobUntilDone(queued.job_id);
            if (outcome.status === "failed") {
                toast({
                    title: "Export failed",
                    description: outcome.errorMessage || "Unknown error",
                    variant: "destructive",
                });
                return;
            }

            const { download_url } = await regenerateDataTransferJobDownload(queued.job_id);
            toast({ title: "Export ready", description: "Download starting…" });
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
                    <span className="text-sm font-medium">Documents to include</span>
                    <div className="flex flex-wrap gap-4">
                        {DOC_TYPE_OPTIONS.map((docType) => (
                            <label key={docType} className="flex items-center gap-2 text-sm">
                                <input
                                    type="checkbox"
                                    checked={docTypes.has(docType)}
                                    onChange={() => toggleDocType(docType)}
                                />
                                {docType.replace(/_/g, " ")}
                            </label>
                        ))}
                    </div>
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
                <span className="text-sm font-medium">Data to include</span>
                <div className="flex flex-col gap-2">
                    <label className="flex items-center gap-2 text-sm">
                        <input
                            type="checkbox"
                            checked={includeRideGps}
                            onChange={(e) => setIncludeRideGps(e.target.checked)}
                        />
                        Exact pickup/dropoff GPS coordinates
                    </label>
                    <label className="flex items-center gap-2 text-sm">
                        <input
                            type="checkbox"
                            checked={includeDocumentBytes}
                            onChange={(e) => setIncludeDocumentBytes(e.target.checked)}
                        />
                        Document file contents (not just metadata)
                    </label>
                </div>
                <div className="text-xs text-muted-foreground">
                    Unchecking either reduces sensitivity for lower-risk exports (e.g. seeding a UI-only
                    staging environment) — record counts stay the same, only these fields are dropped.
                </div>
            </div>

            <div className="text-sm text-muted-foreground">
                {selection.selectAllMatching
                    ? "All records matching your Search & Select filter will be exported."
                    : hasSelection
                      ? `${selection.selectedRefs.size} record(s) selected.`
                      : "No records selected — go to Search & Select first."}
            </div>

            <Button onClick={onExport} disabled={!hasSelection || !reasonValid || loading}>
                {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
                Export
            </Button>
        </div>
    );
}
