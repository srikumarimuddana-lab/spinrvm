"use client";

import { useEffect, useState } from "react";
import { getLostAndFoundItems, resolveLostItem } from "@/lib/api";
import { Input } from "@/components/ui/input";
import { PackageSearch, Search, CheckCircle, Clock, XCircle, Bell } from "lucide-react";

const STATUS_COLORS: Record<string, string> = {
    reported: "bg-amber-100 text-amber-700",
    driver_notified: "bg-blue-100 text-blue-700",
    resolved: "bg-emerald-100 text-emerald-700",
    unresolved: "bg-red-100 text-red-700",
};

const STATUS_ICONS: Record<string, any> = {
    reported: Clock,
    driver_notified: Bell,
    resolved: CheckCircle,
    unresolved: XCircle,
};

export default function LostAndFoundTab() {
    const [items, setItems] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [search, setSearch] = useState("");
    const [statusFilter, setStatusFilter] = useState("all");

    const loadItems = () => {
        setLoading(true);
        getLostAndFoundItems().then(setItems).catch(() => {}).finally(() => setLoading(false));
    };

    useEffect(() => { loadItems(); }, []);

    const filtered = items.filter(i => {
        const q = search.toLowerCase();
        const matchSearch = !search ||
            i.item_description?.toLowerCase().includes(q) ||
            i.ride_id?.toLowerCase().includes(q) ||
            i.rider_id?.toLowerCase().includes(q) ||
            i.driver_id?.toLowerCase().includes(q);
        const matchStatus = statusFilter === "all" || i.status === statusFilter;
        return matchSearch && matchStatus;
    });

    const handleResolve = async (id: string, status: "resolved" | "unresolved") => {
        try {
            await resolveLostItem(id, { status });
            loadItems();
        } catch (e: any) {
            alert(e.message || "Failed to update");
        }
    };

    const statusCounts = {
        all: items.length,
        reported: items.filter(i => i.status === "reported").length,
        driver_notified: items.filter(i => i.status === "driver_notified").length,
        resolved: items.filter(i => i.status === "resolved").length,
        unresolved: items.filter(i => i.status === "unresolved").length,
    };

    return (
        <div>
            {/* Stats */}
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-2 sm:gap-3 mb-4">
                {(["reported", "driver_notified", "resolved", "unresolved"] as const).map(s => {
                    const I = STATUS_ICONS[s];
                    return (
                        <div key={s} className="bg-card border rounded-xl p-2.5 sm:p-3 flex items-center gap-2 sm:gap-3">
                            <div className={`w-8 h-8 sm:w-9 sm:h-9 rounded-lg flex items-center justify-center shrink-0 ${STATUS_COLORS[s]}`}>
                                <I className="h-3.5 w-3.5 sm:h-4 sm:w-4" />
                            </div>
                            <div>
                                <p className="text-xl font-bold">{statusCounts[s]}</p>
                                <p className="text-[10px] text-muted-foreground capitalize">{s.replace(/_/g, " ")}</p>
                            </div>
                        </div>
                    );
                })}
            </div>

            {/* Filters */}
            <div className="flex gap-2 mb-3 flex-wrap">
                {["all", "reported", "driver_notified", "resolved", "unresolved"].map(s => (
                    <button key={s} onClick={() => setStatusFilter(s)}
                        className={`px-3 py-1.5 rounded-lg text-xs font-semibold ${statusFilter === s ? "bg-primary text-white" : "bg-muted text-muted-foreground"}`}>
                        {s === "all" ? "All" : s.replace(/_/g, " ")} ({statusCounts[s as keyof typeof statusCounts]})
                    </button>
                ))}
            </div>

            <div className="relative mb-3">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                <Input placeholder="Search item description, ride ID..." value={search} onChange={e => setSearch(e.target.value)} className="pl-9 h-9 text-sm" />
            </div>

            {/* List */}
            {loading ? (
                <div className="flex items-center justify-center py-16"><div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" /></div>
            ) : filtered.length === 0 ? (
                <div className="text-center py-16 text-muted-foreground"><PackageSearch className="h-10 w-10 mx-auto mb-3 opacity-30" /><p>No items found</p></div>
            ) : (
                <div className="space-y-2">
                    {filtered.map(item => {
                        const I = STATUS_ICONS[item.status] || Clock;
                        return (
                            <div key={item.id} className="flex items-start gap-3 p-3 bg-card border rounded-xl">
                                <I className={`h-5 w-5 mt-0.5 shrink-0 ${STATUS_COLORS[item.status]?.split(" ")[1] || "text-gray-500"}`} />
                                <div className="flex-1 min-w-0">
                                    <p className="text-sm font-medium">{item.item_description}</p>
                                    <div className="flex gap-3 text-xs text-muted-foreground mt-1 flex-wrap">
                                        <span>Ride: <span className="font-mono">{item.ride_id?.slice(0, 8)}</span></span>
                                        <span>Rider: <span className="font-mono">{item.rider_id?.slice(0, 8)}</span></span>
                                        <span>Driver: <span className="font-mono">{item.driver_id?.slice(0, 8)}</span></span>
                                    </div>
                                    {item.admin_notes && <p className="text-xs text-muted-foreground mt-1">Note: {item.admin_notes}</p>}
                                    <p className="text-[10px] text-muted-foreground mt-1">
                                        {item.created_at ? new Date(item.created_at).toLocaleString() : ""}
                                    </p>
                                </div>
                                <div className="flex items-center gap-2 shrink-0">
                                    <span className={`text-[10px] font-bold px-2 py-0.5 rounded ${STATUS_COLORS[item.status] || "bg-gray-100 text-gray-600"}`}>
                                        {item.status?.replace(/_/g, " ").toUpperCase()}
                                    </span>
                                    {(item.status === "reported" || item.status === "driver_notified") && (
                                        <div className="flex gap-1">
                                            <button onClick={() => handleResolve(item.id, "resolved")} className="text-[10px] px-2 py-1 rounded bg-emerald-100 text-emerald-700 hover:bg-emerald-200 font-semibold">Resolve</button>
                                            <button onClick={() => handleResolve(item.id, "unresolved")} className="text-[10px] px-2 py-1 rounded bg-red-100 text-red-700 hover:bg-red-200 font-semibold">Unresolved</button>
                                        </div>
                                    )}
                                </div>
                            </div>
                        );
                    })}
                </div>
            )}
        </div>
    );
}
