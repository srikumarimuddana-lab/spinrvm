// Rides tab of the drivers detail slideout. Pure code motion out of
// drivers/page.tsx (design-audit follow-up,
// docs/change-log/2026-09-04-breakup-drivers-page-god-component.md) — no
// logic changes.

import { useState, useEffect, useMemo } from "react";
import { exportToCsv } from "@/lib/export-csv";
import { Pagination } from "@/components/ui/pagination";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Search, CalendarRange, Car, ArrowUpDown, ArrowUp, ArrowDown, Copy, Download } from "lucide-react";
import { useFeatureFlag } from "@/hooks/useFeatureFlag";

// Categorical ride-status map (7 states: completed/in_progress/cancelled/
// driver_assigned/driver_accepted/driver_arrived/searching) — same class as
// lib/utils.ts's statusColor() and rides/_components/ride-ui-helpers.tsx's
// STATUS_CONFIG; not a #2816 migration target. A 3-token semantic system
// can't express 7 distinct lifecycle states.
/* eslint-disable no-restricted-syntax -- categorical ride-status map, see comment above (#2816) */
export const RIDE_STATUS_STYLE: Record<string, { bg: string; text: string; label: string }> = {
    completed:        { bg: "bg-emerald-100 dark:bg-emerald-900/30", text: "text-emerald-700 dark:text-emerald-300", label: "Completed" },
    in_progress:      { bg: "bg-blue-100 dark:bg-blue-900/30",   text: "text-blue-700 dark:text-blue-300",   label: "In Progress" },
    cancelled:        { bg: "bg-red-100 dark:bg-red-900/30",     text: "text-red-700 dark:text-red-300",     label: "Cancelled" },
    driver_assigned:  { bg: "bg-violet-100 dark:bg-violet-900/30", text: "text-violet-700 dark:text-violet-300", label: "Assigned" },
    driver_accepted:  { bg: "bg-violet-100 dark:bg-violet-900/30", text: "text-violet-700 dark:text-violet-300", label: "Accepted" },
    driver_arrived:   { bg: "bg-indigo-100 dark:bg-indigo-900/30", text: "text-indigo-700 dark:text-indigo-300", label: "Arrived" },
    searching:        { bg: "bg-amber-100 dark:bg-amber-900/30",  text: "text-amber-700 dark:text-amber-300",  label: "Searching" },
};
/* eslint-enable no-restricted-syntax */

// Quiet Console Stage 3: the flag-on Badge variant for each ride status
// above — a 3-token system still can't hold 7 distinct hues, so the
// pre-trip progression states (assigned/accepted/arrived/in_progress) share
// plain `outline` rather than getting invented shades. RIDE_STATUS_STYLE
// itself (and the flag-off rendering using it) is untouched.
export const RIDE_STATUS_BADGE_VARIANT: Record<string, "outline" | "outline-success" | "outline-warning" | "outline-destructive"> = {
    completed: "outline-success",
    in_progress: "outline",
    cancelled: "outline-destructive",
    driver_assigned: "outline",
    driver_accepted: "outline",
    driver_arrived: "outline",
    searching: "outline-warning",
};
export const DRIVER_RIDES_PAGE_SIZE_OPTIONS = [25, 50, 100] as const;

export type RidesSortKey = "created_at" | "rider_name" | "status" | "distance_km" | "duration_seconds" | "total_fare" | "tip_amount";
export function DriverRidesTab({ rides, totalCount, loading, driverName, fmtDate }: {
    rides: any[];
    totalCount?: number | null;
    loading: boolean;
    driverName: string;
    fmtDate: (d: string) => string;
}) {
    // Quiet Console Stage 3: gates the flag-on Badge alternate for the
    // ride-status pill below — RIDE_STATUS_STYLE stays the flag-off path.
    const themeV2Enabled = useFeatureFlag("admin_theme_v2_enabled");
    const [statusFilter, setStatusFilter] = useState<string>("all");
    const [search, setSearch] = useState("");
    const [sortKey, setSortKey] = useState<RidesSortKey>("created_at");
    const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");
    const [page, setPage] = useState(0);
    const [pageSize, setPageSize] = useState<number>(25);
    const [dateFrom, setDateFrom] = useState("");
    const [dateTo, setDateTo] = useState("");

    useEffect(() => { setPage(0); }, [statusFilter, search, sortKey, sortDir, dateFrom, dateTo, pageSize]);

    const fmtDuration = (s?: number) => {
        if (!s) return "—";
        const m = Math.round(s / 60);
        return m < 60 ? `${m}m` : `${Math.floor(m / 60)}h ${m % 60}m`;
    };

    const riderDisplay = (r: any) => {
        const name = (r.rider_name || "").trim();
        if (name) return name;
        if (r.rider_id) return `Rider ${String(r.rider_id).slice(0, 6)}`;
        return "Unknown rider";
    };

    const statusCounts = useMemo(() => {
        const c: Record<string, number> = { all: rides.length };
        for (const r of rides) {
            const s = r.status || "unknown";
            c[s] = (c[s] || 0) + 1;
        }
        return c;
    }, [rides]);

    const statusOptions = useMemo(() => {
        const seen = new Set<string>();
        for (const r of rides) seen.add(r.status || "unknown");
        return [
            { value: "all", label: "All statuses" },
            ...Array.from(seen).sort().map(s => ({
                value: s,
                label: RIDE_STATUS_STYLE[s]?.label || s,
            })),
        ];
    }, [rides]);

    const processed = useMemo(() => {
        const q = search.trim().toLowerCase();
        let out = rides;
        if (statusFilter !== "all") out = out.filter(r => r.status === statusFilter);
        if (dateFrom) {
            const from = new Date(dateFrom);
            from.setHours(0, 0, 0, 0);
            out = out.filter(r => r.created_at && new Date(r.created_at) >= from);
        }
        if (dateTo) {
            const to = new Date(dateTo);
            to.setHours(23, 59, 59, 999);
            out = out.filter(r => r.created_at && new Date(r.created_at) <= to);
        }
        if (q) out = out.filter(r => {
            const haystack = `${riderDisplay(r)} ${r.id || ""} ${r.pickup_address || ""} ${r.dropoff_address || ""}`.toLowerCase();
            return haystack.includes(q);
        });
        const sorted = [...out].sort((a, b) => {
            let av: any, bv: any;
            if (sortKey === "rider_name") { av = riderDisplay(a).toLowerCase(); bv = riderDisplay(b).toLowerCase(); }
            else if (sortKey === "status") { av = a.status || ""; bv = b.status || ""; }
            else if (sortKey === "total_fare") { av = Number(a.total_fare ?? a.fare_amount ?? a.base_fare ?? 0); bv = Number(b.total_fare ?? b.fare_amount ?? b.base_fare ?? 0); }
            else if (sortKey === "tip_amount") { av = Number(a.tip_amount ?? 0); bv = Number(b.tip_amount ?? 0); }
            else if (sortKey === "distance_km") { av = Number(a.distance_km ?? 0); bv = Number(b.distance_km ?? 0); }
            else if (sortKey === "duration_seconds") { av = Number(a.duration_seconds ?? 0); bv = Number(b.duration_seconds ?? 0); }
            else { av = a.created_at || ""; bv = b.created_at || ""; }
            if (av < bv) return sortDir === "asc" ? -1 : 1;
            if (av > bv) return sortDir === "asc" ? 1 : -1;
            return 0;
        });
        return sorted;
    }, [rides, statusFilter, search, sortKey, sortDir, dateFrom, dateTo]);

    const paged = processed.slice(page * pageSize, (page + 1) * pageSize);
    const hasNextPage = processed.length > (page + 1) * pageSize;

    const handleSort = (k: RidesSortKey) => {
        if (sortKey === k) setSortDir(d => d === "asc" ? "desc" : "asc");
        else {
            setSortKey(k);
            setSortDir(k === "rider_name" ? "asc" : "desc");
        }
    };

    const handleExportRides = () => {
        if (processed.length === 0) return;
        const cols = [
            { key: "ride_code", label: "Ride Code" },
            { label: "Date", value: (r: any) => r.created_at ? new Date(r.created_at).toLocaleString() : "" },
            { label: "Rider", value: (r: any) => riderDisplay(r) },
            { label: "Driver", value: () => driverName },
            { key: "pickup_address", label: "Pickup" },
            { key: "dropoff_address", label: "Dropoff" },
            { key: "status", label: "Status" },
            { label: "Distance (km)", value: (r: any) => r.distance_km != null ? Number(r.distance_km).toFixed(1) : "" },
            { label: "Duration (min)", value: (r: any) => r.duration_seconds ? Math.round(r.duration_seconds / 60) : "" },
            { label: "Tip", value: (r: any) => r.tip_amount != null && Number(r.tip_amount) > 0 ? Number(r.tip_amount).toFixed(2) : "" },
            { label: "Fare", value: (r: any) => { const f = r.total_fare ?? r.fare_amount ?? r.base_fare; return f != null ? Number(f).toFixed(2) : ""; } },
        ];
        const safeName = driverName.replace(/[^a-zA-Z0-9]/g, "_").toLowerCase();
        exportToCsv(`driver_rides_${safeName}`, processed, cols);
    };

    const SortIcon = ({ col }: { col: RidesSortKey }) => {
        if (sortKey !== col) return <ArrowUpDown className="h-3 w-3 opacity-30 inline ml-1" />;
        return sortDir === "asc" ? <ArrowUp className="h-3 w-3 inline ml-1" /> : <ArrowDown className="h-3 w-3 inline ml-1" />;
    };

    if (loading) return (
        <div className="space-y-2.5 animate-pulse">
            <div className="h-9 w-full rounded-lg bg-muted" />
            {Array.from({ length: 8 }).map((_, i) => (
                <div key={i} className="h-12 rounded-lg bg-muted" />
            ))}
        </div>
    );

    if (rides.length === 0) return (
        <div className="py-16 text-center text-muted-foreground">
            <Car className="h-10 w-10 mx-auto mb-3 opacity-30" />
            <p className="text-sm font-medium">No rides yet</p>
            <p className="text-xs mt-1">{driverName} has not completed any trips.</p>
        </div>
    );

    return (
        <div className="space-y-3">
            {/* This tab fetches up to 500 rides in one call (getDriverRides);
                totalCount (count_documents(), independent of that cap) tells
                us when even that wasn't enough, so filtering/sorting below is
                silently operating on a partial set (A30 Finding 2,
                docs/audit/2026-08-13-migrated-data-visibility-audit.md). */}
            {typeof totalCount === "number" && totalCount > rides.length && (
                <p className="text-xs text-warning">
                    Showing the {rides.length} most recent of {totalCount} total rides. Use the date filters below to find older ones.
                </p>
            )}
            <div className="flex items-center gap-2 flex-wrap">
                <div className="flex items-center gap-1.5">
                    <Search className="h-4 w-4 text-muted-foreground" />
                    <Input
                        value={search}
                        onChange={(e) => setSearch(e.target.value)}
                        placeholder="Search rider, ride id, address"
                        className="h-8 text-xs w-[260px]"
                    />
                </div>
                <Select value={statusFilter} onValueChange={setStatusFilter}>
                    <SelectTrigger className="h-8 text-xs w-[170px]">
                        <SelectValue placeholder="Status" />
                    </SelectTrigger>
                    <SelectContent>
                        {statusOptions.map((opt: { value: string; label: string }) => (
                            <SelectItem key={opt.value} value={opt.value} className="text-xs">
                                {opt.label}{opt.value !== "all" && statusCounts[opt.value] != null ? ` · ${statusCounts[opt.value]}` : opt.value === "all" ? ` · ${statusCounts.all}` : ""}
                            </SelectItem>
                        ))}
                    </SelectContent>
                </Select>
                <div className="flex items-center gap-1.5">
                    <CalendarRange className="h-4 w-4 text-muted-foreground" />
                    <Input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} className="h-8 text-xs w-[130px]" />
                    <span className="text-xs text-muted-foreground">to</span>
                    <Input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} className="h-8 text-xs w-[130px]" />
                </div>
                <span className="ml-auto text-xs text-muted-foreground tabular-nums">
                    {processed.length} of {rides.length}
                </span>
                <Button variant="outline" size="sm" className="h-8 text-xs gap-1.5" onClick={handleExportRides} disabled={processed.length === 0}>
                    <Download className="h-3.5 w-3.5" />
                    Export CSV
                </Button>
            </div>

            <div className="rounded-xl border border-border overflow-x-auto">
                <Table>
                    <TableHeader>
                        <TableRow className="bg-muted/30 hover:bg-muted/30">
                            <TableHead className="h-9 text-[11px] uppercase tracking-wider cursor-pointer select-none" onClick={() => handleSort("created_at")}>
                                Date<SortIcon col="created_at" />
                            </TableHead>
                            <TableHead className="h-9 text-[11px] uppercase tracking-wider cursor-pointer select-none" onClick={() => handleSort("rider_name")}>
                                Rider<SortIcon col="rider_name" />
                            </TableHead>
                            <TableHead className="h-9 text-[11px] uppercase tracking-wider">Driver</TableHead>
                            <TableHead className="h-9 text-[11px] uppercase tracking-wider">Route</TableHead>
                            <TableHead className="h-9 text-[11px] uppercase tracking-wider cursor-pointer select-none" onClick={() => handleSort("status")}>
                                Status<SortIcon col="status" />
                            </TableHead>
                            <TableHead className="h-9 text-[11px] uppercase tracking-wider text-right cursor-pointer select-none" onClick={() => handleSort("distance_km")}>
                                Distance<SortIcon col="distance_km" />
                            </TableHead>
                            <TableHead className="h-9 text-[11px] uppercase tracking-wider text-right cursor-pointer select-none" onClick={() => handleSort("duration_seconds")}>
                                Duration<SortIcon col="duration_seconds" />
                            </TableHead>
                            <TableHead className="h-9 text-[11px] uppercase tracking-wider text-right cursor-pointer select-none" onClick={() => handleSort("tip_amount")}>
                                Tip<SortIcon col="tip_amount" />
                            </TableHead>
                            <TableHead className="h-9 text-[11px] uppercase tracking-wider text-right cursor-pointer select-none" onClick={() => handleSort("total_fare")}>
                                Fare<SortIcon col="total_fare" />
                            </TableHead>
                            <TableHead className="h-9 text-[11px] uppercase tracking-wider">Ride</TableHead>
                        </TableRow>
                    </TableHeader>
                    <TableBody>
                        {paged.length === 0 ? (
                            <TableRow>
                                <TableCell colSpan={10} className="text-center text-muted-foreground py-12">
                                    <p className="text-sm">No rides match this filter.</p>
                                </TableCell>
                            </TableRow>
                        ) : paged.map((r: any) => {
                            const style = RIDE_STATUS_STYLE[r.status] ?? { bg: "bg-muted/30", text: "text-muted-foreground", label: r.status };
                            const totalFare = r.total_fare ?? r.fare_amount ?? r.base_fare;
                            const tip = r.tip_amount;
                            const hasTip = tip != null && Number(tip) > 0;
                            const dt = r.created_at ? new Date(r.created_at) : null;
                            const rider = riderDisplay(r);
                            return (
                                <TableRow key={r.id} className="hover:bg-muted/20">
                                    <TableCell className="text-xs tabular-nums whitespace-nowrap">
                                        {dt ? (
                                            <>
                                                <div>{fmtDate(r.created_at)}</div>
                                                <div className="text-[10px] text-muted-foreground">{dt.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })}</div>
                                            </>
                                        ) : "—"}
                                    </TableCell>
                                    <TableCell className="text-xs">
                                        <span className="font-medium truncate block max-w-[150px]" title={rider}>{rider}</span>
                                    </TableCell>
                                    <TableCell className="text-xs text-muted-foreground truncate max-w-[150px]" title={driverName}>{driverName}</TableCell>
                                    <TableCell className="text-xs">
                                        <div className="max-w-[220px]">
                                            <p className="truncate text-foreground" title={r.pickup_address}>{r.pickup_address || "—"}</p>
                                            <p className="truncate text-muted-foreground text-[10px]" title={r.dropoff_address}>{r.dropoff_address ? `→ ${r.dropoff_address}` : ""}</p>
                                        </div>
                                    </TableCell>
                                    <TableCell>
                                        {themeV2Enabled ? (
                                            <Badge variant={RIDE_STATUS_BADGE_VARIANT[r.status] ?? "outline"} className="text-[10px]">{style.label}</Badge>
                                        ) : (
                                            <span className={`inline-flex items-center px-2 py-0.5 rounded-md text-[10px] font-semibold ${style.bg} ${style.text}`}>
                                                {style.label}
                                            </span>
                                        )}
                                        {r.legacy_import_metadata && Object.keys(r.legacy_import_metadata).length > 0 && (
                                            <span className="ml-1 inline-flex items-center px-1.5 py-0.5 rounded-md text-[10px] font-medium bg-muted text-muted-foreground">
                                                Imported
                                            </span>
                                        )}
                                    </TableCell>
                                    <TableCell className="text-xs text-right tabular-nums">
                                        {r.distance_km != null ? `${Number(r.distance_km).toFixed(1)} km` : "—"}
                                    </TableCell>
                                    <TableCell className="text-xs text-right tabular-nums">
                                        {fmtDuration(r.duration_seconds)}
                                    </TableCell>
                                    <TableCell className="text-xs text-right tabular-nums">
                                        {hasTip ? <span className="text-success font-medium">${Number(tip).toFixed(2)}</span> : <span className="text-muted-foreground">—</span>}
                                    </TableCell>
                                    <TableCell className="text-sm text-right font-semibold tabular-nums">
                                        {totalFare != null ? `$${Number(totalFare).toFixed(2)}` : "—"}
                                    </TableCell>
                                    <TableCell>
                                        {(() => {
                                            // Prefer the human-readable ride_code (SPR-XXXXXX,
                                            // canonical short identifier — see migration 40).
                                            // Fall back to a UUID prefix only for rides predating
                                            // the backfill, which shouldn't happen in practice.
                                            const code = r.ride_code ? String(r.ride_code).toLowerCase() : `#${String(r.id).slice(0, 8)}`;
                                            const copyTarget = r.ride_code || r.id;
                                            return (
                                                <button
                                                    type="button"
                                                    onClick={() => navigator.clipboard.writeText(copyTarget)}
                                                    title={`Click to copy ${copyTarget}\nFull ID: ${r.id}`}
                                                    className="inline-flex items-center gap-1 text-[10px] font-mono text-muted-foreground hover:text-foreground transition-colors"
                                                >
                                                    {code}
                                                    <Copy className="h-2.5 w-2.5" />
                                                </button>
                                            );
                                        })()}
                                    </TableCell>
                                </TableRow>
                            );
                        })}
                    </TableBody>
                </Table>
            </div>

            <div className="flex items-center justify-between gap-3 flex-wrap">
                <div className="flex items-center gap-2">
                    <span className="text-xs text-muted-foreground">Show</span>
                    <Select value={String(pageSize)} onValueChange={(v) => setPageSize(Number(v))}>
                        <SelectTrigger className="h-8 text-xs w-[70px]">
                            <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                            {DRIVER_RIDES_PAGE_SIZE_OPTIONS.map(n => (
                                <SelectItem key={n} value={String(n)} className="text-xs">{n}</SelectItem>
                            ))}
                        </SelectContent>
                    </Select>
                    <span className="text-xs text-muted-foreground">per page</span>
                </div>
                {processed.length > pageSize && (
                    <Pagination
                        page={page}
                        pageSize={pageSize}
                        hasNextPage={hasNextPage}
                        totalCount={processed.length}
                        onPageChange={setPage}
                    />
                )}
            </div>
        </div>
    );
}
