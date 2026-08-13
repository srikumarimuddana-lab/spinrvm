"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import { getRides, exportRides, getServiceAreas, type RideListOpts } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { PlusCircle } from "lucide-react";
import RideStatsCards, { RidesChart } from "./_components/ride-stats-cards";
import RideList from "./_components/ride-list";
import RideDetailModal from "./_components/ride-detail-modal";
import { CreateRideModal } from "./_components/create-ride-modal";
import { useRequireModule } from "@/hooks/useRequireModule";

const PAGE_SIZES = [25, 50, 100] as const;
type PageSize = (typeof PAGE_SIZES)[number];

export default function RidesPage() {
    const { allowed } = useRequireModule("rides");
    const [rides, setRides] = useState<any[]>([]);
    const [totalCount, setTotalCount] = useState(0);
    const [page, setPage] = useState(0);
    const [pageSize, setPageSize] = useState<PageSize>(25);
    const [areas, setAreas] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [loadError, setLoadError] = useState(false);
    // Lazy initializer (not a useEffect) so the very first `loadRides` call
    // on mount already includes a deep-linked ?search=<term> -- e.g. "view
    // all rides" from a rider detail panel whose "Recent rides" list is
    // capped at 10 (A30 Finding 2,
    // docs/audit/2026-08-13-migrated-data-visibility-audit.md). A useEffect
    // here would run after the mount-effect below has already fired with
    // the stale empty-string search.
    const [search, setSearch] = useState(() =>
        typeof window === "undefined" ? "" : new URLSearchParams(window.location.search).get("search") || ""
    );
    const [statusFilter, setStatusFilter] = useState("all");
    const [areaFilter, setAreaFilter] = useState("all");
    const [dateFrom, setDateFrom] = useState("");
    const [dateTo, setDateTo] = useState("");
    const [sortBy, setSortBy] = useState<string>("created_at");
    const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");
    const [selectedRideId, setSelectedRideId] = useState<string | null>(null);
    const [isCreateModalOpen, setIsCreateModalOpen] = useState(false);

    const searchTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

    // Deep-link support: open a specific ride when arriving via ?id=<rideId>
    // (e.g. from a user's "Recent rides" or the safety queue). Read once on
    // mount from window.location rather than useSearchParams() — the latter
    // requires a <Suspense> boundary (which this route lacks) and fails
    // `next build`. Mount-only also avoids re-opening the modal after close.
    // (?search=<term> is handled by the `search` state's lazy initializer
    // above, not here — it needs to be in place before the mount-effect
    // below fires its first `loadRides` call.)
    useEffect(() => {
        if (typeof window === "undefined") return;
        const id = new URLSearchParams(window.location.search).get("id");
        if (id) setSelectedRideId(id);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    const loadRides = useCallback(async (
        p: number,
        size: number,
        opts: {
            tab: string;
            search?: string;
            dateFrom?: string;
            dateTo?: string;
            areaFilter?: string;
            sortBy?: string;
            sortDir?: "asc" | "desc";
        },
    ) => {
        setLoading(true);
        setLoadError(false);
        try {
            const apiOpts: RideListOpts = {};
            if (opts.tab === "scheduled") {
                apiOpts.isScheduled = true;
            } else if (opts.tab !== "all" && opts.tab !== "no_driver_found") {
                apiOpts.status = opts.tab;
            }
            if (opts.search) apiOpts.search = opts.search;
            if (opts.dateFrom) apiOpts.dateFrom = opts.dateFrom;
            if (opts.dateTo) apiOpts.dateTo = opts.dateTo;
            if (opts.areaFilter && opts.areaFilter !== "all") apiOpts.serviceAreaId = opts.areaFilter;
            if (opts.sortBy) apiOpts.sortBy = opts.sortBy;
            if (opts.sortDir) apiOpts.sortDir = opts.sortDir;

            const res = await getRides(size, p * size, apiOpts);
            setRides(res.rides);
            setTotalCount(res.total_count);
        } catch {
            setLoadError(true);
        } finally {
            setLoading(false);
        }
    }, []);

    const currentOpts = useCallback(() => ({
        tab: statusFilter,
        search,
        dateFrom,
        dateTo,
        areaFilter,
        sortBy,
        sortDir,
    }), [statusFilter, search, dateFrom, dateTo, areaFilter, sortBy, sortDir]);

    useEffect(() => {
        Promise.all([
            loadRides(0, pageSize, currentOpts()),
            getServiceAreas().catch(() => []),
        ])
            .then(([, a]) => { setAreas(Array.isArray(a) ? a : []); })
            .catch(() => {});
        // Only run on mount
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    const reload = useCallback((p: number, size: number) => {
        loadRides(p, size, {
            tab: statusFilter,
            search,
            dateFrom,
            dateTo,
            areaFilter,
            sortBy,
            sortDir,
        });
    }, [loadRides, statusFilter, search, dateFrom, dateTo, areaFilter, sortBy, sortDir]);

    const handlePageChange = (newPage: number) => {
        setPage(newPage);
        reload(newPage, pageSize);
    };

    const handlePageSizeChange = (newSize: PageSize) => {
        setPageSize(newSize);
        setPage(0);
        reload(0, newSize);
    };

    const handleStatusChange = (tab: string) => {
        setStatusFilter(tab);
        setPage(0);
        loadRides(0, pageSize, { tab, search, dateFrom, dateTo, areaFilter, sortBy, sortDir });
    };

    const handleSearchChange = (value: string) => {
        setSearch(value);
        if (searchTimerRef.current) clearTimeout(searchTimerRef.current);
        searchTimerRef.current = setTimeout(() => {
            setPage(0);
            loadRides(0, pageSize, {
                tab: statusFilter,
                search: value,
                dateFrom,
                dateTo,
                areaFilter,
                sortBy,
                sortDir,
            });
        }, 400);
    };

    const handleAreaChange = (value: string) => {
        setAreaFilter(value);
        setPage(0);
        loadRides(0, pageSize, { tab: statusFilter, search, dateFrom, dateTo, areaFilter: value, sortBy, sortDir });
    };

    const handleDateFromChange = (value: string) => {
        setDateFrom(value);
        setPage(0);
        loadRides(0, pageSize, { tab: statusFilter, search, dateFrom: value, dateTo, areaFilter, sortBy, sortDir });
    };

    const handleDateToChange = (value: string) => {
        setDateTo(value);
        setPage(0);
        loadRides(0, pageSize, { tab: statusFilter, search, dateFrom, dateTo: value, areaFilter, sortBy, sortDir });
    };

    const handleSortChange = (key: string, dir: "asc" | "desc") => {
        setSortBy(key);
        setSortDir(dir);
        setPage(0);
        loadRides(0, pageSize, { tab: statusFilter, search, dateFrom, dateTo, areaFilter, sortBy: key, sortDir: dir });
    };

    const handleExport = async (): Promise<any[]> => {
        const opts: RideListOpts = {};
        if (statusFilter === "scheduled") {
            opts.isScheduled = true;
        } else if (statusFilter !== "all" && statusFilter !== "no_driver_found") {
            opts.status = statusFilter;
        }
        if (search) opts.search = search;
        if (dateFrom) opts.dateFrom = dateFrom;
        if (dateTo) opts.dateTo = dateTo;
        if (areaFilter && areaFilter !== "all") opts.serviceAreaId = areaFilter;
        if (sortBy) opts.sortBy = sortBy;
        if (sortDir) opts.sortDir = sortDir;
        const res = await exportRides(opts);
        return res.rides;
    };

    const totalPages = Math.ceil(totalCount / pageSize);

    if (!allowed) return null;

    return (
        <div className="space-y-6 pb-8">
            {/* Page Header */}
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-3xl font-bold tracking-tight">Rides</h1>
                    <p className="text-muted-foreground mt-1">
                        Monitor and manage all ride activity across your platform.
                    </p>
                </div>
                <Button onClick={() => setIsCreateModalOpen(true)} className="gap-2">
                    <PlusCircle className="h-4 w-4" />
                    Create Ride
                </Button>
            </div>

            {/* Stats Overview */}
            <RideStatsCards />

            {/* Load error banner */}
            {loadError && !loading && (
                <div className="rounded-md border border-red-200 dark:border-red-800 bg-red-50 dark:bg-red-900/10 px-4 py-3 text-sm text-red-700 dark:text-red-300 flex items-center justify-between">
                    <span>Failed to load rides — check backend health.</span>
                    <button
                        onClick={() => reload(page, pageSize)}
                        className="ml-4 text-red-700 dark:text-red-300 underline hover:no-underline text-sm"
                    >
                        Retry
                    </button>
                </div>
            )}

            {/* Rides Table */}
            <RideList
                rides={rides}
                totalCount={totalCount}
                areas={areas}
                loading={loading}
                selectedId={selectedRideId || undefined}
                search={search}
                onSearchChange={handleSearchChange}
                statusFilter={statusFilter}
                onStatusChange={handleStatusChange}
                areaFilter={areaFilter}
                onAreaChange={handleAreaChange}
                dateFrom={dateFrom}
                onDateFromChange={handleDateFromChange}
                dateTo={dateTo}
                onDateToChange={handleDateToChange}
                onSelect={(ride) => setSelectedRideId(ride.id)}
                page={page}
                pageSize={pageSize}
                pageSizes={PAGE_SIZES}
                onPageSizeChange={handlePageSizeChange}
                totalPages={totalPages}
                onPageChange={handlePageChange}
                sortBy={sortBy}
                sortDir={sortDir}
                onExport={handleExport}
                onSortChange={handleSortChange}
            />

            {/* Trends Chart */}
            <RidesChart />

            <RideDetailModal
                rideId={selectedRideId}
                open={!!selectedRideId}
                onClose={() => setSelectedRideId(null)}
            />

            <CreateRideModal
                open={isCreateModalOpen}
                onClose={() => setIsCreateModalOpen(false)}
                onSuccess={() => reload(0, pageSize)}
            />
        </div>
    );
}
