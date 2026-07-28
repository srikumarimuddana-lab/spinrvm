"use client";

import { useState } from "react";
import { FileText, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select";
import { useToast } from "@/components/ui/use-toast";
import { generateSgiForm, searchDataTransferEntities, type SgiFormType } from "@/lib/api";
import { inferEntityType, type EntitySelectionState } from "@/components/data-transfer/useEntitySelection";

// Matches sgi_form_filler.py's MAX_DRIVER_ROWS/MAX_VEHICLE_ROWS — the real
// SGI D00032/D00033 forms only have this many repeating rows.
const FORM_ROW_LIMITS: Record<SgiFormType, number> = {
    driver_details: 10,
    vehicle_details: 16,
};

const FORM_LABELS: Record<SgiFormType, string> = {
    driver_details: "D00032 — Passenger for Hire Driver Details",
    vehicle_details: "D00033 — TNC Vehicle Details",
};

const FORM_FILENAMES: Record<SgiFormType, string> = {
    driver_details: "SGI_D00032_Driver_Details.pdf",
    vehicle_details: "SGI_D00033_Vehicle_Details.pdf",
};

// Largest of the two row limits — bounds the "select all matching filter"
// re-query so it fetches enough rows to check either form's limit without
// silently under-fetching for whichever one is checked.
const MAX_ROW_LIMIT = Math.max(...Object.values(FORM_ROW_LIMITS));

function triggerDownload(blob: Blob, filename: string) {
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
}

/** Resolve the current selection to driver-only entity IDs. Regression: an
 * earlier version only read `selection.selectedRefs`, ignoring
 * `selection.selectAllMatching` entirely — using "select all N matching
 * this filter" (rather than checking rows individually) made this tab show
 * 0 drivers selected and disabled Generate, even though a real selection
 * existed. Mirrors ExportTab.tsx's resolveSelection. */
async function resolveDriverIds(selection: EntitySelectionState): Promise<{ ids: string[]; truncated: boolean }> {
    if (selection.selectAllMatching) {
        const result = await searchDataTransferEntities({
            ...selection.selectAllMatching,
            entityType: "driver",
            page: 1,
            pageSize: MAX_ROW_LIMIT,
        });
        const ids = result.rows.filter((r) => inferEntityType(r) === "driver").map((r) => r.id);
        return { ids, truncated: result.total_count > MAX_ROW_LIMIT };
    }
    const ids = Array.from(selection.selectedRefs.values())
        .filter((r) => r.entity_type === "driver")
        .map((r) => r.entity_id);
    return { ids, truncated: false };
}

export function SgiFormsTab({ selection }: { selection: EntitySelectionState }) {
    const { toast } = useToast();
    // Both forms checked by default -- generating a driver's D00032 without
    // also generating their D00033 (or vice versa) is the exception, not
    // the common case, for a full onboarding/deregistration submission.
    const [formTypes, setFormTypes] = useState<Set<SgiFormType>>(
        new Set<SgiFormType>(["driver_details", "vehicle_details"]),
    );
    const [action, setAction] = useState<"add" | "remove" | "change">("add");
    const [loading, setLoading] = useState(false);

    const hasExplicitSelection = selection.selectedRefs.size > 0 || selection.selectAllMatching !== null;
    const selectedFormTypes = Array.from(formTypes);

    const toggleFormType = (formType: SgiFormType) => {
        setFormTypes((prev) => {
            const next = new Set(prev);
            if (next.has(formType)) next.delete(formType);
            else next.add(formType);
            return next;
        });
    };

    const onGenerate = async () => {
        if (selectedFormTypes.length === 0) {
            toast({ title: "No form selected", description: "Check at least one form (D00032 and/or D00033)." });
            return;
        }
        setLoading(true);
        try {
            const { ids: driverIds, truncated } = await resolveDriverIds(selection);
            if (driverIds.length === 0) {
                toast({
                    title: "No drivers selected",
                    description: "Select driver records in Search & Select first (rider selections don't apply).",
                });
                return;
            }
            if (truncated) {
                toast({
                    title: "Selection truncated",
                    description: `Only the first ${MAX_ROW_LIMIT} matching drivers were considered — narrow your filter for a complete submission.`,
                });
            }

            // Each checked form is generated as its own download; one form
            // exceeding its row limit or failing doesn't block the other.
            const results = await Promise.allSettled(
                selectedFormTypes.map(async (formType) => {
                    const rowLimit = FORM_ROW_LIMITS[formType];
                    if (driverIds.length > rowLimit) {
                        throw new Error(
                            `${driverIds.length} drivers selected; the ${FORM_LABELS[formType]} form has ${rowLimit} rows — select ${rowLimit} or fewer, or uncheck this form.`,
                        );
                    }
                    const blob = await generateSgiForm(formType, driverIds, action);
                    triggerDownload(blob, FORM_FILENAMES[formType]);
                    return formType;
                }),
            );

            const failures = results.filter(
                (r): r is PromiseRejectedResult => r.status === "rejected",
            );
            const successes = results.filter((r) => r.status === "fulfilled").length;

            if (successes > 0) {
                toast({ title: `${successes} form(s) generated`, description: "Download(s) starting…" });
            }
            for (const failure of failures) {
                toast({
                    title: "Form generation failed",
                    description: failure.reason?.message ?? "Unknown error",
                    variant: "destructive",
                });
            }
        } catch (e: any) {
            toast({ title: "Form generation failed", description: e?.message ?? "Unknown error", variant: "destructive" });
        } finally {
            setLoading(false);
        }
    };

    return (
        <div className="space-y-4">
            <div className="space-y-2">
                <span className="text-sm font-medium">Forms to generate</span>
                <div className="flex flex-wrap gap-4">
                    {(Object.keys(FORM_LABELS) as SgiFormType[]).map((formType) => (
                        <label key={formType} className="flex items-center gap-2 text-sm">
                            <input
                                type="checkbox"
                                checked={formTypes.has(formType)}
                                onChange={() => toggleFormType(formType)}
                            />
                            {FORM_LABELS[formType]}
                        </label>
                    ))}
                </div>
            </div>

            <div className="space-y-1">
                <span className="text-sm font-medium">Action</span>
                <Select value={action} onValueChange={(v) => setAction(v as typeof action)}>
                    <SelectTrigger className="w-[140px]">
                        <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                        <SelectItem value="add">Add</SelectItem>
                        <SelectItem value="remove">Remove</SelectItem>
                        <SelectItem value="change">Change</SelectItem>
                    </SelectContent>
                </Select>
            </div>

            <p className="text-sm text-muted-foreground">
                {selection.selectAllMatching
                    ? "All drivers matching your Search & Select filter will be used (rider selections don't apply)."
                    : hasExplicitSelection
                      ? `${Array.from(selection.selectedRefs.values()).filter((r) => r.entity_type === "driver").length} driver(s) selected (rider selections don't apply to SGI forms).`
                      : "No records selected — go to Search & Select first."}{" "}
                D00032 supports up to {FORM_ROW_LIMITS.driver_details} drivers per submission; D00033 up to{" "}
                {FORM_ROW_LIMITS.vehicle_details}.
            </p>

            <Button onClick={onGenerate} disabled={!hasExplicitSelection || selectedFormTypes.length === 0 || loading}>
                {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <FileText className="h-4 w-4" />}
                Generate &amp; download
            </Button>
        </div>
    );
}
