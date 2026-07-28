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
import { generateSgiForm, type SgiFormType } from "@/lib/api";
import type { EntitySelectionState } from "@/components/data-transfer/useEntitySelection";

// Matches sgi_form_filler.py's MAX_DRIVER_ROWS/MAX_VEHICLE_ROWS — the real
// SGI D00032/D00033 forms only have this many repeating rows.
const FORM_ROW_LIMITS: Record<SgiFormType, number> = {
    driver_details: 10,
    vehicle_details: 16,
};

export function SgiFormsTab({ selection }: { selection: EntitySelectionState }) {
    const { toast } = useToast();
    const [formType, setFormType] = useState<SgiFormType>("driver_details");
    const [action, setAction] = useState<"add" | "remove" | "change">("add");
    const [loading, setLoading] = useState(false);

    // SGI forms are driver-only (D00032 reports drivers; D00033 reports the
    // vehicles attached to a driver's profile) — rider selections don't apply.
    const driverRefs = Array.from(selection.selectedRefs.values()).filter((r) => r.entity_type === "driver");
    const rowLimit = FORM_ROW_LIMITS[formType];
    const overLimit = driverRefs.length > rowLimit;

    const onGenerate = async () => {
        if (driverRefs.length === 0) {
            toast({ title: "No drivers selected", description: "Select driver records in Search & Select first." });
            return;
        }
        if (overLimit) {
            toast({
                title: "Too many drivers selected",
                description: `The ${formType === "driver_details" ? "D00032" : "D00033"} form has ${rowLimit} rows — select ${rowLimit} or fewer.`,
                variant: "destructive",
            });
            return;
        }
        setLoading(true);
        try {
            const blob = await generateSgiForm(
                formType,
                driverRefs.map((r) => r.entity_id),
                action,
            );
            const url = URL.createObjectURL(blob);
            const link = document.createElement("a");
            link.href = url;
            link.download = formType === "driver_details" ? "SGI_D00032_Driver_Details.pdf" : "SGI_D00033_Vehicle_Details.pdf";
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
            URL.revokeObjectURL(url);
        } catch (e: any) {
            toast({ title: "Form generation failed", description: e?.message ?? "Unknown error", variant: "destructive" });
        } finally {
            setLoading(false);
        }
    };

    return (
        <div className="space-y-4">
            <div className="flex flex-wrap items-end gap-3">
                <div className="space-y-1">
                    <span className="text-sm font-medium">Form</span>
                    <Select value={formType} onValueChange={(v) => setFormType(v as SgiFormType)}>
                        <SelectTrigger className="w-[260px]">
                            <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                            <SelectItem value="driver_details">D00032 — Passenger for Hire Driver Details</SelectItem>
                            <SelectItem value="vehicle_details">D00033 — TNC Vehicle Details</SelectItem>
                        </SelectContent>
                    </Select>
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
            </div>

            <p className={`text-sm ${overLimit ? "text-destructive" : "text-muted-foreground"}`}>
                {driverRefs.length} driver(s) selected (rider selections don't apply to SGI forms). This form supports
                up to {rowLimit} per submission.
            </p>

            <Button onClick={onGenerate} disabled={driverRefs.length === 0 || overLimit || loading}>
                {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <FileText className="h-4 w-4" />}
                Generate &amp; download
            </Button>
        </div>
    );
}
