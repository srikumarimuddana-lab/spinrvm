"use client";

/**
 * Weekly auto-payout monitoring (Spinr-controlled Sunday batch).
 *
 * Two views, in the order ops actually needs them:
 *   1. Blocked drivers — who the batch CANNOT pay right now and why, so the
 *      blocker can be chased before Sunday rather than discovered after.
 *   2. Batch history — what each weekly run paid, skipped, and failed.
 *
 * Both endpoints are read-only and gated by the `earnings` module. Driver
 * IDs only, never names/phones/bank details (PIPEDA — see CLAUDE.md).
 */

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
    Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";
import {
    AlertTriangle, CalendarClock, CheckCircle, ChevronDown, ChevronRight,
    RefreshCw, UserX, Wallet, XCircle,
} from "lucide-react";
import { formatCurrency, formatDate } from "@/lib/utils";
import {
    getAutoPayoutBatches, getBlockedPayoutDrivers,
    type AutoPayoutBatch, type BlockedDriver,
} from "@/lib/api";

const STATUS_CONFIG: Record<string, { label: string; cls: string }> = {
    completed: { label: "Completed", cls: "bg-green-500/15 text-green-700" },
    // "some paid, some not" — resumable, and deliberately distinct from
    // completed so a partial week can't read as a clean run.
    partial: { label: "Partial", cls: "bg-amber-500/15 text-amber-700" },
    running: { label: "Running", cls: "bg-blue-500/15 text-blue-700" },
    failed: { label: "Failed", cls: "bg-red-500/15 text-red-700" },
};

/** Backend skip reasons → what an operator should read. */
const REASON_LABELS: Record<string, string> = {
    no_stripe_account: "No payout account connected",
    stripe_payouts_disabled: "Stripe verification incomplete",
    suspended: "Driver account suspended",
    missing_gst: "Missing GST/HST number",
    missing_sin: "Missing SIN",
};

const reasonLabel = (r: string) => REASON_LABELS[r] ?? r;

function StatusBadge({ status }: { status: string }) {
    const cfg = STATUS_CONFIG[status] ?? { label: status, cls: "bg-zinc-500/15 text-zinc-600" };
    return <Badge className={cfg.cls}>{cfg.label}</Badge>;
}

export default function AutoPayoutsPanel() {
    const [batches, setBatches] = useState<AutoPayoutBatch[]>([]);
    const [blocked, setBlocked] = useState<BlockedDriver[]>([]);
    const [byReason, setByReason] = useState<Record<string, number>>({});
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");
    const [expanded, setExpanded] = useState<string | null>(null);

    const load = useCallback(async () => {
        setLoading(true);
        setError("");
        try {
            const [b, d] = await Promise.all([
                getAutoPayoutBatches(20),
                getBlockedPayoutDrivers(50),
            ]);
            setBatches(b.batches ?? []);
            setBlocked(d.blocked ?? []);
            setByReason(d.by_reason ?? {});
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : "Could not load auto-payout data.");
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => { load(); }, [load]);

    const heldTotal = blocked.reduce((sum, d) => sum + parseFloat(d.pending_amount || "0"), 0);
    const lastRun = batches[0];

    if (loading) {
        return (
            <div className="flex items-center justify-center py-16 text-muted-foreground">
                <RefreshCw className="h-5 w-5 mr-2 animate-spin" />
                Loading weekly payout data…
            </div>
        );
    }

    if (error) {
        return (
            <Card>
                <CardContent className="py-10 text-center space-y-3">
                    <AlertTriangle className="h-8 w-8 mx-auto text-destructive" />
                    <p className="text-sm text-destructive">{error}</p>
                    <Button variant="outline" onClick={load}>
                        <RefreshCw className="h-4 w-4 mr-2" /> Try again
                    </Button>
                </CardContent>
            </Card>
        );
    }

    return (
        <div className="space-y-6">
            {/* Header + how it works */}
            <div className="flex items-start justify-between gap-4">
                <div>
                    <h2 className="text-xl font-bold tracking-tight">Weekly Auto-Payouts</h2>
                    <p className="text-sm text-muted-foreground mt-1">
                        Spinr pays every eligible driver automatically each Sunday (from 6am Saskatchewan
                        time). Balances under $10 carry forward to the next week.
                    </p>
                </div>
                <Button variant="outline" size="sm" onClick={load}>
                    <RefreshCw className="h-4 w-4 mr-2" /> Refresh
                </Button>
            </div>

            {/* Summary tiles */}
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                <Card>
                    <CardContent className="pt-6">
                        <div className="flex items-center gap-2 text-muted-foreground text-sm">
                            <CalendarClock className="h-4 w-4" /> Last run
                        </div>
                        <div className="mt-2 flex items-center gap-2">
                            <span className="text-2xl font-bold">{lastRun?.week_key ?? "—"}</span>
                            {lastRun && <StatusBadge status={lastRun.status} />}
                        </div>
                    </CardContent>
                </Card>
                <Card>
                    <CardContent className="pt-6">
                        <div className="flex items-center gap-2 text-muted-foreground text-sm">
                            <CheckCircle className="h-4 w-4" /> Paid last run
                        </div>
                        <div className="mt-2 text-2xl font-bold">
                            {lastRun ? formatCurrency(lastRun.total_amount) : "—"}
                        </div>
                        <p className="text-xs text-muted-foreground mt-1">
                            {lastRun ? `${lastRun.drivers_paid} driver${lastRun.drivers_paid === 1 ? "" : "s"}` : "No runs yet"}
                        </p>
                    </CardContent>
                </Card>
                <Card>
                    <CardContent className="pt-6">
                        <div className="flex items-center gap-2 text-muted-foreground text-sm">
                            <UserX className="h-4 w-4" /> Blocked drivers
                        </div>
                        <div className="mt-2 text-2xl font-bold">{blocked.length}</div>
                        <p className="text-xs text-muted-foreground mt-1">with earnings waiting</p>
                    </CardContent>
                </Card>
                <Card>
                    <CardContent className="pt-6">
                        <div className="flex items-center gap-2 text-muted-foreground text-sm">
                            <Wallet className="h-4 w-4" /> Money held up
                        </div>
                        <div className="mt-2 text-2xl font-bold">{formatCurrency(heldTotal)}</div>
                        <p className="text-xs text-muted-foreground mt-1">unpayable until fixed</p>
                    </CardContent>
                </Card>
            </div>

            {/* Blocked drivers — the actionable list, so it comes first */}
            <Card>
                <CardHeader>
                    <CardTitle className="flex items-center gap-2">
                        <UserX className="h-5 w-5" /> Blocked drivers
                    </CardTitle>
                    <p className="text-sm text-muted-foreground">
                        Drivers with $10 or more waiting that the batch cannot pay right now. Each was
                        sent a notification telling them what to fix — except suspended accounts, which
                        your team handles directly.
                    </p>
                </CardHeader>
                <CardContent>
                    {blocked.length === 0 ? (
                        <div className="py-10 text-center text-muted-foreground">
                            <CheckCircle className="h-8 w-8 mx-auto mb-2 text-green-600" />
                            <p className="text-sm">No blocked drivers — everyone with a payable balance is ready to be paid.</p>
                        </div>
                    ) : (
                        <>
                            <div className="flex flex-wrap gap-2 mb-4">
                                {Object.entries(byReason).map(([reason, count]) => (
                                    <Badge key={reason} variant="outline" className="font-normal">
                                        {reasonLabel(reason)}: <span className="font-semibold ml-1">{count}</span>
                                    </Badge>
                                ))}
                            </div>
                            <div className="overflow-x-auto">
                                <Table>
                                    <TableHeader>
                                        <TableRow>
                                            <TableHead>Driver</TableHead>
                                            <TableHead>Why they can&apos;t be paid</TableHead>
                                            <TableHead className="text-right">Amount held</TableHead>
                                        </TableRow>
                                    </TableHeader>
                                    <TableBody>
                                        {blocked.map((d) => (
                                            <TableRow key={d.driver_id}>
                                                <TableCell>
                                                    <Link
                                                        href={`/dashboard/drivers?id=${d.driver_id}`}
                                                        className="text-primary hover:underline font-mono text-xs"
                                                    >
                                                        {d.driver_id}
                                                    </Link>
                                                </TableCell>
                                                <TableCell className="text-sm">{reasonLabel(d.reason)}</TableCell>
                                                <TableCell className="text-right font-semibold">
                                                    {formatCurrency(d.pending_amount)}
                                                </TableCell>
                                            </TableRow>
                                        ))}
                                    </TableBody>
                                </Table>
                            </div>
                        </>
                    )}
                </CardContent>
            </Card>

            {/* Weekly batch history */}
            <Card>
                <CardHeader>
                    <CardTitle className="flex items-center gap-2">
                        <CalendarClock className="h-5 w-5" /> Weekly run history
                    </CardTitle>
                    <p className="text-sm text-muted-foreground">
                        One row per week. Expand a row to see why drivers were skipped.
                    </p>
                </CardHeader>
                <CardContent>
                    {batches.length === 0 ? (
                        <div className="py-10 text-center text-muted-foreground">
                            <CalendarClock className="h-8 w-8 mx-auto mb-2 opacity-50" />
                            <p className="text-sm">
                                No runs recorded yet. The first batch appears here after its Sunday run.
                            </p>
                        </div>
                    ) : (
                        <div className="overflow-x-auto">
                            <Table>
                                <TableHeader>
                                    <TableRow>
                                        <TableHead className="w-8" />
                                        <TableHead>Week</TableHead>
                                        <TableHead>Status</TableHead>
                                        <TableHead className="text-right">Paid</TableHead>
                                        <TableHead className="text-right">Failed</TableHead>
                                        <TableHead className="text-right">Total sent</TableHead>
                                        {/* "Finished", not "Completed" — this column holds a
                                            timestamp and sits beside a Status column whose values
                                            include "Completed". Same word, two meanings, one row. */}
                                        <TableHead>Finished</TableHead>
                                    </TableRow>
                                </TableHeader>
                                <TableBody>
                                    {batches.map((b) => {
                                        const counts = b.skipped_summary?.counts ?? {};
                                        const withBalance = b.skipped_summary?.drivers_with_balance ?? {};
                                        const hasDetail = Object.keys(counts).length > 0 || !!b.error_summary;
                                        const isOpen = expanded === b.id;
                                        return (
                                            <>
                                                <TableRow
                                                    key={b.id}
                                                    className={hasDetail ? "cursor-pointer" : undefined}
                                                    onClick={() => hasDetail && setExpanded(isOpen ? null : b.id)}
                                                >
                                                    <TableCell>
                                                        {hasDetail && (isOpen
                                                            ? <ChevronDown className="h-4 w-4 text-muted-foreground" />
                                                            : <ChevronRight className="h-4 w-4 text-muted-foreground" />)}
                                                    </TableCell>
                                                    <TableCell className="font-semibold">{b.week_key}</TableCell>
                                                    <TableCell><StatusBadge status={b.status} /></TableCell>
                                                    <TableCell className="text-right">{b.drivers_paid}</TableCell>
                                                    <TableCell className="text-right">
                                                        {b.drivers_failed > 0
                                                            ? <span className="text-destructive font-semibold">{b.drivers_failed}</span>
                                                            : b.drivers_failed}
                                                    </TableCell>
                                                    <TableCell className="text-right font-semibold">
                                                        {formatCurrency(b.total_amount)}
                                                    </TableCell>
                                                    <TableCell className="text-sm text-muted-foreground">
                                                        {b.completed_at ? formatDate(b.completed_at) : "—"}
                                                    </TableCell>
                                                </TableRow>
                                                {isOpen && (
                                                    <TableRow key={`${b.id}-detail`}>
                                                        <TableCell colSpan={7} className="bg-muted/40">
                                                            <div className="space-y-3 py-2">
                                                                {Object.keys(counts).length > 0 && (
                                                                    <div>
                                                                        <p className="text-xs font-semibold uppercase text-muted-foreground mb-2">
                                                                            Skipped drivers
                                                                        </p>
                                                                        <div className="space-y-2">
                                                                            {Object.entries(counts).map(([reason, count]) => (
                                                                                <div key={reason} className="text-sm">
                                                                                    <span className="font-medium">{reasonLabel(reason)}</span>
                                                                                    <span className="text-muted-foreground"> — {count} driver{count === 1 ? "" : "s"}</span>
                                                                                    {(withBalance[reason]?.length ?? 0) > 0 && (
                                                                                        <div className="mt-1 flex flex-wrap gap-1.5">
                                                                                            {withBalance[reason].map((id) => (
                                                                                                <Link
                                                                                                    key={id}
                                                                                                    href={`/dashboard/drivers?id=${id}`}
                                                                                                    className="font-mono text-[11px] text-primary hover:underline border rounded px-1.5 py-0.5"
                                                                                                >
                                                                                                    {id}
                                                                                                </Link>
                                                                                            ))}
                                                                                        </div>
                                                                                    )}
                                                                                </div>
                                                                            ))}
                                                                        </div>
                                                                        <p className="text-xs text-muted-foreground mt-2">
                                                                            Listed IDs are drivers who had money waiting; the rest had nothing to pay out.
                                                                        </p>
                                                                    </div>
                                                                )}
                                                                {b.error_summary && (
                                                                    <div>
                                                                        <p className="text-xs font-semibold uppercase text-muted-foreground mb-1 flex items-center gap-1">
                                                                            <XCircle className="h-3 w-3" /> Errors
                                                                        </p>
                                                                        <p className="text-xs text-destructive break-words">{b.error_summary}</p>
                                                                    </div>
                                                                )}
                                                            </div>
                                                        </TableCell>
                                                    </TableRow>
                                                )}
                                            </>
                                        );
                                    })}
                                </TableBody>
                            </Table>
                        </div>
                    )}
                </CardContent>
            </Card>
        </div>
    );
}
