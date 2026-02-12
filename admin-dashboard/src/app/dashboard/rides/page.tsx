"use client";

import { useEffect, useState } from "react";
import { getRides } from "@/lib/api";
import { exportToCsv } from "@/lib/export-csv";
import { formatCurrency, formatDate, statusColor } from "@/lib/utils";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from "@/components/ui/table";
import { Input } from "@/components/ui/input";
import { Car, Search, Clock, CheckCircle, XCircle, MapPin, Loader, CalendarClock, Download } from "lucide-react";

const STATUS_TABS = [
    { value: "all", label: "All", icon: Car },
    { value: "searching", label: "Searching", icon: Loader },
    { value: "driver_assigned", label: "Assigned", icon: MapPin },
    { value: "in_progress", label: "In Progress", icon: Clock },
    { value: "completed", label: "Completed", icon: CheckCircle },
    { value: "cancelled", label: "Cancelled", icon: XCircle },
    { value: "scheduled", label: "Scheduled", icon: CalendarClock },
];

export default function RidesPage() {
    const [rides, setRides] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [search, setSearch] = useState("");
    const [statusFilter, setStatusFilter] = useState("all");
    const [dateFrom, setDateFrom] = useState("");
    const [dateTo, setDateTo] = useState("");

    useEffect(() => {
        getRides()
            .then(setRides)
            .catch(() => { })
            .finally(() => setLoading(false));
    }, []);

    const filtered = rides.filter((r) => {
        const matchSearch =
            !search ||
            r.pickup_address?.toLowerCase().includes(search.toLowerCase()) ||
            r.dropoff_address?.toLowerCase().includes(search.toLowerCase()) ||
            r.id?.toLowerCase().includes(search.toLowerCase());
        const matchStatus = statusFilter === "all" || r.status === statusFilter;
        let matchDate = true;
        if (dateFrom || dateTo) {
            const d = r.created_at ? new Date(r.created_at).toISOString().split("T")[0] : "";
            if (dateFrom && d < dateFrom) matchDate = false;
            if (dateTo && d > dateTo) matchDate = false;
        }
        return matchSearch && matchStatus && matchDate;
    });

    const getCount = (status: string) =>
        status === "all" ? rides.length : rides.filter((r) => r.status === status).length;

    return (
        <div className="space-y-6">
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-3xl font-bold tracking-tight">Rides</h1>
                    <p className="text-muted-foreground mt-1">
                        View and manage all rides on the platform.
                    </p>
                </div>
                <div className="flex items-center gap-2">
                    <Input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} className="w-36 text-xs" />
                    <span className="text-muted-foreground text-sm">to</span>
                    <Input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} className="w-36 text-xs" />
                    <Button
                        variant="outline"
                        onClick={() => exportToCsv("rides", filtered, [
                            { key: "id", label: "Ride ID" },
                            { key: "pickup_address", label: "Pickup" },
                            { key: "dropoff_address", label: "Dropoff" },
                            { key: "status", label: "Status" },
                            { key: "total_fare", label: "Total Fare" },
                            { key: "driver_earnings", label: "Driver Earnings" },
                            { key: "admin_earnings", label: "Platform Revenue" },
                            { key: "airport_fee", label: "Airport Fee" },
                            { key: "distance_km", label: "Distance (km)" },
                            { key: "created_at", label: "Date" },
                        ])}
                        disabled={filtered.length === 0}
                    >
                        <Download className="mr-2 h-4 w-4" /> Export CSV
                    </Button>
                </div>
            </div>

            {/* Status tabs */}
            <div className="flex flex-wrap gap-2">
                {STATUS_TABS.map((tab) => {
                    const count = getCount(tab.value);
                    const active = statusFilter === tab.value;
                    return (
                        <button
                            key={tab.value}
                            onClick={() => setStatusFilter(tab.value)}
                            className={`
                                inline-flex items-center gap-2 rounded-lg border px-3 py-2 text-sm font-medium transition-all
                                ${active
                                    ? "border-primary bg-primary/10 text-primary shadow-sm"
                                    : "border-border bg-card text-muted-foreground hover:bg-muted hover:text-foreground"
                                }
                            `}
                        >
                            <tab.icon className="h-4 w-4" />
                            {tab.label}
                            <Badge variant="secondary" className={`text-xs px-1.5 py-0 ${active ? "bg-primary/20" : ""}`}>
                                {count}
                            </Badge>
                        </button>
                    );
                })}
            </div>

            <div className="relative max-w-xs">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                <Input
                    placeholder="Search by address or ID..."
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                    className="pl-9"
                />
            </div>

            <Card className="border-border/50">
                <CardContent className="p-0">
                    {loading ? (
                        <div className="flex items-center justify-center p-12">
                            <div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" />
                        </div>
                    ) : (
                        <Table>
                            <TableHeader>
                                <TableRow>
                                    <TableHead>ID</TableHead>
                                    <TableHead>Pickup</TableHead>
                                    <TableHead>Dropoff</TableHead>
                                    <TableHead>Status</TableHead>
                                    <TableHead>Fare</TableHead>
                                    <TableHead>Date</TableHead>
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                {filtered.length === 0 ? (
                                    <TableRow>
                                        <TableCell colSpan={6} className="text-center text-muted-foreground py-12">
                                            No rides found.
                                        </TableCell>
                                    </TableRow>
                                ) : (
                                    filtered.map((ride) => (
                                        <TableRow key={ride.id} className="cursor-pointer hover:bg-muted/50">
                                            <TableCell className="font-mono text-xs">
                                                {ride.id?.slice(0, 8)}...
                                            </TableCell>
                                            <TableCell className="max-w-[200px] truncate">
                                                {ride.pickup_address || "—"}
                                            </TableCell>
                                            <TableCell className="max-w-[200px] truncate">
                                                {ride.dropoff_address || "—"}
                                            </TableCell>
                                            <TableCell>
                                                <Badge variant="secondary" className={statusColor(ride.status)}>
                                                    {ride.status?.replace(/_/g, " ")}
                                                </Badge>
                                            </TableCell>
                                            <TableCell>{formatCurrency(ride.total_fare || 0)}</TableCell>
                                            <TableCell className="text-xs text-muted-foreground">
                                                {formatDate(ride.created_at)}
                                            </TableCell>
                                        </TableRow>
                                    ))
                                )}
                            </TableBody>
                        </Table>
                    )}
                </CardContent>
            </Card>
        </div>
    );
}
