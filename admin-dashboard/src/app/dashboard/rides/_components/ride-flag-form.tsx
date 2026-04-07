"use client";

import { useState } from "react";
import { flagRideParticipant } from "@/lib/api";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { AlertTriangle } from "lucide-react";

const FLAG_REASONS = [
    { value: "vomited_in_car", label: "Vomited in car" },
    { value: "misbehaved", label: "Misbehaved" },
    { value: "no_show", label: "No show" },
    { value: "damage", label: "Damage to vehicle" },
    { value: "fraud", label: "Fraud" },
    { value: "other", label: "Other" },
];

interface Props {
    open: boolean;
    onClose: () => void;
    rideId: string;
    targetType: "rider" | "driver";
    targetName: string;
    onFlagged: () => void;
}

export default function RideFlagForm({ open, onClose, rideId, targetType, targetName, onFlagged }: Props) {
    const [reason, setReason] = useState("");
    const [description, setDescription] = useState("");
    const [loading, setLoading] = useState(false);

    const handleSubmit = async () => {
        if (!reason) return;
        setLoading(true);
        try {
            const result = await flagRideParticipant(rideId, { target_type: targetType, reason, description: description || undefined });
            if (result.auto_banned) {
                alert(`${targetType === "rider" ? "Rider" : "Driver"} has been AUTO-BANNED (${result.active_flag_count} flags).`);
            } else {
                alert(`Flag added. Active flags: ${result.active_flag_count}/3`);
            }
            onFlagged();
            onClose();
            setReason("");
            setDescription("");
        } catch (e: any) {
            alert(e.message || "Failed to flag");
        } finally {
            setLoading(false);
        }
    };

    return (
        <Dialog open={open} onOpenChange={v => !v && onClose()}>
            <DialogContent className="sm:max-w-md">
                <DialogHeader>
                    <DialogTitle className="flex items-center gap-2">
                        <AlertTriangle className="h-5 w-5 text-red-500" />
                        Flag {targetType === "rider" ? "Rider" : "Driver"}
                    </DialogTitle>
                </DialogHeader>
                <div className="space-y-3">
                    <p className="text-sm text-muted-foreground">Flagging <b>{targetName}</b>. 3 active flags will result in automatic ban.</p>
                    <div>
                        <label className="text-xs font-medium text-muted-foreground">Reason</label>
                        <select value={reason} onChange={e => setReason(e.target.value)}
                            className="w-full mt-1 border rounded-lg px-3 py-2 text-sm bg-card text-foreground">
                            <option value="">Select reason...</option>
                            {FLAG_REASONS.map(r => <option key={r.value} value={r.value}>{r.label}</option>)}
                        </select>
                    </div>
                    <div>
                        <label className="text-xs font-medium text-muted-foreground">Description (optional)</label>
                        <Input value={description} onChange={e => setDescription(e.target.value)} placeholder="Additional details..." className="mt-1" />
                    </div>
                </div>
                <DialogFooter>
                    <button onClick={onClose} className="px-4 py-2 text-sm font-medium rounded-lg hover:bg-muted">Cancel</button>
                    <button onClick={handleSubmit} disabled={!reason || loading}
                        className="px-4 py-2 text-sm font-semibold text-white bg-red-600 rounded-lg hover:bg-red-700 disabled:opacity-50">
                        {loading ? "Flagging..." : "Flag"}
                    </button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}
