"use client";

import { Input } from "@/components/ui/input";
import { formatCurrency } from "@/lib/utils";
import { Car, Search, Clock, CheckCircle, XCircle, MapPin, Loader, Download, ChevronRight, ChevronLeft, User, SlidersHorizontal } from "lucide-react";
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
    onSelect: (ride: any) => void;
    page: number;
    totalPages: number;
    onPageChange: (p: number) => void;
}

export default function RideList({
    rides, allRides, totalCount, areas, loading, selectedId,
    search, onSearchChange, statusFilter, onStatusChange,
    areaFilter, onAreaChange, onSelect,
    page, totalPages, onPageChange,
}: RideListProps) {
    const statusCounts = (s: string) => s === "all" ? allRides.length : allRides.filter(r => r.status === s).length;

    return (
        <div className="bg-card border rounded-xl overflow-hidden">
            {/* Card Header - Title + Actions */}
            <div className="px-5 pt-5 pb-4 border-b">
                <div className="flex items-center justify-between mb-4">
                    <div>
                        <h2 className="text-lg font-semibold">All Rides</h2>
                        <p className="text-sm text-muted-foreground mt-0.5">
                            Showing {rides.length} of {totalCount} rides
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
                            onClick={() => exportToCsv("rides", rides, [
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

                {/* Status Filter Tabs */}
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
                                statusFilter === tab.value
                                    ? "bg-white/20"
                                    : "bg-background text-muted-foreground"
                            }`}>
                                {statusCounts(tab.value)}
                            </span>
                        </button>
                    ))}
                </div>

                {/* Search */}
                <div className="relative mt-3">
                    <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                    <Input
                        placeholder="Search by rider/driver name, phone, address, or ID..."
                        value={search}
                        onChange={e => onSearchChange(e.target.value)}
                        className="pl-9 h-9 text-sm bg-background"
                    />
                </div>
            </div>

            {/* Table */}
            {loading ? (
                <div className="flex items-center justify-center py-24">
                    <div className="flex flex-col items-center gap-3">
                        <div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" />
                        <p className="text-sm text-muted-foreground">Loading rides...</p>
                    </div>
                </div>
            ) : rides.length === 0 ? (
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
                                <th className="text-left text-[11px] font-semibold text-muted-foreground uppercase tracking-wider py-3 px-5">Status</th>
                                <th className="text-left text-[11px] font-semibold text-muted-foreground uppercase tracking-wider py-3 px-4">Route</th>
                                <th className="text-left text-[11px] font-semibold text-muted-foreground uppercase tracking-wider py-3 px-4 hidden lg:table-cell">Rider</th>
                                <th className="text-left text-[11px] font-semibold text-muted-foreground uppercase tracking-wider py-3 px-4 hidden lg:table-cell">Driver</th>
                                <th className="text-right text-[11px] font-semibold text-muted-foreground uppercase tracking-wider py-3 px-4">Fare</th>
                                <th className="text-right text-[11px] font-semibold text-muted-foreground uppercase tracking-wider py-3 px-4 hidden md:table-cell">Date</th>
                                <th className="w-10"></th>
                            </tr>
                        </thead>
                        <tbody className="divide-y divide-border">
                            {rides.map(ride => (
                                <tr
                                    key={ride.id}
                                    onClick={() => onSelect(ride)}
                                    className={`cursor-pointer transition-colors group ${
                                        selectedId === ride.id
                                            ? "bg-primary/5 hover:bg-primary/8"
                                            : "hover:bg-muted/40"
                                    }`}
                                >
                                    <td className="py-3 px-5">
                                        {getStatusBadge(ride.status)}
                                    </td>
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
                                                {ride.rider_phone && (
                                                    <p className="text-[11px] text-muted-foreground truncate">{ride.rider_phone}</p>
                                                )}
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
                                                {ride.driver_phone && (
                                                    <p className="text-[11px] text-muted-foreground truncate">{ride.driver_phone}</p>
                                                )}
                                            </div>
                                        </div>
                                    </td>
                                    <td className="py-3 px-4 text-right">
                                        <p className="text-sm font-bold">{formatCurrency(ride.total_fare || 0)}</p>
                                        {(ride.tip_amount || 0) > 0 && (
                                            <p className="text-[10px] font-semibold text-emerald-600 mt-0.5">
                                                +{formatCurrency(ride.tip_amount)} tip
                                            </p>
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
                            if (totalPages <= 7) {
                                pageNum = i;
                            } else if (page < 3) {
                                pageNum = i;
                            } else if (page > totalPages - 4) {
                                pageNum = totalPages - 7 + i;
                            } else {
                                pageNum = page - 3 + i;
                            }
                            return (
                                <button key={pageNum} onClick={() => onPageChange(pageNum)}
                                    className={`w-8 h-8 rounded-lg text-xs font-semibold transition ${
                                        page === pageNum
                                            ? "bg-primary text-white shadow-sm"
                                            : "hover:bg-muted text-muted-foreground"
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
