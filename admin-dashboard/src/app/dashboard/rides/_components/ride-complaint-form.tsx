"use client";

import { useState } from "react";
import { createRideComplaint } from "@/lib/api";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { FileWarning } from "lucide-react";

const CATEGORIES = [
    { value: "safety", label: "Safety" },
    { value: "behavior", label: "Behavior" },
    { value: "fraud", label: "Fraud" },
    { value: "damage", label: "Damage" },
    { value: "other", label: "Other" },
];

interface Props {
    open: boolean;
    onClose: () => void;
    rideId: string;
    onCreated: () => void;
}

export default function RideComplaintForm({ open, onClose, rideId, onCreated }: Props) {
    const [againstType, setAgainstType] = useState<"rider" | "driver">("driver");
    const [category, setCategory] = useState("");
    const [description, setDescription] = useState("");
    const [loading, setLoading] = useState(false);

    const handleSubmit = async () => {
        if (!category || !description) return;
        setLoading(true);
        try {
            await createRideComplaint(rideId, { against_type: againstType, category, description });
            alert("Complaint created");
            onCreated();
            onClose();
            setCategory("");
            setDescription("");
        } catch (e: any) {
            alert(e.message || "Failed to create complaint");
        } finally {
            setLoading(false);
        }
    };

    return (
        <Dialog open={open} onOpenChange={v => !v && onClose()}>
            <DialogContent className="sm:max-w-md">
                <DialogHeader>
                    <DialogTitle className="flex items-center gap-2">
                        <FileWarning className="h-5 w-5 text-amber-500" />
                        Raise Complaint
                    </DialogTitle>
                </DialogHeader>
                <div className="space-y-3">
                    <div>
                        <label className="text-xs font-medium text-muted-foreground">Against</label>
                        <div className="flex gap-2 mt-1">
                            {(["rider", "driver"] as const).map(t => (
                                <button key={t} onClick={() => setAgainstType(t)}
                                    className={`px-3 py-1.5 rounded-lg text-sm font-semibold ${againstType === t ? "bg-primary text-white" : "bg-muted text-muted-foreground"}`}>
                                    {t === "rider" ? "Rider" : "Driver"}
                                </button>
                            ))}
                        </div>
                    </div>
                    <div>
                        <label className="text-xs font-medium text-muted-foreground">Category</label>
                        <select value={category} onChange={e => setCategory(e.target.value)}
                            className="w-full mt-1 border rounded-lg px-3 py-2 text-sm bg-card text-foreground">
                            <option value="">Select category...</option>
                            {CATEGORIES.map(c => <option key={c.value} value={c.value}>{c.label}</option>)}
                        </select>
                    </div>
                    <div>
                        <label className="text-xs font-medium text-muted-foreground">Description</label>
                        <textarea value={description} onChange={e => setDescription(e.target.value)}
                            placeholder="Describe the issue..."
                            className="w-full mt-1 border rounded-lg px-3 py-2 text-sm bg-card text-foreground min-h-[80px] resize-none" />
                    </div>
                </div>
                <DialogFooter>
                    <button onClick={onClose} className="px-4 py-2 text-sm font-medium rounded-lg hover:bg-muted">Cancel</button>
                    <button onClick={handleSubmit} disabled={!category || !description || loading}
                        className="px-4 py-2 text-sm font-semibold text-white bg-amber-600 rounded-lg hover:bg-amber-700 disabled:opacity-50">
                        {loading ? "Submitting..." : "Submit Complaint"}
                    </button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}
