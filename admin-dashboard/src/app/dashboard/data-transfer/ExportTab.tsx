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
    searchDataTransferEntities,
    type DataTransferExportEntityRef,
    type DataTransferExportFormat,
} from "@/lib/api";
import { inferEntityType, type EntitySelectionState } from "@/components/data-transfer/useEntitySelection";

// Same cap as the backend's MAX_ENTITIES_PER_EXPORT (data_transfer_export.py)
// — kept in sync here so "select all matching" resolution can warn before
// hitting the 422 the server would otherwise return.
const MAX_ENTITIES_PER_EXPORT = 100;

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

export function ExportTab({ selection }: { selection: EntitySelectionState }) {
    const { toast } = useToast();
    const [format, setFormat] = useState<DataTransferExportFormat>("zip");
    const [docTypes, setDocTypes] = useState<Set<string>>(new Set(DOC_TYPE_OPTIONS));
    const [loading, setLoading] = useState(false);

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
            const result = await exportDataTransferEntities(refs, format, docTypeFilter);
            toast({
                title: "Export ready",
                description: `${result.entity_count} record(s) exported.`,
            });
            if (typeof window !== "undefined") {
                window.open(result.download_url, "_blank");
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

            <div className="text-sm text-muted-foreground">
                {selection.selectAllMatching
                    ? "All records matching your Search & Select filter will be exported."
                    : hasSelection
                      ? `${selection.selectedRefs.size} record(s) selected.`
                      : "No records selected — go to Search & Select first."}
            </div>

            <Button onClick={onExport} disabled={!hasSelection || loading}>
                {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
                Export
            </Button>
        </div>
    );
}
