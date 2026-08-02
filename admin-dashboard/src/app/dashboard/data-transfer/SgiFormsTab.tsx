"use client";

import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, FileText, Loader2, RefreshCw } from "lucide-react";
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
import { generateSgiForm, getSgiRemovalQueue, searchDataTransferEntities, type SgiFormType, type SgiRemovalQueueEntry } from "@/lib/api";
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

/** Splits `items` into consecutive chunks of at most `size`. */
function chunk<T>(items: T[], size: number): T[][] {
    const chunks: T[][] = [];
    for (let i = 0; i < items.length; i += size) chunks.push(items.slice(i, i + size));
    return chunks;
}

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

    // Drivers who deleted their account while filed with SGI. They stay listed
    // as active passenger-for-hire drivers until the removal forms are filed,
    // and nothing else in the product surfaces that backlog.
    const [removalQueue, setRemovalQueue] = useState<SgiRemovalQueueEntry[]>([]);
    const [queueUnresolvable, setQueueUnresolvable] = useState(0);
    const [queueLoading, setQueueLoading] = useState(false);

    const loadQueue = useCallback(async () => {
        setQueueLoading(true);
        try {
            const res = await getSgiRemovalQueue();
            setRemovalQueue(res.drivers);
            setQueueUnresolvable(res.unresolvable);
        } catch {
            // Non-fatal: the queue is a prompt, not a prerequisite for
            // generating a form manually. Leave it empty rather than blocking
            // the tab on it.
            setRemovalQueue([]);
        } finally {
            setQueueLoading(false);
        }
    }, []);

    useEffect(() => {
        loadQueue();
    }, [loadQueue]);

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

    /** Generate the checked forms for an explicit driver-id list. Shared by the
     *  Search & Select flow and the removal queue, so a queued removal is filed
     *  through exactly the same path (and therefore gets the same
     *  already-filed stamp) as a manually selected one. */
    const runGenerate = async (
        driverIds: string[],
        formAction: "add" | "remove" | "change",
        // Passed explicitly rather than read from state: the queue button sets
        // both form types and fires in the same tick, and a React state update
        // is not visible to this closure until the next render.
        forms: SgiFormType[],
    ) => {
        try {
            // Each checked form is generated as its own download. A
            // selection larger than the form's row limit is split into
            // consecutive documents (D00032_Driver_Details_1.pdf,
            // _2.pdf, …) rather than refused outright — the real SGI form
            // only has a fixed number of repeating rows per page set, but
            // there's nothing stopping an admin from submitting several
            // documents for one batch.
            const results = await Promise.allSettled(
                forms.map(async (formType) => {
                    const rowLimit = FORM_ROW_LIMITS[formType];
                    const batches = chunk(driverIds, rowLimit);
                    const baseFilename = FORM_FILENAMES[formType].replace(/\.pdf$/, "");
                    for (let i = 0; i < batches.length; i++) {
                        const blob = await generateSgiForm(formType, batches[i], formAction);
                        const filename = batches.length > 1 ? `${baseFilename}_${i + 1}.pdf` : `${baseFilename}.pdf`;
                        triggerDownload(blob, filename);
                    }
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
            // A removal changes what is still outstanding — refresh so the queue
            // reflects what was just filed rather than re-prompting for it.
            if (formAction === "remove" && successes > 0) await loadQueue();
        } catch (e: any) {
            toast({ title: "Form generation failed", description: e?.message ?? "Unknown error", variant: "destructive" });
        } finally {
            setLoading(false);
        }
    };

    const onGenerate = async () => {
        if (selectedFormTypes.length === 0) {
            toast({ title: "No form selected", description: "Check at least one form (D00032 and/or D00033)." });
            return;
        }
        setLoading(true);
        let driverIds: string[];
        try {
            const resolved = await resolveDriverIds(selection);
            driverIds = resolved.ids;
            if (resolved.truncated) {
                toast({
                    title: "Selection truncated",
                    description: `Only the first ${MAX_ROW_LIMIT} matching drivers were considered — narrow your filter for a complete submission.`,
                });
            }
        } catch (e: any) {
            toast({ title: "Form generation failed", description: e?.message ?? "Unknown error", variant: "destructive" });
            setLoading(false);
            return;
        }
        if (driverIds.length === 0) {
            toast({
                title: "No drivers selected",
                description: "Select driver records in Search & Select first (rider selections don't apply).",
            });
            setLoading(false);
            return;
        }
        await runGenerate(driverIds, action, selectedFormTypes);
    };

    /** File the outstanding removals straight from the queue — no Search &
     *  Select round-trip. Both forms are always generated: a driver needs D00032
     *  AND D00033, and filing one leaves their vehicle on SGI's books. Entries
     *  with no linked users row cannot be sent and are surfaced separately
     *  rather than silently skipped. */
    const onGenerateQueuedRemovals = async () => {
        const ids = removalQueue.map((d) => d.entity_id).filter((id): id is string => !!id);
        if (ids.length === 0) return;
        setFormTypes(new Set<SgiFormType>(["driver_details", "vehicle_details"]));
        setAction("remove");
        setLoading(true);
        await runGenerate(ids, "remove", ["driver_details", "vehicle_details"]);
    };

    const queuedFilable = removalQueue.filter((d) => d.entity_id).length;

    return (
        <div className="space-y-4">
            {removalQueue.length > 0 && (
                <div className="rounded-lg border border-amber-300 bg-amber-50 dark:border-amber-800 dark:bg-amber-900/20 p-3 space-y-2">
                    <div className="flex items-start gap-2">
                        <AlertTriangle className="h-4 w-4 text-amber-600 dark:text-amber-400 mt-0.5 shrink-0" />
                        <div className="min-w-0 flex-1">
                            <p className="text-sm font-medium text-amber-900 dark:text-amber-200">
                                {removalQueue.length} driver{removalQueue.length === 1 ? "" : "s"} left but {removalQueue.length === 1 ? "is" : "are"} still filed with the regulator
                            </p>
                            <p className="text-xs text-amber-800/80 dark:text-amber-300/80 mt-0.5">
                                They deleted their Spinr account, so Spinr no longer dispatches to them — but SGI still
                                lists them as active passenger-for-hire drivers until the removal is filed. Each needs
                                both D00032 (driver) and D00033 (vehicle).
                            </p>
                        </div>
                        <Button variant="ghost" size="sm" className="h-7 shrink-0" onClick={loadQueue} disabled={queueLoading}>
                            <RefreshCw className={`h-3.5 w-3.5 ${queueLoading ? "animate-spin" : ""}`} />
                        </Button>
                    </div>
                    <div className="max-h-40 overflow-y-auto rounded border border-amber-200 dark:border-amber-800/60 bg-background/60">
                        <table className="w-full text-xs">
                            <thead className="text-muted-foreground">
                                <tr className="border-b border-amber-200 dark:border-amber-800/60">
                                    <th className="text-left font-medium px-2 py-1.5">Driver</th>
                                    <th className="text-left font-medium px-2 py-1.5">Stopped</th>
                                    <th className="text-left font-medium px-2 py-1.5">Outstanding</th>
                                </tr>
                            </thead>
                            <tbody>
                                {removalQueue.map((d) => (
                                    <tr key={d.driver_id} className="border-b border-amber-100 last:border-0 dark:border-amber-900/40">
                                        <td className="px-2 py-1.5">
                                            {d.name || d.driver_id}
                                            {!d.entity_id && (
                                                <span className="ml-1 text-amber-700 dark:text-amber-400">(no linked account)</span>
                                            )}
                                        </td>
                                        <td className="px-2 py-1.5 tabular-nums">{d.effective_date ?? "—"}</td>
                                        <td className="px-2 py-1.5">
                                            {[d.driver_form_outstanding && "D00032", d.vehicle_form_outstanding && "D00033"]
                                                .filter(Boolean)
                                                .join(" + ") || "—"}
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                    {queueUnresolvable > 0 && (
                        <p className="text-xs text-amber-800 dark:text-amber-300">
                            {queueUnresolvable} of these {queueUnresolvable === 1 ? "has" : "have"} no linked user account and
                            cannot be generated from here — they need to be filed with SGI manually.
                        </p>
                    )}
                    <Button size="sm" onClick={onGenerateQueuedRemovals} disabled={loading || queuedFilable === 0}>
                        {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <FileText className="h-4 w-4" />}
                        Generate removal forms for {queuedFilable}
                    </Button>
                </div>
            )}

            <div className="space-y-2">
                <div className="flex items-center gap-1.5">
                    <span className="text-sm font-medium">Forms to generate</span>
                    <Hint
                        text="Company name, SGI customer number, and address are filled in automatically on every page. Verified driver history and criminal record check attached default to Yes for the selected drivers — uncheck the form and regenerate individually if a specific driver doesn't actually qualify."
                        href="https://www.sgi.sk.ca"
                        linkLabel="SGI — Saskatchewan Government Insurance"
                    />
                </div>
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
                <div className="flex items-center gap-1.5">
                    <span className="text-sm font-medium">Action</span>
                    <Hint text="Add registers a new driver/vehicle with SGI, Remove deregisters one, Change updates an existing registration's details." />
                </div>
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
