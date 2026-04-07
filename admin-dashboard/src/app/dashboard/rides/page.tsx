"use client";

import { useEffect, useState } from "react";
import { getRides, getServiceAreas } from "@/lib/api";
import RideStatsCards from "./_components/ride-stats-cards";
import RideList from "./_components/ride-list";
import RideDetailModal from "./_components/ride-detail-modal";

export default function RidesPage() {
    const [rides, setRides] = useState<any[]>([]);
    const [areas, setAreas] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [search, setSearch] = useState("");
    const [statusFilter, setStatusFilter] = useState("all");
    const [areaFilter, setAreaFilter] = useState("all");
    const [selectedRideId, setSelectedRideId] = useState<string | null>(null);

    useEffect(() => {
        Promise.all([getRides(), getServiceAreas().catch(() => [])])
            .then(([r, a]) => { setRides(r); setAreas(a); })
            .catch(() => {})
            .finally(() => setLoading(false));
    }, []);

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
        return matchSearch && matchStatus && matchArea;
    });

    return (
        <div className="space-y-0">
            <RideStatsCards />
            <div className="h-[calc(100vh-240px)]">
                <RideList
                    rides={filtered}
                    allRides={rides}
                    areas={areas}
                    loading={loading}
                    selectedId={selectedRideId || undefined}
                    search={search}
                    onSearchChange={setSearch}
                    statusFilter={statusFilter}
                    onStatusChange={setStatusFilter}
                    areaFilter={areaFilter}
                    onAreaChange={setAreaFilter}
                    onSelect={(ride) => setSelectedRideId(ride.id)}
                />
            </div>
            <RideDetailModal
                rideId={selectedRideId}
                open={!!selectedRideId}
                onClose={() => setSelectedRideId(null)}
            />
        </div>
    );
}
