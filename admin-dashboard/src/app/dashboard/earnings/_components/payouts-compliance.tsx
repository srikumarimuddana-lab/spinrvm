"use client";

import { useState } from "react";
import { Receipt, CheckCircle } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import {
    Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";
import { useTableSort, SortableHead } from "@/components/ui/sortable-table";
import { useToast } from "@/components/ui/use-toast";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { formatDate } from "@/lib/utils";
import { closePayoutPeriod, type PayoutsOverview } from "@/lib/api";
import { fmtMoney, fmtPeriodKey } from "./earnings-format";

export function PayoutsCompliance({ overview, onClosed }: { overview: PayoutsOverview; onClosed: () => Promise<void> | void }) {
    const { t4a_snapshot, period_locks } = overview;
    const { toast } = useToast();
    const now = new Date();
    // Default closure target: the previous calendar month — what
    // finance typically closes once the month wraps.
    const defaultYear = now.getMonth() === 0 ? now.getFullYear() - 1 : now.getFullYear();
    const defaultMonth = now.getMonth() === 0 ? 12 : now.getMonth(); // getMonth is 0-indexed; we want prior month 1-indexed
    const [closeYear, setCloseYear] = useState<number>(defaultYear);
    const [closeMonth, setCloseMonth] = useState<number>(defaultMonth);
    const [closing, setClosing] = useState(false);

    const handleClose = async () => {
        const periodLabel = fmtPeriodKey(`${closeYear}-${String(closeMonth).padStart(2, "0")}`);
        if (!window.confirm(
            `Close ${periodLabel}? This writes an audit-log entry snapshotting every completed payout in that month. ` +
            `Closure is advisory — no payouts are physically locked, but the audit row is permanent.`
        )) return;
        setClosing(true);
        try {
            const res = await closePayoutPeriod(closeYear, closeMonth);
            toast({
                title: `${periodLabel} closed`,
                description: `${res.payout_count} payouts · ${fmtMoney(res.total_amount)} total`,
            });
            await onClosed();
        } catch (e: any) {
            toast({ title: "Close failed", description: e?.message || "Unknown error", variant: "destructive" });
        } finally {
            setClosing(false);
        }
    };

    const totalBucketed = t4a_snapshot.drivers_with_earnings;

    // Recent closures table — sort the already-sliced (latest 6) rows client-side.
    const { sorted: sortedLocks, sort: lockSort, toggle: lockToggle } = useTableSort(period_locks.slice(0, 6));

    return (
        <div className="space-y-4">
            <h2 className="text-sm font-semibold uppercase tracking-wider text-muted-foreground">
                Compliance &amp; finance
            </h2>

            <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
                {/* T4A snapshot — drivers bucketed against the CRA reporting +
                    GST registration thresholds. The over_30k bucket is the
                    operational lever — those drivers MUST register for GST/HST. */}
                <Card className="border-border/50">
                    <CardHeader className="pb-2">
                        <CardTitle className="text-sm flex items-center gap-2">
                            <Receipt className="h-4 w-4 text-muted-foreground" />
                            T4A snapshot · {t4a_snapshot.tax_year}
                        </CardTitle>
                    </CardHeader>
                    <CardContent className="space-y-3">
                        <div className="grid grid-cols-2 gap-2 text-xs">
                            <div className="rounded-md bg-muted/30 p-2.5">
                                <p className="text-[10px] uppercase tracking-wide text-muted-foreground font-semibold">Drivers w/ earnings YTD</p>
                                <p className="text-xl font-bold tabular-nums mt-0.5">{totalBucketed.toLocaleString()}</p>
                            </div>
                            <div className="rounded-md bg-muted/30 p-2.5">
                                <p className="text-[10px] uppercase tracking-wide text-muted-foreground font-semibold">YTD gross earnings</p>
                                <p className="text-xl font-bold tabular-nums mt-0.5">{fmtMoney(t4a_snapshot.ytd_gross_earnings)}</p>
                            </div>
                        </div>
                        <Table>
                            <TableHeader>
                                <TableRow>
                                    <TableHead className="text-[11px] uppercase tracking-wide h-9">Bucket</TableHead>
                                    <TableHead className="text-[11px] uppercase tracking-wide h-9 text-right">Drivers</TableHead>
                                    <TableHead className="text-[11px] uppercase tracking-wide h-9">CRA implication</TableHead>
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                <TableRow>
                                    <TableCell className="text-xs font-mono">&lt; $500</TableCell>
                                    <TableCell className="text-xs text-right tabular-nums">{t4a_snapshot.buckets.under_500}</TableCell>
                                    <TableCell className="text-[11px] text-muted-foreground">Below T4A reporting threshold</TableCell>
                                </TableRow>
                                <TableRow>
                                    <TableCell className="text-xs font-mono">$500 – $10k</TableCell>
                                    <TableCell className="text-xs text-right tabular-nums">{t4a_snapshot.buckets.from_500_to_10k}</TableCell>
                                    <TableCell className="text-[11px] text-muted-foreground">T4A required</TableCell>
                                </TableRow>
                                <TableRow>
                                    <TableCell className="text-xs font-mono">$10k – $30k</TableCell>
                                    <TableCell className="text-xs text-right tabular-nums">{t4a_snapshot.buckets.from_10k_to_30k}</TableCell>
                                    <TableCell className="text-[11px] text-muted-foreground">T4A required · GST elective</TableCell>
                                </TableRow>
                                <TableRow className="bg-warning/5">
                                    <TableCell className="text-xs font-mono font-semibold">≥ $30k</TableCell>
                                    <TableCell className="text-xs text-right tabular-nums font-bold text-warning">{t4a_snapshot.buckets.over_30k}</TableCell>
                                    <TableCell className="text-[11px] text-warning">T4A + GST/HST registration MANDATORY</TableCell>
                                </TableRow>
                            </TableBody>
                        </Table>
                    </CardContent>
                </Card>

                {/* Period close — picks a month, writes an audit_log row.
                    Closure is advisory at this scale, not a hard lock — the
                    log answers "did we sign off on May 2026?" for the
                    accountant without requiring schema changes. */}
                <Card className="border-border/50">
                    <CardHeader className="pb-2">
                        <CardTitle className="text-sm flex items-center gap-2">
                            <CheckCircle className="h-4 w-4 text-muted-foreground" />
                            Period close
                        </CardTitle>
                    </CardHeader>
                    <CardContent className="space-y-3">
                        <p className="text-[11px] text-muted-foreground">
                            Writes an audit-log entry snapshotting every completed payout in the selected month — the
                            accountant&apos;s "we signed off on this period" record. Restricted to finance + super_admin.
                        </p>
                        <div className="flex items-end gap-2 flex-wrap">
                            <div>
                                <Label htmlFor="close-month" className="text-[10px] uppercase tracking-wide text-muted-foreground font-semibold mb-1.5 block">Month</Label>
                                <select
                                    id="close-month"
                                    className="h-9 text-sm rounded-md border border-input bg-background px-2"
                                    value={closeMonth}
                                    onChange={(e) => setCloseMonth(Number(e.target.value))}
                                >
                                    {Array.from({ length: 12 }).map((_, i) => (
                                        <option key={i + 1} value={i + 1}>
                                            {new Date(2000, i, 1).toLocaleString(undefined, { month: "long" })}
                                        </option>
                                    ))}
                                </select>
                            </div>
                            <div>
                                <Label htmlFor="close-year" className="text-[10px] uppercase tracking-wide text-muted-foreground font-semibold mb-1.5 block">Year</Label>
                                <Input
                                    id="close-year"
                                    type="number"
                                    min={2024}
                                    max={now.getFullYear()}
                                    value={closeYear}
                                    onChange={(e) => setCloseYear(Number(e.target.value))}
                                    className="h-9 w-[100px] text-sm"
                                />
                            </div>
                            <Button
                                onClick={handleClose}
                                disabled={closing}
                                size="sm"
                                // eslint-disable-next-line no-restricted-syntax -- solid-fill white-text success button; --success fails WCAG AA contrast vs white text in dark mode (#2816)
                                className="bg-emerald-600 hover:bg-emerald-700 text-white"
                            >
                                <CheckCircle className="h-3.5 w-3.5 mr-1.5" />
                                {closing ? "Closing…" : `Close ${fmtPeriodKey(`${closeYear}-${String(closeMonth).padStart(2, "0")}`)}`}
                            </Button>
                        </div>

                        {period_locks.length > 0 && (
                            <div className="pt-2">
                                <p className="text-[10px] uppercase tracking-wide text-muted-foreground font-semibold mb-1.5">Recent closures</p>
                                <Table>
                                    <TableHeader>
                                        <TableRow>
                                            <SortableHead column="period" sort={lockSort} onSort={lockToggle} className="text-[11px] uppercase tracking-wide h-9">Period</SortableHead>
                                            <SortableHead column="closed_at" sort={lockSort} onSort={lockToggle} className="text-[11px] uppercase tracking-wide h-9">Closed at</SortableHead>
                                            <SortableHead column="closed_by" sort={lockSort} onSort={lockToggle} className="text-[11px] uppercase tracking-wide h-9">By</SortableHead>
                                        </TableRow>
                                    </TableHeader>
                                    <TableBody>
                                        {sortedLocks.map((lock) => (
                                            <TableRow key={`${lock.period}-${lock.closed_at}`}>
                                                <TableCell className="text-xs font-medium">{fmtPeriodKey(lock.period)}</TableCell>
                                                <TableCell className="text-xs text-muted-foreground">{formatDate(lock.closed_at)}</TableCell>
                                                <TableCell className="text-xs text-muted-foreground font-mono truncate max-w-[140px]" title={lock.closed_by || ""}>
                                                    {lock.closed_by ? lock.closed_by.slice(0, 12) + "…" : "—"}
                                                </TableCell>
                                            </TableRow>
                                        ))}
                                    </TableBody>
                                </Table>
                            </div>
                        )}
                    </CardContent>
                </Card>
            </div>
        </div>
    );
}
