"use client";

import { useEffect, useState } from "react";
import { getFlags } from "@/lib/api";
import { Input } from "@/components/ui/input";
import { Flag, Search, User, Car, AlertTriangle } from "lucide-react";

const REASON_COLORS: Record<string, string> = {
    vomited_in_car: "bg-red-100 text-red-700",
    misbehaved: "bg-amber-100 text-amber-700",
    no_show: "bg-gray-100 text-gray-600",
    damage: "bg-orange-100 text-orange-700",
    fraud: "bg-purple-100 text-purple-700",
    other: "bg-blue-100 text-blue-700",
};

export default function FlagsTab() {
    const [flags, setFlags] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [search, setSearch] = useState("");
    const [typeFilter, setTypeFilter] = useState("all");

    useEffect(() => {
        setLoading(true);
        getFlags().then(setFlags).catch(() => {}).finally(() => setLoading(false));
    }, []);

    const filtered = flags.filter(f => {
        const q = search.toLowerCase();
        const matchSearch = !search ||
            f.reason?.toLowerCase().includes(q) ||
            f.description?.toLowerCase().includes(q) ||
            f.target_id?.toLowerCase().includes(q) ||
            f.ride_id?.toLowerCase().includes(q);
        const matchType = typeFilter === "all" || f.target_type === typeFilter;
        return matchSearch && matchType;
    });

    const riderFlags = flags.filter(f => f.target_type === "rider");
    const driverFlags = flags.filter(f => f.target_type === "driver");

    return (
        <div>
            {/* Stats */}
            <div className="grid grid-cols-3 gap-2 sm:gap-3 mb-4">
                <div className="bg-card border rounded-xl p-2.5 sm:p-3 flex items-center gap-2">
                    <div className="w-8 h-8 sm:w-9 sm:h-9 rounded-lg flex items-center justify-center shrink-0 bg-red-100 text-red-600">
                        <Flag className="h-3.5 w-3.5 sm:h-4 sm:w-4" />
                    </div>
                    <div>
                        <p className="text-lg sm:text-xl font-bold">{flags.length}</p>
                        <p className="text-[10px] text-muted-foreground">Total Flags</p>
                    </div>
                </div>
                <div className="bg-card border rounded-xl p-2.5 sm:p-3 flex items-center gap-2">
                    <div className="w-8 h-8 sm:w-9 sm:h-9 rounded-lg flex items-center justify-center shrink-0 bg-blue-100 text-blue-600">
                        <User className="h-3.5 w-3.5 sm:h-4 sm:w-4" />
                    </div>
                    <div>
                        <p className="text-lg sm:text-xl font-bold">{riderFlags.length}</p>
                        <p className="text-[10px] text-muted-foreground">Rider Flags</p>
                    </div>
                </div>
                <div className="bg-card border rounded-xl p-2.5 sm:p-3 flex items-center gap-2">
                    <div className="w-8 h-8 sm:w-9 sm:h-9 rounded-lg flex items-center justify-center shrink-0 bg-emerald-100 text-emerald-600">
                        <Car className="h-3.5 w-3.5 sm:h-4 sm:w-4" />
                    </div>
                    <div>
                        <p className="text-lg sm:text-xl font-bold">{driverFlags.length}</p>
                        <p className="text-[10px] text-muted-foreground">Driver Flags</p>
                    </div>
                </div>
            </div>

            {/* Filters */}
            <div className="flex gap-2 mb-3 flex-wrap">
                {[
                    { value: "all", label: "All" },
                    { value: "rider", label: "Riders" },
                    { value: "driver", label: "Drivers" },
                ].map(t => (
                    <button key={t.value} onClick={() => setTypeFilter(t.value)}
                        className={`px-3 py-1.5 rounded-lg text-xs font-semibold ${typeFilter === t.value ? "bg-primary text-white" : "bg-muted text-muted-foreground"}`}>
                        {t.label}
                    </button>
                ))}
            </div>

            <div className="relative mb-3">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                <Input placeholder="Search reason, description, target ID, ride ID..." value={search} onChange={e => setSearch(e.target.value)} className="pl-9 h-9 text-sm" />
            </div>

            {/* List */}
            {loading ? (
                <div className="flex items-center justify-center py-16"><div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" /></div>
            ) : filtered.length === 0 ? (
                <div className="text-center py-16 text-muted-foreground"><Flag className="h-10 w-10 mx-auto mb-3 opacity-30" /><p>No flags found</p></div>
            ) : (
                <div className="space-y-2">
                    {filtered.map(flag => (
                        <div key={flag.id} className="flex items-start gap-3 p-3 bg-card border rounded-xl">
                            <Flag className={`h-5 w-5 mt-0.5 shrink-0 ${flag.target_type === "rider" ? "text-blue-500" : "text-emerald-500"}`} />
                            <div className="flex-1 min-w-0">
                                <div className="flex items-center gap-2 flex-wrap">
                                    <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded ${flag.target_type === "rider" ? "bg-blue-100 text-blue-700" : "bg-emerald-100 text-emerald-700"}`}>
                                        {flag.target_type?.toUpperCase()}
                                    </span>
                                    <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded ${REASON_COLORS[flag.reason] || "bg-gray-100 text-gray-600"}`}>
                                        {flag.reason?.replace(/_/g, " ")}
                                    </span>
                                    {!flag.is_active && (
                                        <span className="text-[10px] font-bold px-1.5 py-0.5 rounded bg-gray-100 text-gray-500">INACTIVE</span>
                                    )}
                                </div>
                                {flag.description && <p className="text-sm mt-1">{flag.description}</p>}
                                <div className="flex gap-3 text-xs text-muted-foreground mt-1 flex-wrap">
                                    <span>Target: <span className="font-mono">{flag.target_id?.slice(0, 12)}</span></span>
                                    {flag.ride_id && <span>Ride: <span className="font-mono">{flag.ride_id?.slice(0, 8)}</span></span>}
                                    <span>{flag.created_at ? new Date(flag.created_at).toLocaleString() : ""}</span>
                                </div>
                            </div>
                        </div>
                    ))}
                </div>
            )}
        </div>
    );
}
