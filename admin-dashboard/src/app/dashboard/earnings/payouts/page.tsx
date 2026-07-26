"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import {
    Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";
import {
    Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import {
    Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import { Pagination } from "@/components/ui/pagination";
import { useTableSort, SortableHead } from "@/components/ui/sortable-table";
import {
    Wallet, CheckCircle, Clock, AlertTriangle, RefreshCw,
    RotateCcw, ArrowLeft, ExternalLink, Search,
} from "lucide-react";
import { formatCurrency, formatDate } from "@/lib/utils";
import { getPayouts, getPayoutStats, retryPayout } from "@/lib/api";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface Payout {
    id: string;
    driver_id?: string;
    driver_name?: string;
    amount: number;
    status: "pending" | "processing" | "completed" | "failed" | "cancelled";
    bank_name?: string;
    account_last4?: string;
    stripe_transfer_id?: string;
    failure_reason?: string;
    retry_requested_by?: string;
    created_at?: string;
    updated_at?: string;
}

interface PayoutStats {
    total_paid: number;
    total_pending: number;
    total_failed: number;
    payout_count: number;
    pending_count: number;
}

const STATUS_CONFIG: Record<string, { label: string; cls: string }> = {
    completed: { label: "Completed", cls: "bg-green-500/15 text-green-700" },
    pending:   { label: "Pending",   cls: "bg-amber-500/15 text-amber-700" },
    processing:{ label: "Processing",cls: "bg-blue-500/15 text-blue-700" },
    failed:    { label: "Failed",    cls: "bg-red-500/15 text-red-700" },
    cancelled: { label: "Cancelled", cls: "bg-zinc-500/15 text-zinc-600" },
};

const PAGE_SIZE = 25;

// ---------------------------------------------------------------------------
// Retry Confirm Modal
// ---------------------------------------------------------------------------

interface RetryModalProps {
    open: boolean;
    payout: Payout | null;
    onClose: () => void;
    onConfirm: () => Promise<void>;
}

function RetryModal({ open, payout, onClose, onConfirm }: RetryModalProps) {
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState("");

    const handleConfirm = async () => {
        setBusy(true);
        setError("");
        try {
            await onConfirm();
            onClose();
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : "Retry failed. Please try again.");
        } finally {
            setBusy(false);
        }
    };

    return (
        <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
            <DialogContent>
                <DialogHeader>
                    <DialogTitle>Retry Payout</DialogTitle>
                </DialogHeader>
                <div className="space-y-3 py-2">
                    <p className="text-sm text-muted-foreground">
                        Re-queue this payout for processing? The payment-retry loop will attempt
                        the transfer on its next tick.
                    </p>
                    {payout && (
                        <div className="rounded-md border p-3 text-sm space-y-1">
                            <div className="flex justify-between">
                                <span className="text-muted-foreground">Driver</span>
                                <span className="font-medium">{payout.driver_name ?? "Unknown"}</span>
                            </div>
                            <div className="flex justify-between">
                                <span className="text-muted-foreground">Amount</span>
                                <span className="font-bold">{formatCurrency(payout.amount)}</span>
                            </div>
                            {payout.failure_reason && (
                                <div className="flex justify-between">
                                    <span className="text-muted-foreground">Failure reason</span>
                                    <span className="text-red-600 text-xs max-w-[200px] text-right">{payout.failure_reason}</span>
                                </div>
                            )}
                        </div>
                    )}
                    {error && <p className="text-sm text-red-500">{error}</p>}
                </div>
                <DialogFooter>
                    <Button variant="outline" onClick={onClose} disabled={busy}>Cancel</Button>
                    <Button onClick={handleConfirm} disabled={busy}>
                        <RotateCcw className="h-4 w-4 mr-2" />
                        {busy ? "Retrying…" : "Retry Payout"}
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}

// ---------------------------------------------------------------------------
// Main Page
// ---------------------------------------------------------------------------

export default function PayoutsPage() {
    const [payouts, setPayouts] = useState<Payout[]>([]);
    const [stats, setStats] = useState<PayoutStats | null>(null);
    const [loading, setLoading] = useState(true);

    const [statusFilter, setStatusFilter] = useState("all");
    const [search, setSearch] = useState("");
    const [dateFrom, setDateFrom] = useState("");
    const [dateTo, setDateTo] = useState("");
    const [page, setPage] = useState(1);

    const [retryModal, setRetryModal] = useState<Payout | null>(null);
    const [toast, setToast] = useState("");

    const load = useCallback(async () => {
        setLoading(true);
        try {
            // Promise.all rejects on the first failure — if stats or list
            // 404s/502s, we'd lose the other call's data too. Use allSettled
            // so a backend hiccup on one endpoint doesn't blank the entire
            // page (the earnings tab uses the same defensive pattern).
            const [pRes, sRes] = await Promise.allSettled([
                getPayouts(statusFilter === "all" ? undefined : statusFilter),
                getPayoutStats(),
            ]);
            const p = pRes.status === "fulfilled" ? pRes.value : [];
            const s = sRes.status === "fulfilled" ? sRes.value : null;
            setPayouts(Array.isArray(p) ? p : []);
            setStats(s ?? null);
        } finally {
            setLoading(false);
        }
    }, [statusFilter]);

    useEffect(() => { load(); }, [load]);
    useEffect(() => { setPage(1); }, [statusFilter, search, dateFrom, dateTo]);

    const handleRetry = async () => {
        if (!retryModal) return;
        await retryPayout(retryModal.id);
        setToast(`Payout for ${retryModal.driver_name ?? "driver"} queued for retry.`);
        setTimeout(() => setToast(""), 4000);
        await load();
    };

    // Client-side filter (search + date range applied after fetch)
    const filtered = payouts.filter((p) => {
        if (search) {
            const q = search.toLowerCase();
            if (
                !p.driver_name?.toLowerCase().includes(q) &&
                !p.id.toLowerCase().includes(q) &&
                !p.stripe_transfer_id?.toLowerCase().includes(q)
            ) return false;
        }
        if (dateFrom && p.created_at && p.created_at < dateFrom) return false;
        if (dateTo && p.created_at && p.created_at.slice(0, 10) > dateTo) return false;
        return true;
    });

    const { sorted, sort, toggle } = useTableSort(filtered);
    const paged = sorted.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);
    const failedCount = payouts.filter((p) => p.status === "failed").length;

    return (
        <div className="space-y-6 p-6">
            {/* Header */}
            <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                    <Link href="/dashboard/earnings" className="text-muted-foreground hover:text-foreground">
                        <ArrowLeft className="h-5 w-5" />
                    </Link>
                    <Wallet className="h-6 w-6 text-primary" />
                    <div>
                        <h1 className="text-2xl font-bold">Payout Management</h1>
                        <p className="text-sm text-muted-foreground">Review, filter, and retry driver payouts</p>
                    </div>
                </div>
                <Button variant="outline" onClick={load} disabled={loading}>
                    <RefreshCw className={`h-4 w-4 mr-2 ${loading ? "animate-spin" : ""}`} />
                    Refresh
                </Button>
            </div>

            {/* Toast */}
            {toast && (
                <div className="rounded-md bg-green-50 border border-green-200 text-green-800 text-sm px-4 py-2">
                    {toast}
                </div>
            )}

            {/* Failed alert */}
            {failedCount > 0 && (
                <div className="rounded-md bg-red-50 border border-red-200 text-red-800 text-sm px-4 py-3 flex items-center gap-2">
                    <AlertTriangle className="h-4 w-4 flex-shrink-0" />
                    <span>
                        <strong>{failedCount} failed payout{failedCount !== 1 ? "s" : ""}</strong> require attention.{" "}
                        <button
                            className="underline font-medium"
                            onClick={() => setStatusFilter("failed")}
                        >
                            Show failed only
                        </button>
                    </span>
                </div>
            )}

            {/* Stats */}
            {stats && (
                <div className="grid grid-cols-2 gap-4 sm:grid-cols-5">
                    <Card>
                        <CardContent className="pt-4 pb-3">
                            <div className="flex items-center gap-1 mb-1">
                                <CheckCircle className="h-3 w-3 text-green-500" />
                                <p className="text-xs text-muted-foreground">Total Paid</p>
                            </div>
                            <p className="text-2xl font-bold text-green-600">{formatCurrency(stats.total_paid)}</p>
                        </CardContent>
                    </Card>
                    <Card>
                        <CardContent className="pt-4 pb-3">
                            <div className="flex items-center gap-1 mb-1">
                                <Clock className="h-3 w-3 text-amber-500" />
                                <p className="text-xs text-muted-foreground">Pending</p>
                            </div>
                            <p className="text-2xl font-bold text-amber-600">{formatCurrency(stats.total_pending)}</p>
                            <p className="text-xs text-muted-foreground">{stats.pending_count} payouts</p>
                        </CardContent>
                    </Card>
                    <Card>
                        <CardContent className="pt-4 pb-3">
                            <div className="flex items-center gap-1 mb-1">
                                <AlertTriangle className="h-3 w-3 text-red-500" />
                                <p className="text-xs text-muted-foreground">Failed</p>
                            </div>
                            <p className="text-2xl font-bold text-red-600">{formatCurrency(stats.total_failed)}</p>
                        </CardContent>
                    </Card>
                    <Card>
                        <CardContent className="pt-4 pb-3">
                            <p className="text-xs text-muted-foreground mb-1">Total Payouts</p>
                            <p className="text-2xl font-bold">{stats.payout_count}</p>
                        </CardContent>
                    </Card>
                    <Card>
                        <CardContent className="pt-4 pb-3">
                            <p className="text-xs text-muted-foreground mb-1">Showing</p>
                            <p className="text-2xl font-bold">{filtered.length}</p>
                        </CardContent>
                    </Card>
                </div>
            )}

            <Separator />

            {/* Filters */}
            <div className="flex flex-wrap gap-3 items-end">
                <div className="space-y-1">
                    <Label className="text-xs">Status</Label>
                    <Select value={statusFilter} onValueChange={setStatusFilter}>
                        <SelectTrigger className="w-36" aria-label="Status">
                            <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                            <SelectItem value="all">All Statuses</SelectItem>
                            <SelectItem value="pending">Pending</SelectItem>
                            <SelectItem value="processing">Processing</SelectItem>
                            <SelectItem value="completed">Completed</SelectItem>
                            <SelectItem value="failed">Failed</SelectItem>
                            <SelectItem value="cancelled">Cancelled</SelectItem>
                        </SelectContent>
                    </Select>
                </div>
                <div className="space-y-1">
                    <Label className="text-xs">Search</Label>
                    <div className="relative">
                        <Search className="absolute left-2 top-2.5 h-4 w-4 text-muted-foreground" />
                        <Input
                            className="pl-8 w-56"
                            placeholder="Driver, payout ID, Stripe ID"
                            value={search}
                            onChange={(e) => setSearch(e.target.value)}
                        />
                    </div>
                </div>
                <div className="space-y-1">
                    <Label className="text-xs">From</Label>
                    <Input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} className="w-36" aria-label="Start date" />
                </div>
                <div className="space-y-1">
                    <Label className="text-xs">To</Label>
                    <Input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} className="w-36" aria-label="End date" />
                </div>
                {(search || dateFrom || dateTo || statusFilter !== "all") && (
                    <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => { setSearch(""); setDateFrom(""); setDateTo(""); setStatusFilter("all"); }}
                    >
                        Clear filters
                    </Button>
                )}
            </div>

            {/* Table */}
            <Card>
                <CardContent className="p-0">
                    <Table>
                        <TableHeader>
                            <TableRow>
                                <SortableHead column="driver_name" sort={sort} onSort={toggle}>Driver</SortableHead>
                                <SortableHead column="amount" sort={sort} onSort={toggle} align="right">Amount</SortableHead>
                                <SortableHead column="status" sort={sort} onSort={toggle}>Status</SortableHead>
                                <SortableHead column="bank_name" sort={sort} onSort={toggle}>Bank</SortableHead>
                                <SortableHead column="stripe_transfer_id" sort={sort} onSort={toggle}>Stripe Transfer</SortableHead>
                                <SortableHead column="created_at" sort={sort} onSort={toggle}>Requested</SortableHead>
                                <TableHead className="text-right">Actions</TableHead>
                            </TableRow>
                        </TableHeader>
                        <TableBody>
                            {loading ? (
                                <TableRow>
                                    <TableCell colSpan={7} className="text-center py-12 text-muted-foreground">
                                        Loading…
                                    </TableCell>
                                </TableRow>
                            ) : paged.length === 0 ? (
                                <TableRow>
                                    <TableCell colSpan={7} className="text-center py-12 text-muted-foreground">
                                        No payouts found.
                                    </TableCell>
                                </TableRow>
                            ) : paged.map((p) => {
                                const sc = STATUS_CONFIG[p.status] ?? { label: p.status, cls: "bg-zinc-500/15 text-zinc-600" };
                                return (
                                    <TableRow key={p.id}>
                                        <TableCell className="font-medium">
                                            {p.driver_name ?? "Unknown"}
                                        </TableCell>
                                        <TableCell className="font-mono font-bold">
                                            {formatCurrency(p.amount)}
                                        </TableCell>
                                        <TableCell>
                                            <Badge className={sc.cls}>{sc.label}</Badge>
                                            {p.failure_reason && (
                                                <p className="text-xs text-red-500 mt-0.5">{p.failure_reason}</p>
                                            )}
                                        </TableCell>
                                        <TableCell className="text-sm text-muted-foreground">
                                            {p.bank_name ?? "—"}
                                            {p.account_last4 ? ` •••${p.account_last4}` : ""}
                                        </TableCell>
                                        <TableCell className="font-mono text-xs text-muted-foreground">
                                            {p.stripe_transfer_id ? (
                                                <span title={p.stripe_transfer_id}>
                                                    {p.stripe_transfer_id.slice(0, 18)}…
                                                </span>
                                            ) : "—"}
                                        </TableCell>
                                        <TableCell className="text-xs text-muted-foreground">
                                            {p.created_at ? formatDate(p.created_at) : "—"}
                                        </TableCell>
                                        <TableCell className="text-right">
                                            <div className="flex justify-end gap-2">
                                                <Link href={`/dashboard/earnings/payouts/${p.id}`}>
                                                    <Button size="sm" variant="outline">
                                                        <ExternalLink className="h-3 w-3" />
                                                    </Button>
                                                </Link>
                                                {(p.status === "failed" || p.status === "cancelled") && (
                                                    <Button
                                                        size="sm"
                                                        variant="outline"
                                                        onClick={() => setRetryModal(p)}
                                                    >
                                                        <RotateCcw className="h-3 w-3 text-amber-600" />
                                                    </Button>
                                                )}
                                            </div>
                                        </TableCell>
                                    </TableRow>
                                );
                            })}
                        </TableBody>
                    </Table>
                </CardContent>
            </Card>

            {filtered.length > PAGE_SIZE && (
                <Pagination
                    page={page}
                    totalCount={filtered.length}
                    pageSize={PAGE_SIZE}
                    hasNextPage={(page + 1) * PAGE_SIZE < filtered.length}
                    onPageChange={setPage}
                />
            )}

            <RetryModal
                open={!!retryModal}
                payout={retryModal}
                onClose={() => setRetryModal(null)}
                onConfirm={handleRetry}
            />
        </div>
    );
}
