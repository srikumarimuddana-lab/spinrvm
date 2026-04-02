"use client";

import { useEffect, useState } from "react";
import { getRides } from "@/lib/api";
import { exportToCsv } from "@/lib/export-csv";
import { formatCurrency, formatDate, statusColor } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import {
    Car, Search, Clock, CheckCircle, XCircle, MapPin, Loader,
    CalendarClock, Download, ChevronRight, X, DollarSign, User, Route,
} from "lucide-react";

const STATUS_TABS = [
    { value: "all", label: "All", icon: Car },
    { value: "searching", label: "Searching", icon: Loader },
    { value: "driver_assigned", label: "Assigned", icon: MapPin },
    { value: "in_progress", label: "In Progress", icon: Clock },
    { value: "completed", label: "Completed", icon: CheckCircle },
    { value: "cancelled", label: "Cancelled", icon: XCircle },
];

export default function RidesPage() {
    const [rides, setRides] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [search, setSearch] = useState("");
    const [statusFilter, setStatusFilter] = useState("all");
    const [selectedRide, setSelectedRide] = useState<any>(null);

    useEffect(() => {
        getRides().then(setRides).catch(() => {}).finally(() => setLoading(false));
    }, []);

    const filtered = rides.filter((r) => {
        const matchSearch = !search ||
            r.pickup_address?.toLowerCase().includes(search.toLowerCase()) ||
            r.dropoff_address?.toLowerCase().includes(search.toLowerCase()) ||
            r.id?.toLowerCase().includes(search.toLowerCase());
        const matchStatus = statusFilter === "all" || r.status === statusFilter;
        return matchSearch && matchStatus;
    });

    const statusCounts = (s: string) => s === "all" ? rides.length : rides.filter(r => r.status === s).length;

    const getStatusBadge = (status: string) => {
        const map: Record<string, { bg: string; text: string }> = {
            completed: { bg: "bg-emerald-100 dark:bg-emerald-900/30", text: "text-emerald-700 dark:text-emerald-400" },
            cancelled: { bg: "bg-red-100 dark:bg-red-900/30", text: "text-red-600 dark:text-red-400" },
            in_progress: { bg: "bg-blue-100 dark:bg-blue-900/30", text: "text-blue-700 dark:text-blue-400" },
            searching: { bg: "bg-amber-100 dark:bg-amber-900/30", text: "text-amber-700 dark:text-amber-400" },
            driver_assigned: { bg: "bg-violet-100 dark:bg-violet-900/30", text: "text-violet-700 dark:text-violet-400" },
            driver_arrived: { bg: "bg-teal-100 dark:bg-teal-900/30", text: "text-teal-700 dark:text-teal-400" },
        };
        const c = map[status] || { bg: "bg-gray-100", text: "text-gray-600" };
        return <span className={`px-2 py-0.5 rounded-md text-xs font-bold ${c.bg} ${c.text}`}>{status?.replace(/_/g, " ").toUpperCase()}</span>;
    };

    const fmtTime = (d: string) => {
        if (!d) return "—";
        try { return new Date(d).toLocaleString("en-CA", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }); } catch { return d; }
    };

    return (
        <div className="flex gap-4 h-[calc(100vh-100px)]">
            {/* Left: Ride List */}
            <div className={`flex flex-col ${selectedRide ? "w-1/2 lg:w-3/5" : "w-full"} transition-all`}>
                {/* Header */}
                <div className="flex items-center justify-between mb-4">
                    <div>
                        <h1 className="text-2xl font-bold">Rides</h1>
                        <p className="text-sm text-muted-foreground">{rides.length} total rides</p>
                    </div>
                    <button onClick={() => exportToCsv("rides", filtered, ["id","pickup_address","dropoff_address","status","total_fare","created_at"])}
                        className="flex items-center gap-2 text-sm font-medium text-muted-foreground hover:text-foreground transition px-3 py-1.5 rounded-lg hover:bg-muted">
                        <Download className="h-4 w-4" /> Export
                    </button>
                </div>

                {/* Status Tabs */}
                <div className="flex gap-1.5 mb-3 overflow-x-auto pb-1">
                    {STATUS_TABS.map(tab => (
                        <button key={tab.value} onClick={() => setStatusFilter(tab.value)}
                            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold whitespace-nowrap transition ${
                                statusFilter === tab.value
                                    ? "bg-primary text-white" : "bg-muted text-muted-foreground hover:bg-muted/80"
                            }`}>
                            <tab.icon className="h-3.5 w-3.5" />
                            {tab.label}
                            <span className={`ml-1 px-1.5 py-0 rounded text-[10px] ${statusFilter === tab.value ? "bg-white/20" : "bg-background"}`}>
                                {statusCounts(tab.value)}
                            </span>
                        </button>
                    ))}
                </div>

                {/* Search */}
                <div className="relative mb-3">
                    <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                    <Input placeholder="Search by address or ride ID..." value={search} onChange={e => setSearch(e.target.value)} className="pl-9 h-9 text-sm" />
                </div>

                {/* Ride Cards */}
                <div className="flex-1 overflow-y-auto space-y-2 pr-1">
                    {loading ? (
                        <div className="flex items-center justify-center py-20">
                            <div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" />
                        </div>
                    ) : filtered.length === 0 ? (
                        <div className="text-center py-16 text-muted-foreground">
                            <Car className="h-10 w-10 mx-auto mb-3 opacity-30" />
                            <p className="font-medium">No rides found</p>
                        </div>
                    ) : (
                        filtered.map(ride => (
                            <div key={ride.id}
                                onClick={() => setSelectedRide(ride)}
                                className={`flex items-center gap-3 p-3 rounded-xl border cursor-pointer transition-all hover:shadow-sm ${
                                    selectedRide?.id === ride.id ? "border-primary bg-primary/5" : "border-border hover:border-border/80"
                                }`}>
                                <div className="shrink-0">{getStatusBadge(ride.status)}</div>
                                <div className="flex-1 min-w-0">
                                    <p className="text-sm font-medium truncate">{ride.pickup_address || "Unknown"}</p>
                                    <p className="text-xs text-muted-foreground truncate">→ {ride.dropoff_address || "Unknown"}</p>
                                </div>
                                <div className="text-right shrink-0">
                                    <p className="text-sm font-bold">{formatCurrency(ride.total_fare || 0)}</p>
                                    <p className="text-[10px] text-muted-foreground">{fmtTime(ride.created_at)}</p>
                                </div>
                                <ChevronRight className="h-4 w-4 text-muted-foreground/40 shrink-0" />
                            </div>
                        ))
                    )}
                </div>
            </div>

            {/* Right: Ride Detail Panel */}
            {selectedRide && (
                <div className="w-1/2 lg:w-2/5 bg-card border rounded-2xl overflow-y-auto">
                    {/* Detail Header */}
                    <div className="flex items-center justify-between p-4 border-b sticky top-0 bg-card z-10">
                        <div>
                            <p className="text-xs text-muted-foreground font-mono">{selectedRide.id}</p>
                            <div className="mt-1">{getStatusBadge(selectedRide.status)}</div>
                        </div>
                        <button onClick={() => setSelectedRide(null)} className="p-1.5 hover:bg-muted rounded-lg">
                            <X className="h-4 w-4" />
                        </button>
                    </div>

                    <div className="p-4 space-y-4">
                        {/* Route */}
                        <div className="bg-muted/30 rounded-xl p-4">
                            <div className="flex gap-3">
                                <div className="flex flex-col items-center pt-1">
                                    <div className="w-2.5 h-2.5 rounded-full bg-emerald-500" />
                                    <div className="w-0.5 flex-1 bg-border my-1" />
                                    <div className="w-2.5 h-2.5 rounded-full bg-primary" />
                                </div>
                                <div className="flex-1">
                                    <p className="text-[10px] text-muted-foreground uppercase font-bold tracking-wider">Pickup</p>
                                    <p className="text-sm font-medium">{selectedRide.pickup_address || "—"}</p>
                                    <div className="h-4" />
                                    <p className="text-[10px] text-muted-foreground uppercase font-bold tracking-wider">Dropoff</p>
                                    <p className="text-sm font-medium">{selectedRide.dropoff_address || "—"}</p>
                                </div>
                            </div>
                        </div>

                        {/* Fare Breakdown */}
                        <div>
                            <h4 className="text-xs font-bold text-muted-foreground uppercase tracking-wider mb-2">Fare</h4>
                            <div className="bg-muted/30 rounded-xl p-4 space-y-1.5">
                                <Row label="Base fare" value={`$${(selectedRide.base_fare || 0).toFixed(2)}`} />
                                <Row label={`Distance (${(selectedRide.distance_km || 0).toFixed(1)} km)`} value={`$${(selectedRide.distance_fare || 0).toFixed(2)}`} />
                                <Row label={`Time (${selectedRide.duration_minutes || 0} min)`} value={`$${(selectedRide.time_fare || 0).toFixed(2)}`} />
                                <Row label="Booking fee" value={`$${(selectedRide.booking_fee || 0).toFixed(2)}`} />
                                {(selectedRide.tip_amount || 0) > 0 && <Row label="Tip" value={`$${selectedRide.tip_amount.toFixed(2)}`} highlight />}
                                <div className="border-t my-2" />
                                <div className="flex justify-between">
                                    <span className="text-sm font-bold">Total</span>
                                    <span className="text-lg font-extrabold text-primary">{formatCurrency(selectedRide.total_fare || 0)}</span>
                                </div>
                            </div>
                        </div>

                        {/* Payment */}
                        <div>
                            <h4 className="text-xs font-bold text-muted-foreground uppercase tracking-wider mb-2">Payment</h4>
                            <div className="bg-muted/30 rounded-xl p-3 flex items-center gap-3">
                                <DollarSign className="h-5 w-5 text-muted-foreground" />
                                <div className="flex-1">
                                    <p className="text-sm font-medium">{selectedRide.payment_method || "Card"}</p>
                                    <p className="text-xs text-muted-foreground">{selectedRide.payment_status || "pending"}</p>
                                </div>
                                {selectedRide.payment_status === "paid" && <CheckCircle className="h-4 w-4 text-emerald-500" />}
                            </div>
                        </div>

                        {/* Driver & Rider */}
                        <div className="grid grid-cols-2 gap-3">
                            <div>
                                <h4 className="text-xs font-bold text-muted-foreground uppercase tracking-wider mb-2">Rider</h4>
                                <div className="bg-muted/30 rounded-xl p-3">
                                    <p className="text-sm font-medium">{selectedRide.rider_id?.slice(0, 8) || "—"}...</p>
                                    <p className="text-xs text-muted-foreground mt-1">
                                        Rating: {selectedRide.rider_rating || "—"}
                                    </p>
                                </div>
                            </div>
                            <div>
                                <h4 className="text-xs font-bold text-muted-foreground uppercase tracking-wider mb-2">Driver</h4>
                                <div className="bg-muted/30 rounded-xl p-3">
                                    <p className="text-sm font-medium">{selectedRide.driver_id?.slice(0, 8) || "Not assigned"}...</p>
                                    <p className="text-xs text-muted-foreground mt-1">
                                        Earnings: {formatCurrency(selectedRide.driver_earnings || 0)}
                                    </p>
                                </div>
                            </div>
                        </div>

                        {/* Timeline */}
                        <div>
                            <h4 className="text-xs font-bold text-muted-foreground uppercase tracking-wider mb-2">Timeline</h4>
                            <div className="bg-muted/30 rounded-xl p-4 space-y-2.5">
                                <TimelineItem label="Requested" time={selectedRide.ride_requested_at || selectedRide.created_at} />
                                <TimelineItem label="Driver accepted" time={selectedRide.driver_accepted_at} />
                                <TimelineItem label="Driver arrived" time={selectedRide.driver_arrived_at} />
                                <TimelineItem label="Ride started" time={selectedRide.ride_started_at} />
                                <TimelineItem label="Ride completed" time={selectedRide.ride_completed_at} />
                                {selectedRide.cancelled_at && <TimelineItem label="Cancelled" time={selectedRide.cancelled_at} danger />}
                            </div>
                        </div>

                        {/* Meta */}
                        <div className="text-xs text-muted-foreground space-y-1 pt-2 border-t">
                            <p>Vehicle type: {selectedRide.vehicle_type_id?.slice(0, 8) || "—"}</p>
                            <p>OTP: {selectedRide.pickup_otp || "—"}</p>
                            {selectedRide.rider_comment && <p>Comment: "{selectedRide.rider_comment}"</p>}
                            {selectedRide.cancellation_reason && <p>Cancel reason: {selectedRide.cancellation_reason}</p>}
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}

function Row({ label, value, highlight }: { label: string; value: string; highlight?: boolean }) {
    return (
        <div className="flex justify-between text-sm">
            <span className={highlight ? "text-emerald-600" : "text-muted-foreground"}>{label}</span>
            <span className={highlight ? "text-emerald-600 font-semibold" : "font-medium"}>{value}</span>
        </div>
    );
}

function TimelineItem({ label, time, danger }: { label: string; time?: string; danger?: boolean }) {
    if (!time) return null;
    return (
        <div className="flex items-center gap-2.5">
            <div className={`w-2 h-2 rounded-full shrink-0 ${danger ? "bg-red-400" : "bg-emerald-400"}`} />
            <p className="text-sm flex-1">{label}</p>
            <p className="text-xs text-muted-foreground">{new Date(time).toLocaleString("en-CA", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}</p>
        </div>
    );
}
