"use client";

/**
 * Driver earnings-statements panel — lives in the driver detail Sheet's
 * Payouts tab.
 *
 * Two modes, one document:
 *  - "Sent statements": the ledger the backend statement job writes
 *    (weekly Monday / monthly 1st). Each row downloads or re-sends the
 *    exact statement that driver was emailed.
 *  - "Custom range": pick any start/end date and download or email the
 *    same statement for that window.
 *
 * The PDF is regenerated from live data on every call, so what an admin
 * downloads and what the driver receives can never diverge.
 */

import { useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Pagination } from "@/components/ui/pagination";
import { Download, Mail, FileText, RefreshCw } from "lucide-react";
import {
    getDriverStatements,
    downloadDriverStatement,
    emailDriverStatement,
    type DriverStatement,
    type StatementSelection,
} from "@/lib/api";

const STMT_PAGE_SIZE_OPTIONS = [25, 50, 100] as const;

function triggerBrowserDownload(blob: Blob, filename: string) {
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
}

const STATUS_STYLE: Record<string, { label: string; cls: string }> = {
    sent: { label: "Emailed", cls: "bg-success/15 text-success" },
    // eslint-disable-next-line no-restricted-syntax -- "claimed" (in progress, neither good nor bad) has no semantic-token equivalent; must stay distinct from sent/failed/skipped (#2816)
    claimed: { label: "In progress", cls: "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300" },
    failed: { label: "Failed", cls: "bg-destructive/15 text-destructive" },
    skipped_no_email: { label: "No email on file", cls: "bg-warning/15 text-warning" },
    skipped_inactive: { label: "No activity", cls: "bg-muted text-muted-foreground" },
};

const fmtPeriod = (s: DriverStatement) => {
    const kind = s.period_type === "monthly" ? "Monthly" : "Weekly";
    return `${kind} · ${s.period_start} → ${s.period_end}`;
};

const todayISO = () => new Date().toISOString().slice(0, 10);
const daysAgoISO = (n: number) => new Date(Date.now() - n * 86_400_000).toISOString().slice(0, 10);

export interface DriverStatementsPanelProps {
    driverId: string;
    driverName: string;
    /** Injected by the parent so this panel reuses the page's toast. */
    notify: (opts: { title: string; description?: string; variant?: "destructive" }) => void;
}

export function DriverStatementsPanel({ driverId, driverName, notify }: DriverStatementsPanelProps) {
    const [statements, setStatements] = useState<DriverStatement[] | null>(null);
    const [loading, setLoading] = useState(false);
    const [busyKey, setBusyKey] = useState<string | null>(null);
    const [from, setFrom] = useState(daysAgoISO(30));
    const [to, setTo] = useState(todayISO());
    const [stmtPage, setStmtPage] = useState(0);
    const [stmtPageSize, setStmtPageSize] = useState<number>(25);

    const load = useCallback(async () => {
        setLoading(true);
        try {
            const res = await getDriverStatements(driverId);
            setStatements(res.statements || []);
        } catch (e: unknown) {
            // Never leave a silent empty list — an API failure that renders as
            // "no statements yet" would read as "the job never ran".
            setStatements(null);
            notify({
                title: "Could not load statements",
                description: e instanceof Error ? e.message : "Unknown error",
                variant: "destructive",
            });
        } finally {
            setLoading(false);
        }
    }, [driverId, notify]);

    useEffect(() => {
        void load();
    }, [load]);

    const runDownload = async (key: string, selection: StatementSelection) => {
        setBusyKey(key);
        try {
            const { blob, filename } = await downloadDriverStatement(driverId, selection);
            triggerBrowserDownload(blob, filename);
        } catch (e: unknown) {
            notify({
                title: "Could not download statement",
                description: e instanceof Error ? e.message : "Unknown error",
                variant: "destructive",
            });
        } finally {
            setBusyKey(null);
        }
    };

    const runEmail = async (key: string, selection: StatementSelection) => {
        setBusyKey(key);
        try {
            const res = await emailDriverStatement(driverId, selection);
            notify({ title: "Statement sent", description: `Emailed ${res.period_label} to ${driverName}.` });
            void load();
        } catch (e: unknown) {
            notify({
                title: "Could not email statement",
                description: e instanceof Error ? e.message : "Unknown error",
                variant: "destructive",
            });
        } finally {
            setBusyKey(null);
        }
    };

    const rangeInvalid = !from || !to || from > to;
    const rangeSelection: StatementSelection = { start: from, end: to };

    return (
        <div className="rounded-xl border border-border bg-card overflow-hidden">
            <div className="px-4 py-3 border-b border-border flex items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                    <FileText className="h-4 w-4 text-muted-foreground" />
                    <h4 className="text-sm font-semibold">Earnings statements</h4>
                </div>
                <Button
                    size="xs"
                    variant="ghost"
                    className="h-6 text-[10px] px-2"
                    onClick={() => void load()}
                    disabled={loading}
                    title="Reload the sent-statement list"
                >
                    <RefreshCw className={`h-3 w-3 mr-1 ${loading ? "animate-spin" : ""}`} />
                    Refresh
                </Button>
            </div>

            {/* Custom date range → download / email */}
            <div className="px-4 py-3 border-b border-border space-y-2">
                <p className="text-[11px] text-muted-foreground">
                    Generate a statement for any date range, or use the sent statements below.
                </p>
                <div className="flex flex-wrap items-end gap-2">
                    <div className="space-y-1">
                        <Label htmlFor="stmt-from" className="text-[10px] uppercase tracking-wide text-muted-foreground">From</Label>
                        <Input
                            id="stmt-from"
                            type="date"
                            value={from}
                            max={to || undefined}
                            onChange={(e) => setFrom(e.target.value)}
                            className="h-8 w-[150px] text-xs"
                        />
                    </div>
                    <div className="space-y-1">
                        <Label htmlFor="stmt-to" className="text-[10px] uppercase tracking-wide text-muted-foreground">To</Label>
                        <Input
                            id="stmt-to"
                            type="date"
                            value={to}
                            min={from || undefined}
                            onChange={(e) => setTo(e.target.value)}
                            className="h-8 w-[150px] text-xs"
                        />
                    </div>
                    <Button
                        size="sm"
                        variant="outline"
                        className="h-8"
                        disabled={rangeInvalid || busyKey === "range-download"}
                        onClick={() => void runDownload("range-download", rangeSelection)}
                    >
                        <Download className="h-3.5 w-3.5 mr-1.5" />
                        {busyKey === "range-download" ? "Preparing…" : "Download PDF"}
                    </Button>
                    <Button
                        size="sm"
                        variant="outline"
                        className="h-8"
                        disabled={rangeInvalid || busyKey === "range-email"}
                        onClick={() => void runEmail("range-email", rangeSelection)}
                        title={`Email this statement to ${driverName}`}
                    >
                        <Mail className="h-3.5 w-3.5 mr-1.5" />
                        {busyKey === "range-email" ? "Sending…" : "Email to driver"}
                    </Button>
                </div>
                {rangeInvalid && (
                    <p className="text-[11px] text-destructive">
                        Pick a start and end date — the end date cannot be before the start date.
                    </p>
                )}
            </div>

            {/* Sent statements ledger */}
            <div className="divide-y divide-border">
                {loading && !statements ? (
                    <div className="px-4 py-6 space-y-2 animate-pulse">
                        {[0, 1, 2].map((i) => <div key={i} className="h-8 rounded bg-muted" />)}
                    </div>
                ) : !statements || statements.length === 0 ? (
                    <p className="px-4 py-6 text-center text-xs text-muted-foreground">
                        No statements sent yet. Weekly statements go out Mondays and monthly on the 1st.
                    </p>
                ) : (
                    statements.slice(stmtPage * stmtPageSize, (stmtPage + 1) * stmtPageSize).map((s) => {
                        const style = STATUS_STYLE[s.status] ?? { label: s.status, cls: "bg-muted text-muted-foreground" };
                        const selection = {
                            period_type: s.period_type as "weekly" | "monthly",
                            period_start: s.period_start,
                        } as StatementSelection;
                        return (
                            <div key={s.id} className="px-4 py-2.5 flex items-center justify-between gap-3 flex-wrap">
                                <div className="min-w-0">
                                    <p className="text-xs font-medium">{fmtPeriod(s)}</p>
                                    <div className="flex items-center gap-2 mt-0.5">
                                        <span className={`inline-flex px-1.5 py-0.5 rounded text-[10px] font-semibold ${style.cls}`}>
                                            {style.label}
                                        </span>
                                        {s.totals?.earnings?.total && (
                                            <span className="text-[10px] text-muted-foreground tabular-nums">
                                                ${s.totals.earnings.total} earned
                                                {/* One era per number: previous-app money gets its
                                                    own label instead of inflating "paid out".
                                                    Rows stored before the split fall back to the
                                                    gross figure. */}
                                                {s.totals.payouts_previous_app_total != null && parseFloat(s.totals.payouts_previous_app_total) > 0 ? (
                                                    <>
                                                        {` · $${s.totals.payouts_spinr_total ?? "0.00"} paid out`}
                                                        {` · $${s.totals.payouts_previous_app_total} previous app`}
                                                    </>
                                                ) : s.totals.payouts_total ? ` · $${s.totals.payouts_total} paid out` : ""}
                                            </span>
                                        )}
                                    </div>
                                </div>
                                <div className="flex items-center gap-1.5">
                                    <Button
                                        size="xs"
                                        variant="outline"
                                        className="h-7 text-[10px] px-2"
                                        disabled={busyKey === `dl-${s.id}`}
                                        onClick={() => void runDownload(`dl-${s.id}`, selection)}
                                    >
                                        <Download className="h-3 w-3 mr-1" />
                                        {busyKey === `dl-${s.id}` ? "…" : "PDF"}
                                    </Button>
                                    <Button
                                        size="xs"
                                        variant="outline"
                                        className="h-7 text-[10px] px-2"
                                        disabled={busyKey === `em-${s.id}`}
                                        onClick={() => void runEmail(`em-${s.id}`, selection)}
                                        title={s.status === "sent" ? "Send this statement again" : `Email to ${driverName}`}
                                    >
                                        <Mail className="h-3 w-3 mr-1" />
                                        {busyKey === `em-${s.id}` ? "…" : s.status === "sent" ? "Resend" : "Email"}
                                    </Button>
                                </div>
                            </div>
                        );
                    })
                )}
            </div>
            {statements && statements.length > 0 && (
                <div className="px-4 py-3 border-t border-border flex items-center justify-between gap-3 flex-wrap">
                    <div className="flex items-center gap-2">
                        <span className="text-xs text-muted-foreground">Show</span>
                        <Select value={String(stmtPageSize)} onValueChange={(v) => { setStmtPageSize(Number(v)); setStmtPage(0); }}>
                            <SelectTrigger className="h-8 text-xs w-[70px]">
                                <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                                {STMT_PAGE_SIZE_OPTIONS.map(n => (
                                    <SelectItem key={n} value={String(n)} className="text-xs">{n}</SelectItem>
                                ))}
                            </SelectContent>
                        </Select>
                        <span className="text-xs text-muted-foreground">per page</span>
                    </div>
                    {statements.length > stmtPageSize && (
                        <Pagination
                            page={stmtPage}
                            pageSize={stmtPageSize}
                            hasNextPage={statements.length > (stmtPage + 1) * stmtPageSize}
                            totalCount={statements.length}
                            onPageChange={setStmtPage}
                        />
                    )}
                </div>
            )}
        </div>
    );
}
