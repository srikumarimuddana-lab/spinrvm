"use client";

import { useState } from "react";
import { reportLostItem, resolveLostItem } from "@/lib/api";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { PackageSearch, CheckCircle, Clock, XCircle, Bell } from "lucide-react";
import { Sec } from "./ride-ui-helpers";

const STATUS_ICONS: Record<string, { icon: any; color: string }> = {
    reported: { icon: Clock, color: "text-amber-500" },
    driver_notified: { icon: Bell, color: "text-blue-500" },
    resolved: { icon: CheckCircle, color: "text-emerald-500" },
    unresolved: { icon: XCircle, color: "text-red-500" },
};

interface Props {
    rideId: string;
    items: any[];
    onRefresh: () => void;
}

export default function RideLostFound({ rideId, items, onRefresh }: Props) {
    const [showForm, setShowForm] = useState(false);
    const [description, setDescription] = useState("");
    const [loading, setLoading] = useState(false);

    const handleReport = async () => {
        if (!description) return;
        setLoading(true);
        try {
            await reportLostItem(rideId, { item_description: description });
            alert("Lost item reported. Driver has been notified.");
            setShowForm(false);
            setDescription("");
            onRefresh();
        } catch (e: any) {
            alert(e.message || "Failed to report item");
        } finally {
            setLoading(false);
        }
    };

    const handleResolve = async (itemId: string, status: "resolved" | "unresolved") => {
        try {
            await resolveLostItem(itemId, { status });
            onRefresh();
        } catch (e: any) {
            alert(e.message || "Failed to update");
        }
    };

    return (
        <>
            <Sec title="Lost & Found">
                {items.length === 0 ? (
                    <p className="text-sm text-muted-foreground">No lost items reported</p>
                ) : (
                    <div className="space-y-2">
                        {items.map((item: any) => {
                            const s = STATUS_ICONS[item.status] || STATUS_ICONS.reported;
                            const I = s.icon;
                            return (
                                <div key={item.id} className="flex items-start gap-2 bg-background rounded-lg p-2">
                                    <I className={`h-4 w-4 mt-0.5 shrink-0 ${s.color}`} />
                                    <div className="flex-1 min-w-0">
                                        <p className="text-sm font-medium">{item.item_description}</p>
                                        <p className="text-[10px] text-muted-foreground">{item.status?.replace(/_/g, " ").toUpperCase()}</p>
                                    </div>
                                    {(item.status === "reported" || item.status === "driver_notified") && (
                                        <div className="flex gap-1">
                                            <button onClick={() => handleResolve(item.id, "resolved")} className="text-[10px] px-2 py-0.5 rounded bg-emerald-100 text-emerald-700 hover:bg-emerald-200">Resolved</button>
                                            <button onClick={() => handleResolve(item.id, "unresolved")} className="text-[10px] px-2 py-0.5 rounded bg-red-100 text-red-700 hover:bg-red-200">Unresolved</button>
                                        </div>
                                    )}
                                </div>
                            );
                        })}
                    </div>
                )}
                <button onClick={() => setShowForm(true)}
                    className="flex items-center gap-1.5 text-xs font-semibold text-primary hover:bg-primary/10 px-2.5 py-1.5 rounded-lg mt-1">
                    <PackageSearch className="h-3.5 w-3.5" /> Report Lost Item
                </button>
            </Sec>

            <Dialog open={showForm} onOpenChange={v => !v && setShowForm(false)}>
                <DialogContent className="sm:max-w-md">
                    <DialogHeader>
                        <DialogTitle className="flex items-center gap-2">
                            <PackageSearch className="h-5 w-5 text-primary" />
                            Report Lost Item
                        </DialogTitle>
                    </DialogHeader>
                    <div>
                        <label className="text-xs font-medium text-muted-foreground">Item Description</label>
                        <textarea value={description} onChange={e => setDescription(e.target.value)}
                            placeholder="Describe the lost item..."
                            className="w-full mt-1 border rounded-lg px-3 py-2 text-sm bg-card text-foreground min-h-[80px] resize-none" />
                        <p className="text-[10px] text-muted-foreground mt-1">The driver will be notified via push notification.</p>
                    </div>
                    <DialogFooter>
                        <button onClick={() => setShowForm(false)} className="px-4 py-2 text-sm font-medium rounded-lg hover:bg-muted">Cancel</button>
                        <button onClick={handleReport} disabled={!description || loading}
                            className="px-4 py-2 text-sm font-semibold text-white bg-primary rounded-lg hover:bg-primary/90 disabled:opacity-50">
                            {loading ? "Reporting..." : "Report & Notify Driver"}
                        </button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </>
    );
}
