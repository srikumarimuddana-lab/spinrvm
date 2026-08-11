"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { ListChecks, Loader2, RefreshCw } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
    cancelCompanyBooking,
    listCompanyBookings,
    CompanyBookingRow,
} from "@/lib/companyApi";
import { useCompanyAuthStore } from "@/store/companyAuthStore";

/**
 * Live company bookings — polls every 12s (v1; a socket feed is overkill for
 * a desk dashboard). Members see their own bookings; owner/admin see all
 * (enforced by the backend guard regardless of what this UI shows).
 */

const POLL_MS = 12_000;
const PRE_TRIP = new Set(["scheduled", "searching", "driver_assigned", "driver_accepted", "driver_arrived"]);

const STATUS_STYLES: Record<string, string> = {
    scheduled: "bg-sky-100 text-sky-800",
    searching: "bg-amber-100 text-amber-800",
    driver_assigned: "bg-indigo-100 text-indigo-800",
    driver_accepted: "bg-indigo-100 text-indigo-800",
    driver_arrived: "bg-violet-100 text-violet-800",
    in_progress: "bg-emerald-100 text-emerald-800",
    completed: "bg-emerald-50 text-emerald-700",
    cancelled: "bg-red-100 text-red-700",
};

function StatusBadge({ status }: { status: string }) {
    return (
        <Badge className={`${STATUS_STYLES[status] ?? "bg-muted text-foreground"} hover:bg-inherit`}>
            {status.replace(/_/g, " ")}
        </Badge>
    );
}

export default function CompanyBookingsPage() {
    const params = useParams();
    const companyId = typeof params?.id === "string" ? params.id : "";
    const memberships = useCompanyAuthStore((s) => s.memberships);
    const isCompanyAdmin = useMemo(() => {
        const m = memberships.find((p) => p.company.id === companyId);
        return m?.membership.role === "owner" || m?.membership.role === "admin";
    }, [memberships, companyId]);

    const [rows, setRows] = useState<CompanyBookingRow[]>([]);
    const [statusFilter, setStatusFilter] = useState("");
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [cancelling, setCancelling] = useState<string | null>(null);

    const load = useCallback(async () => {
        if (!companyId) return;
        try {
            const res = await listCompanyBookings(companyId, {
                status: statusFilter || undefined,
                limit: 100,
            });
            setRows(res.bookings);
            setError(null);
        } catch (e) {
            setError(e instanceof Error ? e.message : "Failed to load bookings");
        } finally {
            setLoading(false);
        }
    }, [companyId, statusFilter]);

    useEffect(() => {
        setLoading(true);
        load();
        const t = setInterval(load, POLL_MS);
        return () => clearInterval(t);
    }, [load]);

    const handleCancel = async (rideId: string) => {
        if (!window.confirm("Cancel this booking? The customer will be notified by text.")) return;
        setCancelling(rideId);
        try {
            await cancelCompanyBooking(companyId, rideId);
            await load();
        } catch (e) {
            setError(e instanceof Error ? e.message : "Cancel failed");
        } finally {
            setCancelling(null);
        }
    };

    return (
        <div className="space-y-4">
            <header className="flex flex-wrap items-center justify-between gap-3">
                <div>
                    <h1 className="flex items-center gap-2 text-xl font-semibold">
                        <ListChecks className="h-5 w-5" /> Bookings
                    </h1>
                    <p className="text-sm text-muted-foreground">
                        {isCompanyAdmin
                            ? "All rides booked by your company. Updates every few seconds."
                            : "Rides you booked for customers. Updates every few seconds."}
                    </p>
                </div>
                <div className="flex items-center gap-2">
                    <select
                        className="h-9 rounded-md border border-input bg-background px-3 text-sm"
                        value={statusFilter}
                        onChange={(e) => setStatusFilter(e.target.value)}
                    >
                        <option value="">All statuses</option>
                        <option value="scheduled">Scheduled</option>
                        <option value="searching">Finding driver</option>
                        <option value="driver_accepted">Driver on the way</option>
                        <option value="in_progress">In progress</option>
                        <option value="completed">Completed</option>
                        <option value="cancelled">Cancelled</option>
                    </select>
                    <Button variant="outline" size="sm" onClick={() => load()}>
                        <RefreshCw className="h-4 w-4" />
                    </Button>
                    <Button size="sm" asChild>
                        <Link href={`/company-portal/${companyId}/book`}>Book a ride</Link>
                    </Button>
                </div>
            </header>

            {error && <p className="rounded bg-destructive/10 p-3 text-sm text-destructive">{error}</p>}
            {loading && <p className="text-sm text-muted-foreground">Loading…</p>}

            <Card>
                <CardContent className="p-0">
                    <div className="overflow-x-auto">
                        <table className="w-full text-sm">
                            <thead>
                                <tr className="border-b bg-muted/40 text-left text-xs text-muted-foreground">
                                    <th className="px-4 py-2 font-medium">Customer</th>
                                    <th className="px-4 py-2 font-medium">Trip</th>
                                    <th className="px-4 py-2 font-medium">Status</th>
                                    <th className="px-4 py-2 font-medium">Fare</th>
                                    <th className="px-4 py-2 font-medium">Payment</th>
                                    <th className="px-4 py-2 font-medium">Booked</th>
                                    <th className="px-4 py-2" />
                                </tr>
                            </thead>
                            <tbody>
                                {rows.map((r) => (
                                    <tr key={r.ride_id} className="border-b last:border-0">
                                        <td className="px-4 py-3">
                                            <div className="font-medium">
                                                {r.customer_first_name ?? "Customer"}
                                            </div>
                                            <div className="text-xs text-muted-foreground">
                                                {r.ride_code ?? r.ride_id.slice(0, 8)}
                                            </div>
                                        </td>
                                        <td className="max-w-[280px] px-4 py-3">
                                            <div className="truncate text-xs">{r.pickup_address}</div>
                                            <div className="truncate text-xs text-muted-foreground">
                                                → {r.dropoff_address}
                                            </div>
                                        </td>
                                        <td className="px-4 py-3">
                                            <StatusBadge status={r.status} />
                                            {r.scheduled_time && r.status === "scheduled" && (
                                                <div className="mt-1 text-[11px] text-muted-foreground">
                                                    {new Date(r.scheduled_time).toLocaleString()}
                                                </div>
                                            )}
                                        </td>
                                        <td className="px-4 py-3">
                                            {r.grand_total != null ? `$${Number(r.grand_total).toFixed(2)}` : "—"}
                                        </td>
                                        <td className="px-4 py-3">
                                            <span className="text-xs text-muted-foreground">
                                                {r.payment_status ?? "—"}
                                            </span>
                                        </td>
                                        <td className="px-4 py-3 text-xs text-muted-foreground">
                                            {r.created_at ? new Date(r.created_at).toLocaleString() : "—"}
                                        </td>
                                        <td className="px-4 py-3 text-right">
                                            {PRE_TRIP.has(r.status) && (
                                                <Button
                                                    variant="outline"
                                                    size="sm"
                                                    disabled={cancelling === r.ride_id}
                                                    onClick={() => handleCancel(r.ride_id)}
                                                >
                                                    {cancelling === r.ride_id ? (
                                                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                                                    ) : (
                                                        "Cancel"
                                                    )}
                                                </Button>
                                            )}
                                        </td>
                                    </tr>
                                ))}
                                {!loading && rows.length === 0 && (
                                    <tr>
                                        <td
                                            colSpan={7}
                                            className="px-4 py-10 text-center text-sm text-muted-foreground"
                                        >
                                            No bookings yet.{" "}
                                            <Link
                                                href={`/company-portal/${companyId}/book`}
                                                className="text-primary underline"
                                            >
                                                Book the first ride
                                            </Link>
                                            .
                                        </td>
                                    </tr>
                                )}
                            </tbody>
                        </table>
                    </div>
                </CardContent>
            </Card>
        </div>
    );
}
