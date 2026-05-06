"use client";

import { useEffect, useState, useCallback } from "react";
import { getRides, getServiceAreas } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { PlusCircle } from "lucide-react";
import RideStatsCards, { RidesChart } from "./_components/ride-stats-cards";
import RideList from "./_components/ride-list";
import RideDetailModal from "./_components/ride-detail-modal";
import { CreateRideModal } from "./_components/create-ride-modal";
import { useRequireModule } from "@/hooks/useRequireModule";

const PAGE_SIZE = 50;

export default function RidesPage() {
    const { allowed } = useRequireModule("rides");
    const [rides, setRides] = useState<any[]>([]);
    const [totalCount, setTotalCount] = useState(0);
    const [page, setPage] = useState(0);
    const [areas, setAreas] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [search, setSearch] = useState("");
    const [statusFilter, setStatusFilter] = useState("all");
    const [areaFilter, setAreaFilter] = useState("all");
    const [dateFrom, setDateFrom] = useState("");
    const [dateTo, setDateTo] = useState("");
    const [selectedRideId, setSelectedRideId] = useState<string | null>(null);
    const [isCreateModalOpen, setIsCreateModalOpen] = useState(false);

    // Scheduled rides live as status="searching" + is_scheduled=true until
    // their scheduled_time, so they are not on the default feed — re-query
    // the backend when the operator picks the Scheduled tab. Every other
    // tab keeps the original client-side status filter (so per-tab counts
    // work from a single loaded batch).
    const loadRides = useCallback(async (p: number, tab: string) => {
        setLoading(true);
        try {
            const opts = tab === "scheduled" ? { isScheduled: true } : undefined;
            const res = await getRides(PAGE_SIZE, p * PAGE_SIZE, opts);
            setRides(res.rides);
            setTotalCount(res.total_count);
        } catch {}
        finally { setLoading(false); }
    }, []);

    useEffect(() => {
        Promise.all([loadRides(0, statusFilter), getServiceAreas().catch(() => [])])
            .then(([, a]) => { if (a) setAreas(a as any); })
            .catch(() => {});
    }, [loadRides, statusFilter]);

    const handlePageChange = (newPage: number) => {
        setPage(newPage);
        loadRides(newPage, statusFilter);
    };

    const handleStatusChange = (tab: string) => {
        setStatusFilter(tab);
        setPage(0);
    };

    const filtered = rides.filter(r => {
        const q = search.toLowerCase();
        const matchSearch = !search ||
            r.pickup_address?.toLowerCase().includes(q) ||
            r.dropoff_address?.toLowerCase().includes(q) ||
            r.id?.toLowerCase().includes(q) ||
            r.ride_code?.toLowerCase().includes(q) ||
            r.rider_name?.toLowerCase().includes(q) ||
            r.rider_phone?.toLowerCase().includes(q) ||
            r.driver_name?.toLowerCase().includes(q) ||
            r.driver_phone?.toLowerCase().includes(q) ||
            r.rider_id?.toLowerCase().includes(q) ||
            r.driver_id?.toLowerCase().includes(q);
        // "scheduled" narrows to rides the rider explicitly scheduled
        // (is_scheduled=true). Even though the backend is also called
        // with is_scheduled=true for this tab, we narrow client-side too
        // so a stale feed or an older row with is_scheduled=null never
        // leaks a non-scheduled ride into the Scheduled tab.
        //
        // "no_driver_found" is backed by status=cancelled on the wire;
        // narrow client-side to the ones auto-cancelled by the
        // dispatcher timeout (cancellation_type or legacy reason text).
        const matchStatus =
            statusFilter === "all" ||
            (statusFilter === "scheduled" && r.is_scheduled === true) ||
            (statusFilter === "no_driver_found" &&
                r.status === "cancelled" &&
                (r.cancellation_type === "no_drivers_found" ||
                    (r.cancellation_reason || "").toLowerCase().includes("no nearby drivers"))) ||
            (statusFilter !== "scheduled" &&
                statusFilter !== "no_driver_found" &&
                statusFilter !== "all" &&
                r.status === statusFilter);
        const matchArea = areaFilter === "all" || r.service_area_id === areaFilter;
        let matchDate = true;
        if (dateFrom || dateTo) {
            const d = r.created_at ? new Date(r.created_at).toISOString().split("T")[0] : "";
            if (dateFrom && d < dateFrom) matchDate = false;
            if (dateTo && d > dateTo) matchDate = false;
        }
        return matchSearch && matchStatus && matchArea && matchDate;
    });

    const totalPages = Math.ceil(totalCount / PAGE_SIZE);

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

            {/* Rides Table */}
            <RideList
                rides={filtered}
                allRides={rides}
                totalCount={totalCount}
                areas={areas}
                loading={loading}
                selectedId={selectedRideId || undefined}
                search={search}
                onSearchChange={setSearch}
                statusFilter={statusFilter}
                onStatusChange={handleStatusChange}
                areaFilter={areaFilter}
                onAreaChange={setAreaFilter}
                dateFrom={dateFrom}
                onDateFromChange={setDateFrom}
                dateTo={dateTo}
                onDateToChange={setDateTo}
                onSelect={(ride) => setSelectedRideId(ride.id)}
                page={page}
                totalPages={totalPages}
                onPageChange={handlePageChange}
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
                onSuccess={() => loadRides(0, statusFilter)}
            />
        </div>
    );
}
