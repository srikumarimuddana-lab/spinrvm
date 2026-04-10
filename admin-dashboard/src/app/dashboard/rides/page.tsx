"use client";

import { useEffect, useState, useCallback } from "react";
import { getRides, getServiceAreas } from "@/lib/api";
import RideStatsCards, { RidesChart } from "./_components/ride-stats-cards";
import RideList from "./_components/ride-list";
import RideDetailModal from "./_components/ride-detail-modal";

const PAGE_SIZE = 50;

export default function RidesPage() {
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

    const loadRides = useCallback(async (p: number) => {
        setLoading(true);
        try {
            const res = await getRides(PAGE_SIZE, p * PAGE_SIZE);
            setRides(res.rides);
            setTotalCount(res.total_count);
        } catch {}
        finally { setLoading(false); }
    }, []);

    useEffect(() => {
        Promise.all([loadRides(0), getServiceAreas().catch(() => [])])
            .then(([_, a]) => { if (a) setAreas(a as any); })
            .catch(() => {});
    }, [loadRides]);

    const handlePageChange = (newPage: number) => {
        setPage(newPage);
        loadRides(newPage);
    };

    const filtered = rides.filter(r => {
        const q = search.toLowerCase();
        const matchSearch = !search ||
            r.pickup_address?.toLowerCase().includes(q) ||
            r.dropoff_address?.toLowerCase().includes(q) ||
            r.id?.toLowerCase().includes(q) ||
            r.rider_name?.toLowerCase().includes(q) ||
            r.rider_phone?.toLowerCase().includes(q) ||
            r.driver_name?.toLowerCase().includes(q) ||
            r.driver_phone?.toLowerCase().includes(q) ||
            r.rider_id?.toLowerCase().includes(q) ||
            r.driver_id?.toLowerCase().includes(q);
        const matchStatus = statusFilter === "all" || r.status === statusFilter;
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

    return (
        <div className="space-y-6 pb-8">
            {/* Page Header */}
            <div>
                <h1 className="text-3xl font-bold tracking-tight">Rides</h1>
                <p className="text-muted-foreground mt-1">
                    Monitor and manage all ride activity across your platform.
                </p>
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
                onStatusChange={setStatusFilter}
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
        </div>
    );
}
