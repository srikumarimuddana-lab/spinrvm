"use client";

import { useState, useMemo } from "react";
import { Input } from "@/components/ui/input";
import { formatCurrency } from "@/lib/utils";
import { Car, Search, Clock, CheckCircle, XCircle, MapPin, Loader, Download, ChevronRight, ChevronLeft, User, SlidersHorizontal, ArrowUpDown, ArrowUp, ArrowDown, CalendarRange, X } from "lucide-react";
import { getStatusBadge, fmtTime } from "./ride-ui-helpers";
import { exportToCsv } from "@/lib/export-csv";

const STATUS_TABS = [
    { value: "all", label: "All", icon: Car },
    { value: "searching", label: "Searching", icon: Loader },
    { value: "driver_assigned", label: "Assigned", icon: MapPin },
    { value: "in_progress", label: "In Progress", icon: Clock },
    { value: "completed", label: "Completed", icon: CheckCircle },
    { value: "cancelled", label: "Cancelled", icon: XCircle },
];

type SortKey = "status" | "pickup_address" | "rider_name" | "driver_name" | "total_fare" | "created_at";
type SortDir = "asc" | "desc";

interface RideListProps {
    rides: any[];
    allRides: any[];
    totalCount: number;
    areas: any[];
    loading: boolean;
    selectedId?: string;
    search: string;
    onSearchChange: (v: string) => void;
    statusFilter: string;
    onStatusChange: (v: string) => void;
    areaFilter: string;
    onAreaChange: (v: string) => void;
    dateFrom: string;
    onDateFromChange: (v: string) => void;
    dateTo: string;
    onDateToChange: (v: string) => void;
    onSelect: (ride: any) => void;
    page: number;
    totalPages: number;
    onPageChange: (p: number) => void;
}

export default function RideList({
    rides, allRides, totalCount, areas, loading, selectedId,
    search, onSearchChange, statusFilter, onStatusChange,
    areaFilter, onAreaChange, dateFrom, onDateFromChange, dateTo, onDateToChange,
    onSelect, page, totalPages, onPageChange,
}: RideListProps) {
    const [sortKey, setSortKey] = useState<SortKey>("created_at");
    const [sortDir, setSortDir] = useState<SortDir>("desc");

    // Column filters
    const [fareFilter, setFareFilter] = useState<"all" | "under10" | "10to25" | "25to50" | "over50">("all");
    const [riderFilter, setRiderFilter] = useState("");
    const [driverFilter, setDriverFilter] = useState("");

    const statusCounts = (s: string) => s === "all" ? allRides.length : allRides.filter(r => r.status === s).length;

    const handleSort = (key: SortKey) => {
        if (sortKey === key) {
            setSortDir(d => d === "asc" ? "desc" : "asc");
        } else {
            setSortKey(key);
            setSortDir(key === "created_at" || key === "total_fare" ? "desc" : "asc");
        }
    };

    const SortIcon = ({ col }: { col: SortKey }) => {
        if (sortKey !== col) return <ArrowUpDown className="h-3 w-3 opacity-30" />;
        return sortDir === "asc" ? <ArrowUp className="h-3 w-3" /> : <ArrowDown className="h-3 w-3" />;
    };

    // Apply column filters + sorting
    const sortedRides = useMemo(() => {
        let data = [...rides];

        // Column filters
        if (fareFilter !== "all") {
            data = data.filter(r => {
                const f = r.total_fare || 0;
                if (fareFilter === "under10") return f < 10;
                if (fareFilter === "10to25") return f >= 10 && f < 25;
                if (fareFilter === "25to50") return f >= 25 && f < 50;
                if (fareFilter === "over50") return f >= 50;
                return true;
            });
        }
        if (riderFilter) {
            const q = riderFilter.toLowerCase();
            data = data.filter(r => r.rider_name?.toLowerCase().includes(q));
        }
        if (driverFilter) {
            const q = driverFilter.toLowerCase();
            data = data.filter(r => r.driver_name?.toLowerCase().includes(q));
        }

        // Sort
        data.sort((a, b) => {
            let av: any, bv: any;
            if (sortKey === "total_fare") {
                av = a.total_fare || 0; bv = b.total_fare || 0;
            } else if (sortKey === "created_at") {
                av = a.created_at || ""; bv = b.created_at || "";
            } else {
                av = (a[sortKey] || "").toLowerCase(); bv = (b[sortKey] || "").toLowerCase();
            }
            if (av < bv) return sortDir === "asc" ? -1 : 1;
            if (av > bv) return sortDir === "asc" ? 1 : -1;
            return 0;
        });
        return data;
    }, [rides, sortKey, sortDir, fareFilter, riderFilter, driverFilter]);

    const hasColumnFilters = fareFilter !== "all" || riderFilter || driverFilter;

    return (
        <div className="bg-card border rounded-xl overflow-hidden">
            {/* Header */}
            <div className="px-5 pt-5 pb-4 border-b">
                <div className="flex items-center justify-between mb-4">
                    <div>
                        <h2 className="text-lg font-semibold">All Rides</h2>
                        <p className="text-sm text-muted-foreground mt-0.5">
                            Showing {sortedRides.length} of {totalCount} rides
                            {totalPages > 1 && <span className="ml-1 text-muted-foreground/70">&middot; Page {page + 1} of {totalPages}</span>}
                        </p>
                    </div>
                    <div className="flex items-center gap-2">
                        <div className="flex items-center gap-1.5">
                            <SlidersHorizontal className="h-3.5 w-3.5 text-muted-foreground" />
                            <select value={areaFilter} onChange={e => onAreaChange(e.target.value)}
                                className="text-xs font-medium border rounded-lg px-2.5 py-1.5 bg-background text-foreground focus:outline-none focus:ring-2 focus:ring-primary/20 transition">
                                <option value="all">All Areas</option>
                                {areas.map(a => <option key={a.id} value={a.id}>{a.name}</option>)}
                            </select>
                        </div>
                        <button
                            onClick={() => exportToCsv("rides", sortedRides, [
                                { key: "id", label: "ID" }, { key: "pickup_address", label: "Pickup" }, { key: "dropoff_address", label: "Dropoff" },
                                { key: "status", label: "Status" }, { key: "total_fare", label: "Fare" }, { key: "tip_amount", label: "Tip" },
                                { key: "distance_km", label: "km" }, { key: "duration_minutes", label: "min" }, { key: "created_at", label: "Date" },
                            ])}
                            className="flex items-center gap-1.5 text-xs font-semibold text-muted-foreground hover:text-foreground px-3 py-1.5 rounded-lg border hover:bg-muted transition"
                        >
                            <Download className="h-3.5 w-3.5" /> Export
                        </button>
                    </div>
                </div>

                {/* Status Tabs */}
                <div className="flex gap-1.5 overflow-x-auto pb-0.5">
                    {STATUS_TABS.map(tab => (
                        <button key={tab.value} onClick={() => onStatusChange(tab.value)}
                            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold whitespace-nowrap transition-all ${
                                statusFilter === tab.value
                                    ? "bg-primary text-white shadow-sm"
                                    : "bg-muted/60 text-muted-foreground hover:bg-muted hover:text-foreground"
                            }`}>
                            <tab.icon className="h-3.5 w-3.5" />
                            {tab.label}
                            <span className={`ml-0.5 px-1.5 py-0.5 rounded-md text-[10px] font-bold ${
                                statusFilter === tab.value ? "bg-white/20" : "bg-background text-muted-foreground"
                            }`}>
                                {statusCounts(tab.value)}
                            </span>
                        </button>
                    ))}
                </div>

                {/* Search + Date Filter */}
                <div className="flex items-center gap-2 mt-3">
                    <div className="relative flex-1">
                        <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                        <Input placeholder="Search by rider/driver name, phone, address, or ID..."
                            value={search} onChange={e => onSearchChange(e.target.value)}
                            className="pl-9 h-9 text-sm bg-background" />
                    </div>
                    <div className="flex items-center gap-1.5 shrink-0">
                        <CalendarRange className="h-3.5 w-3.5 text-muted-foreground" />
                        <input type="date" value={dateFrom} onChange={e => onDateFromChange(e.target.value)}
                            className="text-xs border rounded-lg px-2 py-1.5 bg-background w-[120px]" />
                        <span className="text-xs text-muted-foreground">to</span>
                        <input type="date" value={dateTo} onChange={e => onDateToChange(e.target.value)}
                            className="text-xs border rounded-lg px-2 py-1.5 bg-background w-[120px]" />
                        {(dateFrom || dateTo) && (
                            <button onClick={() => { onDateFromChange(""); onDateToChange(""); }}
                                className="p-1 text-muted-foreground hover:text-foreground"><X className="h-3.5 w-3.5" /></button>
                        )}
                    </div>
                </div>

                {/* Active column filters indicator */}
                {hasColumnFilters && (
                    <div className="flex items-center gap-2 mt-2">
                        <span className="text-[10px] text-muted-foreground font-semibold uppercase">Column filters:</span>
                        {fareFilter !== "all" && (
                            <span className="flex items-center gap-1 bg-primary/10 text-primary text-[10px] font-semibold px-2 py-0.5 rounded-md">
                                Fare: {fareFilter.replace("under", "<$").replace("over", ">$").replace("to", "-$")}
                                <button onClick={() => setFareFilter("all")}><X className="h-2.5 w-2.5" /></button>
                            </span>
                        )}
                        {riderFilter && (
                            <span className="flex items-center gap-1 bg-blue-500/10 text-blue-600 text-[10px] font-semibold px-2 py-0.5 rounded-md">
                                Rider: {riderFilter}
                                <button onClick={() => setRiderFilter("")}><X className="h-2.5 w-2.5" /></button>
                            </span>
                        )}
                        {driverFilter && (
                            <span className="flex items-center gap-1 bg-emerald-500/10 text-emerald-600 text-[10px] font-semibold px-2 py-0.5 rounded-md">
                                Driver: {driverFilter}
                                <button onClick={() => setDriverFilter("")}><X className="h-2.5 w-2.5" /></button>
                            </span>
                        )}
                        <button onClick={() => { setFareFilter("all"); setRiderFilter(""); setDriverFilter(""); }}
                            className="text-[10px] text-muted-foreground hover:text-foreground underline">Clear all</button>
                    </div>
                )}
            </div>

            {/* Table */}
            {loading ? (
                <div className="flex items-center justify-center py-24">
                    <div className="flex flex-col items-center gap-3">
                        <div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" />
                        <p className="text-sm text-muted-foreground">Loading rides...</p>
                    </div>
                </div>
            ) : sortedRides.length === 0 ? (
                <div className="flex flex-col items-center justify-center py-20 text-muted-foreground">
                    <div className="w-16 h-16 rounded-2xl bg-muted/50 flex items-center justify-center mb-4">
                        <Car className="h-8 w-8 opacity-30" />
                    </div>
                    <p className="text-sm font-medium">No rides found</p>
                    <p className="text-xs mt-1">Try adjusting your search or filters</p>
                </div>
            ) : (
                <div className="overflow-x-auto">
                    <table className="w-full">
                        <thead>
                            <tr className="border-b bg-muted/30">
                                <th className="text-left py-2 px-5">
                                    <button onClick={() => handleSort("status")} className="flex items-center gap-1 text-[11px] font-semibold text-muted-foreground uppercase tracking-wider hover:text-foreground transition">
                                        Status <SortIcon col="status" />
                                    </button>
                                </th>
                                <th className="text-left py-2 px-4">
                                    <button onClick={() => handleSort("pickup_address")} className="flex items-center gap-1 text-[11px] font-semibold text-muted-foreground uppercase tracking-wider hover:text-foreground transition">
                                        Route <SortIcon col="pickup_address" />
                                    </button>
                                </th>
                                <th className="text-left py-2 px-4 hidden lg:table-cell">
                                    <div>
                                        <button onClick={() => handleSort("rider_name")} className="flex items-center gap-1 text-[11px] font-semibold text-muted-foreground uppercase tracking-wider hover:text-foreground transition">
                                            Rider <SortIcon col="rider_name" />
                                        </button>
                                        <input type="text" value={riderFilter} onChange={e => setRiderFilter(e.target.value)}
                                            placeholder="Filter..." className="mt-1 w-full text-[10px] border rounded px-1.5 py-0.5 bg-background text-foreground focus:outline-none focus:ring-1 focus:ring-primary/30" />
                                    </div>
                                </th>
                                <th className="text-left py-2 px-4 hidden lg:table-cell">
                                    <div>
                                        <button onClick={() => handleSort("driver_name")} className="flex items-center gap-1 text-[11px] font-semibold text-muted-foreground uppercase tracking-wider hover:text-foreground transition">
                                            Driver <SortIcon col="driver_name" />
                                        </button>
                                        <input type="text" value={driverFilter} onChange={e => setDriverFilter(e.target.value)}
                                            placeholder="Filter..." className="mt-1 w-full text-[10px] border rounded px-1.5 py-0.5 bg-background text-foreground focus:outline-none focus:ring-1 focus:ring-primary/30" />
                                    </div>
                                </th>
                                <th className="text-right py-2 px-4">
                                    <div className="flex flex-col items-end">
                                        <button onClick={() => handleSort("total_fare")} className="flex items-center gap-1 text-[11px] font-semibold text-muted-foreground uppercase tracking-wider hover:text-foreground transition">
                                            Fare <SortIcon col="total_fare" />
                                        </button>
                                        <select value={fareFilter} onChange={e => setFareFilter(e.target.value as any)}
                                            className="mt-1 text-[10px] border rounded px-1 py-0.5 bg-background text-foreground focus:outline-none">
                                            <option value="all">All</option>
                                            <option value="under10">&lt; $10</option>
                                            <option value="10to25">$10 - $25</option>
                                            <option value="25to50">$25 - $50</option>
                                            <option value="over50">&gt; $50</option>
                                        </select>
                                    </div>
                                </th>
                                <th className="text-right py-2 px-4 hidden md:table-cell">
                                    <button onClick={() => handleSort("created_at")} className="flex items-center gap-1 text-[11px] font-semibold text-muted-foreground uppercase tracking-wider hover:text-foreground transition ml-auto">
                                        Date <SortIcon col="created_at" />
                                    </button>
                                </th>
                                <th className="w-10"></th>
                            </tr>
                        </thead>
                        <tbody className="divide-y divide-border">
                            {sortedRides.map(ride => (
                                <tr key={ride.id} onClick={() => onSelect(ride)}
                                    className={`cursor-pointer transition-colors group ${
                                        selectedId === ride.id ? "bg-primary/5 hover:bg-primary/8" : "hover:bg-muted/40"
                                    }`}>
                                    <td className="py-3 px-5">{getStatusBadge(ride.status)}</td>
                                    <td className="py-3 px-4 max-w-[320px]">
                                        <p className="text-sm font-medium truncate">{ride.pickup_address || "—"}</p>
                                        <p className="text-xs text-muted-foreground truncate mt-0.5">
                                            <span className="text-muted-foreground/60">to</span> {ride.dropoff_address || "—"}
                                        </p>
                                    </td>
                                    <td className="py-3 px-4 hidden lg:table-cell">
                                        <div className="flex items-center gap-2">
                                            <div className="w-7 h-7 rounded-full bg-blue-100 dark:bg-blue-900/30 flex items-center justify-center shrink-0">
                                                <User className="h-3.5 w-3.5 text-blue-600 dark:text-blue-400" />
                                            </div>
                                            <div className="min-w-0">
                                                <p className="text-sm font-medium truncate">{ride.rider_name || "—"}</p>
                                                {ride.rider_phone && <p className="text-[11px] text-muted-foreground truncate">{ride.rider_phone}</p>}
                                            </div>
                                        </div>
                                    </td>
                                    <td className="py-3 px-4 hidden lg:table-cell">
                                        <div className="flex items-center gap-2">
                                            <div className="w-7 h-7 rounded-full bg-emerald-100 dark:bg-emerald-900/30 flex items-center justify-center shrink-0">
                                                <Car className="h-3.5 w-3.5 text-emerald-600 dark:text-emerald-400" />
                                            </div>
                                            <div className="min-w-0">
                                                <p className="text-sm font-medium truncate">{ride.driver_name || "—"}</p>
                                                {ride.driver_phone && <p className="text-[11px] text-muted-foreground truncate">{ride.driver_phone}</p>}
                                            </div>
                                        </div>
                                    </td>
                                    <td className="py-3 px-4 text-right">
                                        <p className="text-sm font-bold">{formatCurrency(ride.total_fare || 0)}</p>
                                        {(ride.tip_amount || 0) > 0 && (
                                            <p className="text-[10px] font-semibold text-emerald-600 mt-0.5">+{formatCurrency(ride.tip_amount)} tip</p>
                                        )}
                                    </td>
                                    <td className="py-3 px-4 text-right hidden md:table-cell">
                                        <p className="text-xs text-muted-foreground whitespace-nowrap">{fmtTime(ride.created_at)}</p>
                                    </td>
                                    <td className="py-3 pr-4">
                                        <ChevronRight className="h-4 w-4 text-muted-foreground/30 group-hover:text-muted-foreground/60 transition-colors" />
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}

            {/* Pagination */}
            {totalPages > 1 && (
                <div className="flex items-center justify-between px-5 py-3.5 border-t bg-muted/20">
                    <button onClick={() => onPageChange(page - 1)} disabled={page === 0}
                        className="flex items-center gap-1.5 text-xs font-semibold px-3 py-1.5 rounded-lg border bg-background hover:bg-muted disabled:opacity-30 disabled:cursor-not-allowed transition">
                        <ChevronLeft className="h-3.5 w-3.5" /> Previous
                    </button>
                    <div className="flex items-center gap-1">
                        {Array.from({ length: Math.min(totalPages, 7) }, (_, i) => {
                            let pageNum: number;
                            if (totalPages <= 7) { pageNum = i; }
                            else if (page < 3) { pageNum = i; }
                            else if (page > totalPages - 4) { pageNum = totalPages - 7 + i; }
                            else { pageNum = page - 3 + i; }
                            return (
                                <button key={pageNum} onClick={() => onPageChange(pageNum)}
                                    className={`w-8 h-8 rounded-lg text-xs font-semibold transition ${
                                        page === pageNum ? "bg-primary text-white shadow-sm" : "hover:bg-muted text-muted-foreground"
                                    }`}>
                                    {pageNum + 1}
                                </button>
                            );
                        })}
                    </div>
                    <button onClick={() => onPageChange(page + 1)} disabled={page >= totalPages - 1}
                        className="flex items-center gap-1.5 text-xs font-semibold px-3 py-1.5 rounded-lg border bg-background hover:bg-muted disabled:opacity-30 disabled:cursor-not-allowed transition">
                        Next <ChevronRight className="h-3.5 w-3.5" />
                    </button>
                </div>
            )}
        </div>
    );
}
