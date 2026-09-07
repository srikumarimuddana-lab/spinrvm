"use client";

import { useEffect, useState } from "react";
import { Download, Clock, Wallet, CheckCircle, AlertTriangle, Undo2 } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
    Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";
import { useTableSort, SortableHead } from "@/components/ui/sortable-table";
import { useToast } from "@/components/ui/use-toast";
import { exportToCsv } from "@/lib/export-csv";
import { formatDate } from "@/lib/utils";
import {
    getPayouts, getPayoutStats, getPayoutsOverview, getServiceAreas, retryPayout,
    bulkRetryPayouts, type EarningsPeriod, type PayoutsOverview,
} from "@/lib/api";
import { PayoutsCeoHeader } from "./payouts-ceo-header";
import { PayoutsCompliance } from "./payouts-compliance";

export function PayoutsTab() {
    const [payouts, setPayouts] = useState<any[]>([]);
    const [stats, setStats] = useState<any>(null);
    const [loading, setLoading] = useState(true);
    const [statusFilter, setStatusFilter] = useState("all");
    const [period, setPeriod] = useState<EarningsPeriod>("7d");
    // Independent from the earnings tab's area filter — operators may
    // want to view payouts fleet-wide while looking at earnings for
    // one area, so each tab tracks its own selection.
    const [serviceAreaId, setServiceAreaId] = useState<string>("all");
    const [serviceAreas, setServiceAreas] = useState<Array<{ id: string; name?: string }>>([]);
    const [overview, setOverview] = useState<PayoutsOverview | null>(null);
    const [overviewLoading, setOverviewLoading] = useState(true);
    const [retryingId, setRetryingId] = useState<string | null>(null);
    const [bulkRetrying, setBulkRetrying] = useState(false);
    const { toast } = useToast();

    useEffect(() => {
        // Service areas list is small and only feeds the dropdown —
        // fetch once on mount, no need to refetch per period change.
        getServiceAreas().then((rows) => setServiceAreas(Array.isArray(rows) ? rows : [])).catch(() => {});
    }, []);

    const refreshAll = async () => {
        setLoading(true);
        setOverviewLoading(true);
        try {
            const [p, s, o] = await Promise.all([
                getPayouts().catch(() => []),
                getPayoutStats().catch(() => null),
                getPayoutsOverview({
                    period,
                    service_area_id: serviceAreaId !== "all" ? serviceAreaId : undefined,
                }).catch(() => null),
            ]);
            setPayouts(Array.isArray(p) ? p : []);
            setStats(s);
            if (o) setOverview(o);
        } finally {
            setLoading(false);
            setOverviewLoading(false);
        }
    };

    const handleRowRetry = async (id: string) => {
        setRetryingId(id);
        try {
            await retryPayout(id);
            toast({ title: "Retry queued", description: "Payout flipped back to pending — the retry loop will pick it up." });
            await refreshAll();
        } catch (e: any) {
            toast({ title: "Retry failed", description: e?.message || "Unknown error", variant: "destructive" });
        } finally {
            setRetryingId(null);
        }
    };

    const handleBulkRetry = async () => {
        if (!overview) return;
        // Use the window the operator is already looking at. since=start
        // matches the Pass 1 daily chart so "retry everything failed in
        // this window" reads as a single coherent action.
        const since = overview.period.start;
        if (!window.confirm(
            `Retry every failed/cancelled payout since ${new Date(since).toLocaleString()}? Each will be flipped to pending and the retry loop will pick them up.`
        )) return;
        setBulkRetrying(true);
        try {
            const res = await bulkRetryPayouts({ since, max_to_retry: 200 });
            toast({
                title: "Bulk retry queued",
                description: `${res.retried} queued · ${res.skipped} skipped · ${res.failed_to_initiate} errored.`,
            });
            await refreshAll();
        } catch (e: any) {
            toast({ title: "Bulk retry failed", description: e?.message || "Unknown error", variant: "destructive" });
        } finally {
            setBulkRetrying(false);
        }
    };

    const handleExportCsv = () => {
        exportToCsv("payouts", payouts, [
            { key: "id", label: "Payout ID" },
            { key: "driver_id", label: "Driver ID" },
            { key: "driver_name", label: "Driver" },
            { key: "amount", label: "Amount" },
            { key: "status", label: "Status" },
            { key: "bank_name", label: "Bank" },
            { key: "account_last4", label: "Account Last 4" },
            { key: "stripe_payout_id", label: "Stripe Payout ID" },
            { key: "error_message", label: "Error" },
            { key: "created_at", label: "Requested" },
            { key: "processed_at", label: "Settled" },
        ]);
    };

    useEffect(() => {
        Promise.all([
            getPayouts().catch(() => []),
            getPayoutStats().catch(() => null),
        ]).then(([p, s]) => {
            setPayouts(Array.isArray(p) ? p : []);
            setStats(s);
        }).finally(() => setLoading(false));
    }, []);

    useEffect(() => {
        setOverviewLoading(true);
        getPayoutsOverview({
            period,
            service_area_id: serviceAreaId !== "all" ? serviceAreaId : undefined,
        })
            .then(setOverview)
            .catch((e) => console.error('[PayoutsOverview] load failed:', e))
            .finally(() => setOverviewLoading(false));
    }, [period, serviceAreaId]);

    const filtered = statusFilter === "all" ? payouts : payouts.filter(p => p.status === statusFilter);

    // Client-side sort of the already status-filtered payout list.
    const { sorted: sortedFiltered, sort: payoutsSort, toggle: payoutsToggle } = useTableSort<any>(filtered);

    const statusBadge = (s: string) => {
        if (s === "completed") return "bg-success/15 text-success";
        if (s === "pending") return "bg-warning/15 text-warning";
        if (s === "failed") return "bg-destructive/15 text-destructive dark:text-[#ff453a]";
        return "bg-muted text-muted-foreground";
    };

    return (
        <div className="space-y-6">
            {/* Pass 1 — CEO header. Sits above the legacy stat cards
                so the page opens to "is payout flow healthy?" instead
                of a static transaction log. Skeleton-loads independently
                so it doesn't block the rest of the page. */}
            <PayoutsCeoHeader
                overview={overview}
                loading={overviewLoading}
                period={period}
                onPeriodChange={setPeriod}
                serviceAreaId={serviceAreaId}
                onServiceAreaChange={setServiceAreaId}
                serviceAreas={serviceAreas}
            />

            {/* Legacy stats — kept for backward compatibility while the
                Pass 1 header takes over the headline role. Will retire
                once Pass 2 lands. */}
            {loading ? (
                <div className="grid grid-cols-2 md:grid-cols-4 gap-4 animate-pulse">
                    {[0, 1, 2, 3].map((i) => <div key={i} className="h-20 rounded-xl bg-muted" />)}
                </div>
            ) : stats && (
                <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                    <Card><CardContent className="pt-4">
                        <div className="flex items-center gap-2 text-sm text-muted-foreground"><CheckCircle className="h-4 w-4 text-success" /> Total Paid</div>
                        <div className="text-2xl font-bold text-success">${stats.total_paid?.toLocaleString()}</div>
                    </CardContent></Card>
                    <Card><CardContent className="pt-4">
                        <div className="flex items-center gap-2 text-sm text-muted-foreground"><Clock className="h-4 w-4 text-warning" /> Pending</div>
                        <div className="text-2xl font-bold text-warning">${stats.total_pending?.toLocaleString()}</div>
                        <p className="text-xs text-muted-foreground">{stats.pending_count} payouts</p>
                    </CardContent></Card>
                    <Card><CardContent className="pt-4">
                        <div className="flex items-center gap-2 text-sm text-muted-foreground"><AlertTriangle className="h-4 w-4 text-destructive" /> Failed</div>
                        <div className="text-2xl font-bold text-destructive">${stats.total_failed?.toLocaleString()}</div>
                    </CardContent></Card>
                    <Card><CardContent className="pt-4">
                        <div className="flex items-center gap-2 text-sm text-muted-foreground"><Wallet className="h-4 w-4" /> Total Payouts</div>
                        <div className="text-2xl font-bold">{stats.payout_count}</div>
                    </CardContent></Card>
                </div>
            )}

            {/* Filter + Table */}
            <Card>
                <CardHeader className="flex flex-row items-center justify-between gap-3 flex-wrap">
                    <CardTitle>Payout History</CardTitle>
                    <div className="flex items-center gap-2 flex-wrap">
                        <div className="flex gap-1 bg-muted rounded-lg p-0.5">
                            {["all", "pending", "completed", "failed"].map(s => (
                                <button key={s} onClick={() => setStatusFilter(s)}
                                    className={`px-3 py-1 rounded-md text-xs font-medium transition ${statusFilter === s ? "bg-background shadow-sm" : "text-muted-foreground"}`}>
                                    {s.charAt(0).toUpperCase() + s.slice(1)}
                                </button>
                            ))}
                        </div>
                        <Button
                            variant="outline"
                            size="sm"
                            onClick={handleBulkRetry}
                            disabled={bulkRetrying || !overview}
                            title="Retry every failed/cancelled payout in the selected period"
                        >
                            <Undo2 className="h-3.5 w-3.5 mr-1.5" />
                            {bulkRetrying ? "Retrying…" : "Bulk retry failed"}
                        </Button>
                        <Button
                            variant="outline"
                            size="sm"
                            onClick={handleExportCsv}
                            disabled={payouts.length === 0}
                        >
                            <Download className="h-3.5 w-3.5 mr-1.5" />
                            Export CSV
                        </Button>
                    </div>
                </CardHeader>
                <CardContent className="p-0">
                    <Table>
                        <TableHeader>
                            <TableRow>
                                <SortableHead column="driver_name" sort={payoutsSort} onSort={payoutsToggle}>Driver</SortableHead>
                                <SortableHead column="amount" sort={payoutsSort} onSort={payoutsToggle}>Amount</SortableHead>
                                <SortableHead column="status" sort={payoutsSort} onSort={payoutsToggle}>Status</SortableHead>
                                <SortableHead column="bank_name" sort={payoutsSort} onSort={payoutsToggle}>Bank</SortableHead>
                                <SortableHead column="created_at" sort={payoutsSort} onSort={payoutsToggle}>Requested</SortableHead>
                                <TableHead className="text-right">Action</TableHead>
                            </TableRow>
                        </TableHeader>
                        <TableBody>
                            {sortedFiltered.length === 0 ? (
                                <TableRow><TableCell colSpan={6} className="text-center py-8 text-muted-foreground">No payouts found</TableCell></TableRow>
                            ) : sortedFiltered.map((p: any) => {
                                const isRetryable = p.status === "failed" || p.status === "cancelled";
                                const isRetrying = retryingId === p.id;
                                return (
                                    <TableRow key={p.id}>
                                        <TableCell className="font-medium">{p.driver_name || "Unknown"}</TableCell>
                                        <TableCell className="font-mono font-bold">${Number(p.amount || 0).toFixed(2)}</TableCell>
                                        <TableCell>
                                            <Badge className={statusBadge(p.status)}>{p.status}</Badge>
                                            {p.error_message && (
                                                <p className="text-[10px] text-destructive mt-0.5 truncate max-w-[200px]" title={p.error_message}>
                                                    {p.error_message}
                                                </p>
                                            )}
                                        </TableCell>
                                        <TableCell className="text-sm text-muted-foreground">{p.bank_name || "—"} {p.account_last4 ? `•••${p.account_last4}` : ""}</TableCell>
                                        <TableCell className="text-xs text-muted-foreground">{formatDate(p.created_at)}</TableCell>
                                        <TableCell className="text-right">
                                            {isRetryable ? (
                                                <Button
                                                    size="xs"
                                                    variant="outline"
                                                    className="h-7 text-[11px]"
                                                    onClick={() => handleRowRetry(p.id)}
                                                    disabled={isRetrying}
                                                >
                                                    <Undo2 className="h-3 w-3 mr-1" />
                                                    {isRetrying ? "Retrying…" : "Retry"}
                                                </Button>
                                            ) : (
                                                <span className="text-[10px] text-muted-foreground">—</span>
                                            )}
                                        </TableCell>
                                    </TableRow>
                                );
                            })}
                        </TableBody>
                    </Table>
                </CardContent>
            </Card>

            {/* Pass 4 — compliance. T4A snapshot + period close. Sits
                below the transaction table so finance opens the page
                lower to find these; day-to-day operators don't see
                them on first glance. */}
            {overview && (
                <PayoutsCompliance overview={overview} onClosed={refreshAll} />
            )}
        </div>
    );
}
