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
    Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import {
    AlertTriangle, CalendarClock, CheckCircle, ChevronDown, ChevronRight,
    Download, Filter, RefreshCw, UserX, Wallet, XCircle,
} from "lucide-react";
import { exportToCsv } from "@/lib/export-csv";
import { formatCurrency, formatDate } from "@/lib/utils";
import {
    getAutoPayoutBatches, getBlockedPayoutDrivers, getServiceAreas,
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

/**
 * Calendar span of an ISO week key like "2026-W33".
 *
 * ISO weeks run Monday→Sunday and week 1 is the one containing Jan 4, so a
 * week can start in the previous calendar year ("2026-W01" begins Dec 29,
 * 2025). All arithmetic is in UTC to keep the boundaries stable regardless
 * of where the operator is sitting.
 *
 * The batch runs on the LAST day of its week — the Sunday that closes it.
 */
function isoWeekRange(weekKey: string): { start: Date; end: Date } | null {
    const m = /^(\d{4})-W(\d{2})$/.exec(weekKey);
    if (!m) return null;
    const [year, week] = [Number(m[1]), Number(m[2])];
    const jan4 = new Date(Date.UTC(year, 0, 4));
    const jan4Dow = jan4.getUTCDay() || 7; // Sunday is 0 in JS, 7 in ISO
    const week1Monday = new Date(jan4);
    week1Monday.setUTCDate(jan4.getUTCDate() - (jan4Dow - 1));
    const start = new Date(week1Monday);
    start.setUTCDate(week1Monday.getUTCDate() + (week - 1) * 7);
    const end = new Date(start);
    end.setUTCDate(start.getUTCDate() + 6);
    return { start, end };
}

/** "Aug 10–16" (or "Aug 31 – Sep 6" when the week straddles two months). */
function weekRangeLabel(weekKey: string): string {
    const r = isoWeekRange(weekKey);
    if (!r) return "";
    const day = (d: Date) => d.toLocaleDateString("en-CA", { timeZone: "UTC", day: "numeric" });
    const monthDay = (d: Date) =>
        d.toLocaleDateString("en-CA", { timeZone: "UTC", month: "short", day: "numeric" });
    return r.start.getUTCMonth() === r.end.getUTCMonth()
        ? `${monthDay(r.start)}–${day(r.end)}`
        : `${monthDay(r.start)} – ${monthDay(r.end)}`;
}

function StatusBadge({ status }: { status: string }) {
    const cfg = STATUS_CONFIG[status] ?? { label: status, cls: "bg-zinc-500/15 text-zinc-600" };
    return <Badge className={cfg.cls}>{cfg.label}</Badge>;
}

export default function AutoPayoutsPanel() {
    const [batches, setBatches] = useState<AutoPayoutBatch[]>([]);
    const [blocked, setBlocked] = useState<BlockedDriver[]>([]);
    const [byReason, setByReason] = useState<Record<string, number>>({});
    const [serviceAreas, setServiceAreas] = useState<Array<{ id: string; name?: string }>>([]);
    const [areaId, setAreaId] = useState("all");
    const [weekKey, setWeekKey] = useState("all");
    // 20 weeks by default; "Show more" widens to a full year of history.
    const [weekLimit, setWeekLimit] = useState(20);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");
    const [expanded, setExpanded] = useState<string | null>(null);

    // Service areas are independent of the selected market — fetch once.
    useEffect(() => {
        getServiceAreas()
            .then((rows) => setServiceAreas(Array.isArray(rows) ? rows : []))
            .catch(() => setServiceAreas([]));
    }, []);

    const load = useCallback(async () => {
        setLoading(true);
        setError("");
        try {
            const [b, d] = await Promise.all([
                getAutoPayoutBatches(weekLimit),
                getBlockedPayoutDrivers(50, areaId !== "all" ? areaId : undefined),
            ]);
            setBatches(b.batches ?? []);
            setBlocked(d.blocked ?? []);
            setByReason(d.by_reason ?? {});
        } catch (e: unknown) {
            const msg = e instanceof Error ? e.message : "";
            // During rollout the two failure modes are specific and fixable,
            // and both look like a generic outage otherwise: the backend
            // hasn't been redeployed with these routes (404), or the
            // migration hasn't been applied (503 naming the table).
            setError(
                /404|not found/i.test(msg)
                    ? "This view needs a backend deploy — the weekly-payout endpoints aren't live on this environment yet."
                    : msg || "Could not load auto-payout data.",
            );
        } finally {
            setLoading(false);
        }
    }, [areaId, weekLimit]);

    useEffect(() => { load(); }, [load]);

    const areaName = useCallback(
        (id: string) => {
            if (id === "unassigned") return "No service area";
            return serviceAreas.find((a) => a.id === id)?.name || id.slice(0, 8);
        },
        [serviceAreas],
    );

    /**
     * Per-run figures for the selected market. Runs recorded before per-area
     * tracking existed have no `area_summary` — return null so the UI can say
     * "not recorded" rather than render a misleading $0.00.
     */
    const runSlice = useCallback(
        (b: AutoPayoutBatch) => {
            if (areaId === "all") {
                return { paid: b.drivers_paid, failed: b.drivers_failed, amount: b.total_amount };
            }
            if (!b.area_summary) return null;
            const slice = b.area_summary[areaId];
            return slice
                ? { paid: slice.paid, failed: slice.failed, amount: slice.amount }
                : { paid: 0, failed: 0, amount: "0" };
        },
        [areaId],
    );

    const heldTotal = blocked.reduce((sum, d) => sum + parseFloat(d.pending_amount || "0"), 0);
    // "All weeks" keeps the newest run in the tiles; picking a week pins them
    // to it, so the tiles and the table always describe the same run.
    const visibleBatches = weekKey === "all" ? batches : batches.filter((b) => b.week_key === weekKey);
    const focusRun = visibleBatches[0];
    const focusSlice = focusRun ? runSlice(focusRun) : null;
    const scopeLabel = areaId === "all" ? "all service areas" : areaName(areaId);

    /**
     * Exports carry driver_id only — never names, phones, or bank details
     * (PIPEDA). Filenames encode the active filters so a file sitting in
     * someone's Downloads folder still says what it is.
     */
    const filterSuffix = `${weekKey === "all" ? "all-weeks" : weekKey}_${
        areaId === "all" ? "all-areas" : areaName(areaId).replace(/\s+/g, "-").toLowerCase()
    }`;

    const exportBlocked = useCallback(() => {
        exportToCsv(`blocked-drivers_${filterSuffix}.csv`, blocked, [
            { key: "driver_id", label: "Driver ID" },
            {
                label: "Service area",
                value: (r) => (r.service_area_id ? areaName(String(r.service_area_id)) : ""),
            },
            { label: "Reason", value: (r) => reasonLabel(String(r.reason)) },
            { key: "reason", label: "Reason code" },
            { key: "pending_amount", label: "Amount held (CAD)" },
        ]);
    }, [blocked, areaName, filterSuffix]);

    const exportRuns = useCallback(() => {
        // One row per week per service area when a breakdown exists, so the
        // file is pivot-ready; a single row otherwise. Runs predating
        // per-area tracking leave the numeric columns blank rather than 0,
        // which would read as "this market earned nothing".
        const rows: Record<string, unknown>[] = [];
        for (const b of visibleBatches) {
            const dates = weekRangeLabel(b.week_key);
            const areas = b.area_summary ?? {};
            const scoped =
                areaId === "all"
                    ? Object.entries(areas)
                    : Object.entries(areas).filter(([id]) => id === areaId);
            if (scoped.length === 0) {
                const fleetWide = areaId === "all";
                rows.push({
                    week: b.week_key,
                    dates,
                    service_area: fleetWide ? "All areas" : areaName(areaId),
                    status: b.status,
                    paid: fleetWide ? b.drivers_paid : "",
                    failed: fleetWide ? b.drivers_failed : "",
                    amount: fleetWide ? b.total_amount : "",
                    finished: b.completed_at ?? "",
                    errors: b.error_summary ?? "",
                });
                continue;
            }
            for (const [id, a] of scoped) {
                rows.push({
                    week: b.week_key,
                    dates,
                    service_area: areaName(id),
                    status: b.status,
                    paid: a.paid,
                    failed: a.failed,
                    amount: a.amount,
                    finished: b.completed_at ?? "",
                    errors: b.error_summary ?? "",
                });
            }
        }
        exportToCsv(`weekly-payout-runs_${filterSuffix}.csv`, rows, [
            { key: "week", label: "Week" },
            { key: "dates", label: "Dates" },
            { key: "service_area", label: "Service area" },
            { key: "status", label: "Status" },
            { key: "paid", label: "Drivers paid" },
            { key: "failed", label: "Drivers failed" },
            { key: "amount", label: "Amount sent (CAD)" },
            { key: "finished", label: "Finished at" },
            { key: "errors", label: "Errors" },
        ]);
    }, [visibleBatches, areaId, areaName, filterSuffix]);

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
                <div className="flex items-center gap-2 shrink-0">
                    {/* Scopes every figure below to one market. The batch always
                        runs fleet-wide — this filters reporting, not payments. */}
                    {/* Week picker. Lists the runs actually loaded, each with
                        its calendar span — "2026-W33" alone is unreadable. */}
                    <Select value={weekKey} onValueChange={setWeekKey}>
                        <SelectTrigger className="h-9 text-xs w-[210px]" aria-label="Filter by week">
                            <SelectValue placeholder="All weeks" />
                        </SelectTrigger>
                        <SelectContent>
                            <SelectItem value="all" className="text-xs">All weeks</SelectItem>
                            {batches.map((b) => (
                                <SelectItem key={b.id} value={b.week_key} className="text-xs">
                                    {b.week_key} · {weekRangeLabel(b.week_key)}
                                </SelectItem>
                            ))}
                        </SelectContent>
                    </Select>
                    <Filter className="h-3.5 w-3.5 text-muted-foreground" />
                    <Select value={areaId} onValueChange={setAreaId}>
                        <SelectTrigger className="h-9 text-xs w-[190px]" aria-label="Filter by service area">
                            <SelectValue placeholder="All service areas" />
                        </SelectTrigger>
                        <SelectContent>
                            <SelectItem value="all" className="text-xs">All service areas</SelectItem>
                            {serviceAreas.map((a) => (
                                <SelectItem key={a.id} value={a.id} className="text-xs">
                                    {a.name || a.id.slice(0, 8)}
                                </SelectItem>
                            ))}
                        </SelectContent>
                    </Select>
                    <Button variant="outline" size="sm" onClick={load}>
                        <RefreshCw className="h-4 w-4 mr-2" /> Refresh
                    </Button>
                </div>
            </div>

            {/* Summary tiles */}
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                <Card>
                    <CardContent className="pt-6">
                        <div className="flex items-center gap-2 text-muted-foreground text-sm">
                            <CalendarClock className="h-4 w-4" /> {weekKey === "all" ? "Last run" : "Selected week"}
                        </div>
                        <div className="mt-2 flex items-center gap-2">
                            <span className="text-2xl font-bold">{focusRun?.week_key ?? "—"}</span>
                            {focusRun && <StatusBadge status={focusRun.status} />}
                        </div>
                        {focusRun && (
                            <p className="text-xs text-muted-foreground mt-1">
                                {weekRangeLabel(focusRun.week_key)} · paid out {formatDate(focusRun.completed_at)}
                            </p>
                        )}
                    </CardContent>
                </Card>
                <Card>
                    <CardContent className="pt-6">
                        <div className="flex items-center gap-2 text-muted-foreground text-sm">
                            <CheckCircle className="h-4 w-4" /> {weekKey === "all" ? "Paid last run" : "Paid that week"}
                        </div>
                        <div className="mt-2 text-2xl font-bold">
                            {!focusRun ? "—" : focusSlice ? formatCurrency(focusSlice.amount) : "Not recorded"}
                        </div>
                        <p className="text-xs text-muted-foreground mt-1">
                            {!focusRun
                                ? "No runs yet"
                                : focusSlice
                                    ? `${focusSlice.paid} driver${focusSlice.paid === 1 ? "" : "s"} · ${scopeLabel}`
                                    : "This run predates per-area tracking"}
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
                    <div className="flex items-start justify-between gap-4">
                        <CardTitle className="flex items-center gap-2">
                            <UserX className="h-5 w-5" /> Blocked drivers
                        </CardTitle>
                        {blocked.length > 0 && (
                            <Button variant="outline" size="sm" onClick={exportBlocked}>
                                <Download className="h-4 w-4 mr-2" /> Export CSV
                            </Button>
                        )}
                    </div>
                    <p className="text-sm text-muted-foreground">
                        Drivers in <span className="font-medium text-foreground">{scopeLabel}</span> with
                        $10 or more waiting that the batch cannot pay right now. Each was sent a
                        notification telling them what to fix — except suspended accounts, which your
                        team handles directly.
                    </p>
                </CardHeader>
                <CardContent>
                    {blocked.length === 0 ? (
                        <div className="py-10 text-center text-muted-foreground">
                            <CheckCircle className="h-8 w-8 mx-auto mb-2 text-green-600" />
                            <p className="text-sm">
                                No blocked drivers in {scopeLabel} — everyone with a payable balance is ready to be paid.
                            </p>
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
                                            {areaId === "all" && <TableHead>Service area</TableHead>}
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
                                                {areaId === "all" && (
                                                    <TableCell className="text-sm text-muted-foreground">
                                                        {d.service_area_id ? areaName(d.service_area_id) : "—"}
                                                    </TableCell>
                                                )}
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
                    <div className="flex items-start justify-between gap-4">
                        <CardTitle className="flex items-center gap-2">
                            <CalendarClock className="h-5 w-5" /> Weekly run history
                        </CardTitle>
                        {visibleBatches.length > 0 && (
                            <Button variant="outline" size="sm" onClick={exportRuns}>
                                <Download className="h-4 w-4 mr-2" /> Export CSV
                            </Button>
                        )}
                    </div>
                    <p className="text-sm text-muted-foreground">
                        One row per week, newest first. Expand a row to see why drivers were skipped.
                    </p>
                </CardHeader>
                <CardContent>
                    {visibleBatches.length === 0 ? (
                        <div className="py-10 text-center text-muted-foreground">
                            <CalendarClock className="h-8 w-8 mx-auto mb-2 opacity-50" />
                            <p className="text-sm">
                                {batches.length === 0
                                    ? "No runs recorded yet. The first batch appears here after its Sunday run."
                                    : `No run recorded for ${weekKey}.`}
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
                                    {visibleBatches.map((b) => {
                                        const counts = b.skipped_summary?.counts ?? {};
                                        const withBalance = b.skipped_summary?.drivers_with_balance ?? {};
                                        const areas = b.area_summary ?? {};
                                        const hasDetail =
                                            Object.keys(counts).length > 0 ||
                                            !!b.error_summary ||
                                            Object.keys(areas).length > 0;
                                        const isOpen = expanded === b.id;
                                        const slice = runSlice(b);
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
                                                    <TableCell className="font-semibold">
                                                        {b.week_key}
                                                        {/* ISO week numbers are opaque; ops thinks in dates. */}
                                                        <div className="text-xs font-normal text-muted-foreground">
                                                            {weekRangeLabel(b.week_key)}
                                                        </div>
                                                    </TableCell>
                                                    <TableCell><StatusBadge status={b.status} /></TableCell>
                                                    <TableCell className="text-right">{slice ? slice.paid : "—"}</TableCell>
                                                    <TableCell className="text-right">
                                                        {!slice
                                                            ? "—"
                                                            : slice.failed > 0
                                                                ? <span className="text-destructive font-semibold">{slice.failed}</span>
                                                                : slice.failed}
                                                    </TableCell>
                                                    <TableCell className="text-right font-semibold">
                                                        {slice
                                                            ? formatCurrency(slice.amount)
                                                            : <span className="text-xs font-normal text-muted-foreground">Not recorded</span>}
                                                    </TableCell>
                                                    <TableCell className="text-sm text-muted-foreground">
                                                        {b.completed_at ? formatDate(b.completed_at) : "—"}
                                                    </TableCell>
                                                </TableRow>
                                                {isOpen && (
                                                    <TableRow key={`${b.id}-detail`}>
                                                        <TableCell colSpan={7} className="bg-muted/40">
                                                            <div className="space-y-3 py-2">
                                                                {Object.keys(areas).length > 0 && (
                                                                    <div>
                                                                        <p className="text-xs font-semibold uppercase text-muted-foreground mb-2">
                                                                            By service area
                                                                        </p>
                                                                        <div className="grid gap-1.5 sm:grid-cols-2 lg:grid-cols-3">
                                                                            {Object.entries(areas).map(([id, a]) => (
                                                                                <div
                                                                                    key={id}
                                                                                    className="flex items-center justify-between gap-3 rounded border px-2.5 py-1.5 text-sm"
                                                                                >
                                                                                    <span className="truncate">{areaName(id)}</span>
                                                                                    <span className="shrink-0 text-muted-foreground text-xs">
                                                                                        {a.paid} paid
                                                                                        {a.failed > 0 && <span className="text-destructive"> · {a.failed} failed</span>}
                                                                                        {" · "}
                                                                                        <span className="font-semibold text-foreground">{formatCurrency(a.amount)}</span>
                                                                                    </span>
                                                                                </div>
                                                                            ))}
                                                                        </div>
                                                                    </div>
                                                                )}
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

                    {/* Only offered when the fetch actually filled the window —
                        otherwise there is nothing older to load. */}
                    {weekKey === "all" && weekLimit === 20 && batches.length >= 20 && (
                        <div className="pt-4 text-center">
                            <Button variant="outline" size="sm" onClick={() => setWeekLimit(52)}>
                                Show up to a year of weeks
                            </Button>
                        </div>
                    )}
                </CardContent>
            </Card>
        </div>
    );
}
