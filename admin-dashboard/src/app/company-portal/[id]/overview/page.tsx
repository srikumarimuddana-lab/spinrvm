"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import {
    Activity,
    CalendarClock,
    CalendarPlus,
    CheckCircle2,
    ChevronDown,
    ChevronUp,
    ClipboardList,
    Loader2,
    Search,
    Wallet,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
    BookingDetail,
    CompanyBookingRow,
    DashboardStats,
    getBookingDetail,
    getDashboardStats,
    listCompanyBookings,
} from "@/lib/companyApi";

/**
 * Role-scoped company dashboard (Phase 6 / M6.2) — the landing page for
 * every role. Scope is resolved SERVER-SIDE: admins see the whole company,
 * section heads their department, members their own work rides (booked by
 * or for them). Stat groups per the owner's spec, switchable across
 * Today / This week / This month / Overall.
 */

type Win = "today" | "week" | "month" | "overall";

const WINDOWS: Array<{ key: Win; label: string }> = [
    { key: "today", label: "Today" },
    { key: "week", label: "This week" },
    { key: "month", label: "This month" },
    { key: "overall", label: "Overall" },
];

const STATUS_COLORS: Record<string, string> = {
    completed: "bg-emerald-100 text-emerald-800",
    in_progress: "bg-blue-100 text-blue-800",
    searching: "bg-amber-100 text-amber-800",
    scheduled: "bg-purple-100 text-purple-800",
    cancelled: "bg-red-100 text-red-700",
};

function formatCAD(v: number | string | undefined | null) {
    return Number(v ?? 0).toLocaleString("en-CA", { style: "currency", currency: "CAD" });
}

export default function OverviewPage() {
    const { id } = useParams<{ id: string }>();
    const router = useRouter();

    const [stats, setStats] = useState<DashboardStats | null>(null);
    const [bookings, setBookings] = useState<CompanyBookingRow[]>([]);
    const [win, setWin] = useState<Win>("month");
    const [query, setQuery] = useState("");
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [openRide, setOpenRide] = useState<string | null>(null);
    const [detail, setDetail] = useState<BookingDetail | null>(null);
    const [detailBusy, setDetailBusy] = useState(false);

    const load = useCallback(async () => {
        if (!id) return;
        try {
            const [st, rows] = await Promise.all([
                getDashboardStats(id),
                listCompanyBookings(id, { limit: 50 }).catch(() => ({ bookings: [], skip: 0, limit: 50 })),
            ]);
            setStats(st);
            setBookings(rows.bookings ?? []);
            setError(null);
        } catch (e) {
            setError(e instanceof Error ? e.message : "Failed to load dashboard");
        } finally {
            setLoading(false);
        }
    }, [id]);

    useEffect(() => {
        load();
        const t = setInterval(load, 30_000);
        return () => clearInterval(t);
    }, [load]);

    // Search by booking id / code or customer name (owner spec).
    const filtered = useMemo(() => {
        const q = query.trim().toLowerCase();
        if (!q) return bookings;
        return bookings.filter(
            (b) =>
                (b.ride_code ?? "").toLowerCase().includes(q) ||
                b.ride_id.toLowerCase().includes(q) ||
                (b.customer_first_name ?? "").toLowerCase().includes(q)
        );
    }, [bookings, query]);

    const toggleDetail = async (rideId: string) => {
        if (openRide === rideId) {
            setOpenRide(null);
            setDetail(null);
            return;
        }
        setOpenRide(rideId);
        setDetail(null);
        setDetailBusy(true);
        try {
            setDetail(await getBookingDetail(id, rideId));
        } catch {
            /* detail unavailable — the row itself still shows the basics */
        } finally {
            setDetailBusy(false);
        }
    };

    const g = stats?.stats;
    const tiles = [
        { label: "Total bookings", icon: ClipboardList, value: g?.total_bookings?.[win] ?? "—" },
        { label: "Expenses", icon: Wallet, value: g ? formatCAD(g.expenses?.[win]) : "—" },
        { label: "Active rides", icon: Activity, value: g?.active_rides?.[win] ?? "—" },
        { label: "Pending bookings", icon: CalendarClock, value: g?.pending_bookings?.[win] ?? "—" },
        { label: "Completed rides", icon: CheckCircle2, value: g?.completed_rides?.[win] ?? "—" },
    ];

    return (
        <div className="space-y-6">
            <header className="flex flex-wrap items-center justify-between gap-3">
                <div>
                    <h1 className="text-2xl font-semibold">Dashboard</h1>
                    <p className="text-sm text-muted-foreground">
                        {stats?.scope === "company"
                            ? "Company-wide activity and spend."
                            : "Your work rides — booked by you or for you."}
                    </p>
                </div>
                <Button onClick={() => router.push(`/company-portal/${id}/book`)}>
                    <CalendarPlus className="mr-2 h-4 w-4" /> Create new ride
                </Button>
            </header>

            <div className="flex gap-2">
                {WINDOWS.map((w) => (
                    <Button
                        key={w.key}
                        size="sm"
                        variant={win === w.key ? "default" : "outline"}
                        onClick={() => setWin(w.key)}
                    >
                        {w.label}
                    </Button>
                ))}
            </div>

            {error && <p className="rounded bg-red-50 p-3 text-sm text-red-700">{error}</p>}

            <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
                {tiles.map((t) => {
                    const Icon = t.icon;
                    return (
                        <Card key={t.label}>
                            <CardContent className="p-4">
                                <div className="flex items-center gap-2 text-xs text-muted-foreground">
                                    <Icon className="h-3.5 w-3.5" /> {t.label}
                                </div>
                                <div className="mt-1 text-2xl font-semibold">
                                    {loading && !stats ? (
                                        <Loader2 className="h-5 w-5 animate-spin" />
                                    ) : (
                                        t.value
                                    )}
                                </div>
                            </CardContent>
                        </Card>
                    );
                })}
            </div>

            <Card>
                <CardContent className="space-y-3 p-4">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                        <h2 className="text-sm font-semibold">Recent rides</h2>
                        <div className="relative">
                            <Search className="absolute left-2 top-2.5 h-4 w-4 text-muted-foreground" />
                            <Input
                                className="h-9 w-64 pl-8"
                                placeholder="Search booking # or customer"
                                value={query}
                                onChange={(e) => setQuery(e.target.value)}
                            />
                        </div>
                    </div>

                    <div className="space-y-2">
                        {filtered.map((b) => (
                            <div key={b.ride_id} className="rounded-md border">
                                <button
                                    type="button"
                                    className="flex w-full items-center justify-between gap-3 p-3 text-left"
                                    onClick={() => toggleDetail(b.ride_id)}
                                >
                                    <div className="min-w-0">
                                        <span className="font-mono text-xs text-muted-foreground">
                                            {b.ride_code ?? b.ride_id.slice(0, 8)}
                                        </span>
                                        <span className="ml-2 text-sm font-medium">
                                            {b.customer_first_name ?? "Team member"}
                                        </span>
                                        <div className="truncate text-xs text-muted-foreground">
                                            {b.pickup_address} → {b.dropoff_address}
                                        </div>
                                    </div>
                                    <div className="flex shrink-0 items-center gap-2">
                                        {b.grand_total != null && (
                                            <span className="text-sm">{formatCAD(b.grand_total)}</span>
                                        )}
                                        <Badge className={STATUS_COLORS[b.status] ?? "bg-muted"}>
                                            {b.status}
                                        </Badge>
                                        {openRide === b.ride_id ? (
                                            <ChevronUp className="h-4 w-4" />
                                        ) : (
                                            <ChevronDown className="h-4 w-4" />
                                        )}
                                    </div>
                                </button>

                                {openRide === b.ride_id && (
                                    <div className="border-t bg-muted/30 p-3 text-sm">
                                        {detailBusy && (
                                            <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
                                        )}
                                        {detail && (
                                            <div className="grid gap-3 md:grid-cols-3">
                                                <div>
                                                    <div className="text-xs font-semibold uppercase text-muted-foreground">
                                                        Timeline
                                                    </div>
                                                    {detail.timeline.map((t) => (
                                                        <div key={t.event} className="text-xs">
                                                            {t.event.replace(/_/g, " ")} —{" "}
                                                            {new Date(t.at).toLocaleString("en-CA")}
                                                        </div>
                                                    ))}
                                                </div>
                                                <div>
                                                    <div className="text-xs font-semibold uppercase text-muted-foreground">
                                                        Fare
                                                    </div>
                                                    <div className="text-xs">
                                                        Total: {formatCAD(detail.fare?.grand_total as number)}
                                                    </div>
                                                    {detail.fare?.tax_breakdown &&
                                                        Object.entries(detail.fare.tax_breakdown).map(([k, v]) => (
                                                            <div key={k} className="text-xs uppercase">
                                                                {k}: {formatCAD(v)}
                                                            </div>
                                                        ))}
                                                    {detail.payment_source && (
                                                        <div className="mt-1 text-xs text-muted-foreground">
                                                            Paid: allowance {formatCAD(detail.payment_source.allowance)}
                                                            {Number(detail.payment_source.section ?? 0) > 0 &&
                                                                ` · dept ${formatCAD(detail.payment_source.section)}`}
                                                            {Number(detail.payment_source.master ?? 0) > 0 &&
                                                                ` · master ${formatCAD(detail.payment_source.master)}`}
                                                        </div>
                                                    )}
                                                </div>
                                                <div>
                                                    <div className="text-xs font-semibold uppercase text-muted-foreground">
                                                        Driver
                                                    </div>
                                                    {detail.driver ? (
                                                        <div className="text-xs">
                                                            {detail.driver.first_name} —{" "}
                                                            {detail.driver.vehicle_make} {detail.driver.vehicle_model} (
                                                            {detail.driver.license_plate})
                                                        </div>
                                                    ) : (
                                                        <div className="text-xs text-muted-foreground">
                                                            Not assigned yet
                                                        </div>
                                                    )}
                                                </div>
                                            </div>
                                        )}
                                    </div>
                                )}
                            </div>
                        ))}
                        {!loading && filtered.length === 0 && (
                            <p className="py-6 text-center text-sm text-muted-foreground">
                                {query ? "No rides match your search." : "No rides yet."}
                            </p>
                        )}
                    </div>
                </CardContent>
            </Card>
        </div>
    );
}
