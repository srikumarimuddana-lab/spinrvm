"use client";

import { Input } from "@/components/ui/input";
import { formatCurrency } from "@/lib/utils";
import { Car, Search, Clock, CheckCircle, XCircle, MapPin, Loader, Download, ChevronRight, ChevronLeft } from "lucide-react";
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
        <div className="flex flex-col w-full h-full">
            <div className="flex items-center justify-between mb-3">
                <div>
                    <h1 className="text-2xl font-bold">Rides</h1>
                    <p className="text-sm text-muted-foreground">
                        Showing {rides.length} of {totalCount} rides
                        {totalPages > 1 && <span className="ml-1">(page {page + 1}/{totalPages})</span>}
                    </p>
                </div>
                <button
                    onClick={() => exportToCsv("rides", rides, [
                        { key: "id", label: "ID" }, { key: "pickup_address", label: "Pickup" }, { key: "dropoff_address", label: "Dropoff" },
                        { key: "status", label: "Status" }, { key: "total_fare", label: "Fare" }, { key: "tip_amount", label: "Tip" },
                        { key: "distance_km", label: "km" }, { key: "duration_minutes", label: "min" }, { key: "created_at", label: "Date" },
                    ])}
                    className="flex items-center gap-2 text-sm font-medium text-muted-foreground hover:text-foreground px-3 py-1.5 rounded-lg hover:bg-muted"
                >
                    <Download className="h-4 w-4" /> Export
                </button>
            </div>

            <div className="flex gap-2 mb-2 flex-wrap">
                <div className="flex gap-1 overflow-x-auto">
                    {STATUS_TABS.map(tab => (
                        <button key={tab.value} onClick={() => onStatusChange(tab.value)}
                            className={`flex items-center gap-1 px-2.5 py-1.5 rounded-lg text-xs font-semibold whitespace-nowrap transition ${statusFilter === tab.value ? "bg-primary text-white" : "bg-muted text-muted-foreground"
                                }`}>
                            <tab.icon className="h-3 w-3" /> {tab.label}
                            <span className={`ml-0.5 px-1 rounded text-[10px] ${statusFilter === tab.value ? "bg-white/20" : "bg-background"}`}>{statusCounts(tab.value)}</span>
                        </button>
                    ))}
                </div>
                <select value={areaFilter} onChange={e => onAreaChange(e.target.value)}
                    className="text-xs border rounded-lg px-2 py-1.5 bg-card text-foreground">
                    <option value="all">All Areas</option>
                    {areas.map(a => <option key={a.id} value={a.id}>{a.name}</option>)}
                </select>
            </div>

            <div className="relative mb-2">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                <Input placeholder="Search rider/driver name, phone, address, ID..." value={search} onChange={e => onSearchChange(e.target.value)} className="pl-9 h-9 text-sm" />
            </div>

            <div className="flex-1 overflow-y-auto space-y-1.5 pr-1">
                {loading ? (
                    <div className="flex items-center justify-center py-20"><div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" /></div>
                ) : rides.length === 0 ? (
                    <div className="text-center py-16 text-muted-foreground"><Car className="h-10 w-10 mx-auto mb-3 opacity-30" /><p>No rides found</p></div>
                ) : rides.map(ride => (
                    <div key={ride.id} onClick={() => onSelect(ride)}
                        className={`flex items-center gap-2.5 p-2.5 rounded-xl border cursor-pointer transition-all hover:shadow-sm ${selectedId === ride.id ? "border-primary bg-primary/5" : "border-border"}`}>
                        {getStatusBadge(ride.status)}
                        <div className="flex-1 min-w-0">
                            <p className="text-sm font-medium truncate">{ride.pickup_address || "—"}</p>
                            <p className="text-xs text-muted-foreground truncate">→ {ride.dropoff_address || "—"}</p>
                        </div>
                        <div className="text-right shrink-0">
                            <p className="text-sm font-bold">{formatCurrency(ride.total_fare || 0)}</p>
                            <p className="text-[10px] text-muted-foreground">{fmtTime(ride.created_at)}</p>
                        </div>
                        <ChevronRight className="h-4 w-4 text-muted-foreground/40 shrink-0" />
                    </div>
                ))}
            </div>

            {/* Pagination */}
            {totalPages > 1 && (
                <div className="flex items-center justify-between pt-3 border-t mt-2">
                    <button onClick={() => onPageChange(page - 1)} disabled={page === 0}
                        className="flex items-center gap-1 text-xs font-semibold px-3 py-1.5 rounded-lg hover:bg-muted disabled:opacity-30 disabled:cursor-not-allowed">
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
                                    className={`w-7 h-7 rounded-lg text-xs font-semibold ${page === pageNum ? "bg-primary text-white" : "hover:bg-muted text-muted-foreground"}`}>
                                    {pageNum + 1}
                                </button>
                            );
                        })}
                    </div>
                    <button onClick={() => onPageChange(page + 1)} disabled={page >= totalPages - 1}
                        className="flex items-center gap-1 text-xs font-semibold px-3 py-1.5 rounded-lg hover:bg-muted disabled:opacity-30 disabled:cursor-not-allowed">
                        Next <ChevronRight className="h-3.5 w-3.5" />
                    </button>
                </div>
            )}
        </div>
    );
}
