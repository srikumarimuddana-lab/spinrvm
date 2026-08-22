"use client";

import { useState } from "react";
import { Input } from "@/components/ui/input";
import { formatCurrency } from "@/lib/utils";
import { Car, Search, Clock, CheckCircle, XCircle, MapPin, Loader, Download, ChevronRight, ChevronLeft, User, SlidersHorizontal, ArrowUpDown, ArrowUp, ArrowDown, CalendarRange, X, CalendarClock, UserX } from "lucide-react";
import { getStatusBadge, fmtTime, fmtKm, rideDistances } from "./ride-ui-helpers";
import { exportToCsv } from "@/lib/export-csv";

const STATUS_TABS = [
    { value: "all", label: "All", icon: Car },
    { value: "scheduled", label: "Scheduled", icon: CalendarClock },
    { value: "searching", label: "Searching", icon: Loader },
    { value: "driver_assigned", label: "Assigned", icon: MapPin },
    { value: "in_progress", label: "In Progress", icon: Clock },
    { value: "completed", label: "Completed", icon: CheckCircle },
    { value: "cancelled", label: "Cancelled", icon: XCircle },
    { value: "no_driver_found", label: "No Driver Found", icon: UserX },
];

type SortKey = "status" | "pickup_address" | "total_fare" | "created_at";

interface RideListProps {
    rides: any[];
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
    pageSize: number;
    pageSizes: readonly number[];
    onPageSizeChange: (size: any) => void;
    totalPages: number;
    onPageChange: (p: number) => void;
    sortBy: string;
    sortDir: "asc" | "desc";
    onSortChange: (key: string, dir: "asc" | "desc") => void;
    onExport: () => Promise<any[]>;
}

export default function RideList({
    rides, totalCount, areas, loading, selectedId,
    search, onSearchChange, statusFilter, onStatusChange,
    areaFilter, onAreaChange, dateFrom, onDateFromChange, dateTo, onDateToChange,
    onSelect, page, pageSize, pageSizes, onPageSizeChange, totalPages, onPageChange,
    sortBy, sortDir, onSortChange, onExport,
}: RideListProps) {
    const [exporting, setExporting] = useState(false);

    const handleExportClick = async () => {
        setExporting(true);
        try {
            const allRides = await onExport();
            exportToCsv("rides", allRides, [
                { key: "ride_code", label: "Ride Code" },
                { key: "id", label: "UUID" },
                { key: "pickup_address", label: "Pickup" },
                { key: "dropoff_address", label: "Dropoff" },
                { key: "status", label: "Status" },
                { key: "total_fare", label: "Fare" },
                { key: "tip_amount", label: "Tip" },
                { label: "To Pickup km", value: (r) => fmtKm(rideDistances(r).toPickupKm) },
                { label: "Trip km", value: (r) => fmtKm(rideDistances(r).tripKm) },
                { label: "Total km", value: (r) => fmtKm(rideDistances(r).totalKm) },
                { key: "planned_distance_km", label: "Planned km" },
                { key: "duration_minutes", label: "Min" },
                { key: "driver_name", label: "Driver" },
                { key: "driver_id", label: "Driver ID" },
                { key: "rider_name", label: "Rider" },
                { key: "created_at", label: "Requested At" },
                { key: "ride_completed_at", label: "Completed At" },
            ]);
        } catch {
            // export failed silently
        } finally {
            setExporting(false);
        }
    };

    const handleSort = (key: SortKey) => {
        if (sortBy === key) {
            onSortChange(key, sortDir === "asc" ? "desc" : "asc");
        } else {
            onSortChange(key, key === "created_at" || key === "total_fare" ? "desc" : "asc");
        }
    };

    const SortIcon = ({ col }: { col: SortKey }) => {
        if (sortBy !== col) return <ArrowUpDown className="h-3 w-3 opacity-30" />;
        return sortDir === "asc" ? <ArrowUp className="h-3 w-3" /> : <ArrowDown className="h-3 w-3" />;
    };

    const startItem = page * pageSize + 1;
    const endItem = Math.min((page + 1) * pageSize, totalCount);

    return (
        <div className="bg-card border rounded-xl overflow-hidden">
            {/* Header */}
            <div className="px-5 pt-5 pb-4 border-b">
                <div className="flex items-center justify-between mb-4">
                    <div>
                        <h2 className="text-lg font-semibold">All Rides</h2>
                        <p className="text-sm text-muted-foreground mt-0.5">
                            {totalCount > 0
                                ? `Showing ${startItem}–${endItem} of ${totalCount} rides`
                                : "No rides found"}
                            {totalPages > 1 && <span className="ml-1 text-muted-foreground/70">&middot; Page {page + 1} of {totalPages}</span>}
                        </p>
                    </div>
                    <div className="flex items-center gap-2">
                        <div className="flex items-center gap-1.5">
                            <SlidersHorizontal className="h-3.5 w-3.5 text-muted-foreground" />
                            <select value={areaFilter} onChange={e => onAreaChange(e.target.value)}
                                aria-label="Filter by service area"
                                className="text-xs font-medium border rounded-lg px-2.5 py-1.5 bg-background text-foreground focus:outline-none focus:ring-2 focus:ring-primary/20 transition">
                                <option value="all">All Areas</option>
                                {areas.map(a => <option key={a.id} value={a.id}>{a.name}</option>)}
                            </select>
                        </div>
                        <button
                            onClick={handleExportClick}
                            disabled={exporting}
                            className="flex items-center gap-1.5 text-xs font-semibold text-muted-foreground hover:text-foreground px-3 py-1.5 rounded-lg border hover:bg-muted transition disabled:opacity-50 disabled:cursor-not-allowed"
                        >
                            <Download className="h-3.5 w-3.5" /> {exporting ? "Exporting..." : "Export"}
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
                        </button>
                    ))}
                </div>

                {/* Search + Date Filter */}
                <div className="flex items-center gap-2 mt-3">
                    <div className="relative flex-1">
                        <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                        <Input placeholder="Search by ride code, name, phone, address, or ID..."
                            value={search} onChange={e => onSearchChange(e.target.value)}
                            className="pl-9 h-9 text-sm bg-background" />
                    </div>
                    <div className="flex items-center gap-1.5 shrink-0">
                        <CalendarRange className="h-3.5 w-3.5 text-muted-foreground" />
                        <input type="date" value={dateFrom} onChange={e => onDateFromChange(e.target.value)}
                            aria-label="Start date"
                            className="text-xs border rounded-lg px-2 py-1.5 bg-background w-[120px]" />
                        <span className="text-xs text-muted-foreground">to</span>
                        <input type="date" value={dateTo} onChange={e => onDateToChange(e.target.value)}
                            aria-label="End date"
                            className="text-xs border rounded-lg px-2 py-1.5 bg-background w-[120px]" />
                        {(dateFrom || dateTo) && (
                            <button onClick={() => { onDateFromChange(""); onDateToChange(""); }}
                                className="p-1 text-muted-foreground hover:text-foreground"><X className="h-3.5 w-3.5" /></button>
                        )}
                    </div>
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
                                    <span className="text-[11px] font-semibold text-muted-foreground uppercase tracking-wider">
                                        Rider
                                    </span>
                                </th>
                                <th className="text-left py-2 px-4 hidden lg:table-cell">
                                    <span className="text-[11px] font-semibold text-muted-foreground uppercase tracking-wider">
                                        Driver
                                    </span>
                                </th>
                                <th className="text-right py-2 px-4">
                                    <button onClick={() => handleSort("total_fare")} className="flex items-center gap-1 text-[11px] font-semibold text-muted-foreground uppercase tracking-wider hover:text-foreground transition ml-auto">
                                        Fare <SortIcon col="total_fare" />
                                    </button>
                                </th>
                                <th className="text-right py-2 px-4 hidden md:table-cell">
                                    <span className="text-[11px] font-semibold text-muted-foreground uppercase tracking-wider">
                                        Distance
                                    </span>
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
                            {rides.map(ride => (
                                <tr key={ride.id} onClick={() => onSelect(ride)}
                                    className={`cursor-pointer transition-colors group ${
                                        selectedId === ride.id ? "bg-primary/5 hover:bg-primary/8" : "hover:bg-muted/40"
                                    }`}>
                                    <td className="py-3 px-5">{getStatusBadge(ride.status)}</td>
                                    <td className="py-3 px-4 max-w-[320px]">
                                        {ride.ride_code && (
                                            <p className="text-[11px] font-bold tracking-wide text-primary/80 mb-0.5">
                                                {ride.ride_code}
                                            </p>
                                        )}
                                        {ride.legacy_import_metadata && Object.keys(ride.legacy_import_metadata).length > 0 && (
                                            <span className="inline-block text-[10px] font-medium text-muted-foreground bg-muted rounded px-1.5 py-0.5 mb-0.5">
                                                Imported
                                            </span>
                                        )}
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
                                        {parseFloat(String(ride.tip_amount ?? 0)) > 0 && (
                                            <p className="text-[10px] font-semibold text-success mt-0.5">+{formatCurrency(ride.tip_amount)} tip</p>
                                        )}
                                    </td>
                                    <td className="py-3 px-4 text-right hidden md:table-cell tabular-nums">
                                        {(() => {
                                            const { toPickupKm, tripKm } = rideDistances(ride);
                                            const planned = ride.planned_distance_km != null ? Number(ride.planned_distance_km) : null;
                                            const actualSecs = ride.phase_durations?.trip_in_progress ?? null;
                                            const actualMin = actualSecs != null ? Math.round(actualSecs / 60) : null;
                                            const estMin = ride.duration_minutes ?? null;
                                            const Row = ({ k, label, accent }: { k: number | null; label: string; accent?: boolean }) => (
                                                <div className="flex items-center justify-end gap-1.5 leading-tight">
                                                    <span className={`text-[10px] uppercase tracking-wide ${accent ? "text-foreground/60 font-semibold" : "text-muted-foreground"}`}>
                                                        {label}
                                                    </span>
                                                    <span className={accent ? "text-sm font-bold whitespace-nowrap" : "text-xs whitespace-nowrap"}>
                                                        {k == null ? "—" : `${fmtKm(k)} km`}
                                                    </span>
                                                </div>
                                            );
                                            return (
                                                <div className="space-y-0.5">
                                                    <Row k={planned} label="Plan" />
                                                    <Row k={toPickupKm} label="Pickup" />
                                                    <Row k={tripKm} label="Trip" accent />
                                                    <div className="flex items-center justify-end gap-1.5 leading-tight mt-0.5 pt-0.5 border-t border-border/40">
                                                        <span className="text-[10px] uppercase tracking-wide text-success font-semibold">Time</span>
                                                        <span className="text-xs font-bold whitespace-nowrap text-success">
                                                            {actualMin != null ? `${actualMin} min` : estMin != null ? `~${estMin} min` : "—"}
                                                        </span>
                                                    </div>
                                                </div>
                                            );
                                        })()}
                                    </td>
                                    <td className="py-3 px-4 text-right hidden md:table-cell">
                                        {ride.is_scheduled && ride.scheduled_time ? (
                                            <>
                                                <p className="text-xs font-semibold text-primary whitespace-nowrap">
                                                    {fmtTime(ride.scheduled_time)}
                                                </p>
                                                <p className="text-[10px] text-muted-foreground whitespace-nowrap">
                                                    scheduled · booked {fmtTime(ride.created_at)}
                                                </p>
                                            </>
                                        ) : (
                                            <p className="text-xs text-muted-foreground whitespace-nowrap">{fmtTime(ride.created_at)}</p>
                                        )}
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
            <div className="flex items-center justify-between px-5 py-3.5 border-t bg-muted/20">
                <div className="flex items-center gap-3">
                    <button onClick={() => onPageChange(page - 1)} disabled={page === 0}
                        className="flex items-center gap-1.5 text-xs font-semibold px-3 py-1.5 rounded-lg border bg-background hover:bg-muted disabled:opacity-30 disabled:cursor-not-allowed transition">
                        <ChevronLeft className="h-3.5 w-3.5" /> Previous
                    </button>
                    {totalPages > 1 && (
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
                    )}
                    <button onClick={() => onPageChange(page + 1)} disabled={page >= totalPages - 1}
                        className="flex items-center gap-1.5 text-xs font-semibold px-3 py-1.5 rounded-lg border bg-background hover:bg-muted disabled:opacity-30 disabled:cursor-not-allowed transition">
                        Next <ChevronRight className="h-3.5 w-3.5" />
                    </button>
                </div>

                {/* Page Size Dropdown */}
                <div className="flex items-center gap-2">
                    <span className="text-xs text-muted-foreground">Show</span>
                    <select
                        value={pageSize}
                        onChange={e => onPageSizeChange(Number(e.target.value))}
                        aria-label="Rows per page"
                        className="text-xs font-semibold border rounded-lg px-2 py-1.5 bg-background text-foreground focus:outline-none focus:ring-2 focus:ring-primary/20 transition"
                    >
                        {pageSizes.map(s => (
                            <option key={s} value={s}>{s}</option>
                        ))}
                    </select>
                    <span className="text-xs text-muted-foreground">per page</span>
                </div>
            </div>
        </div>
    );
}
