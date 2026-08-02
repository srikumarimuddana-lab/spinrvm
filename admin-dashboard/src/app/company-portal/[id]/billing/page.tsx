"use client";

import { useCallback, useEffect, useState, type ReactNode } from "react";
import { useParams } from "next/navigation";
import type {
    BillingStatement,
    BillingSummary,
    BillingTransactionsPage,
} from "@/lib/api";
import {
    fetchCompanyStatementPdfBlob,
    getCompanyBillingStatement,
    getCompanyBillingSummary,
    getCompanyBillingTransactions,
    selfServeWalletTopup,
} from "@/lib/companyApi";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog";
import {
    Table,
    TableBody,
    TableCell,
    TableHeader,
    TableRow,
} from "@/components/ui/table";
import { Download, FileText, Plus } from "lucide-react";
import { sanitizeCsvCell } from "@/lib/export-csv";
import { useTableSort, SortableHead } from "@/components/ui/sortable-table";
import { useToast } from "@/components/ui/use-toast";

function monthOptions(): string[] {
    const out: string[] = [];
    const now = new Date();
    for (let i = 0; i < 12; i++) {
        const d = new Date(now.getFullYear(), now.getMonth() - i, 1);
        out.push(`${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`);
    }
    return out;
}

function formatCAD(n: number | undefined) {
    return (n ?? 0).toLocaleString("en-CA", {
        style: "currency",
        currency: "CAD",
    });
}

function toCSV(statement: BillingStatement): string {
    const cols = [
        "ride_id",
        "member_id",
        "source_type",
        "allowance_debit_amount",
        "master_fallback_amount",
        "tax_amount",
        "policy_check_result",
        "created_at",
    ];
    const serializeCell = (cell: unknown): string => {
        let val = cell === null || cell === undefined ? "" : String(cell);
        val = sanitizeCsvCell(val);
        return `"${val.replace(/"/g, '""')}"`;
    };
    const header = cols.map((h) => `"${h}"`).join(",");
    const body = statement.line_items
        .map((r) =>
            [
                r.ride_id,
                r.member_id,
                r.source_type,
                r.allowance_debit_amount,
                r.master_fallback_amount,
                r.tax_amount ?? "",
                r.policy_check_result ?? "",
                r.created_at,
            ]
                .map(serializeCell)
                .join(","),
        )
        .join("\n");
    return `${header}\n${body}`;
}

export default function BillingPage() {
    const { id } = useParams<{ id: string }>();
    const { toast } = useToast();
    const months = monthOptions();
    const [month, setMonth] = useState<string>(months[0]);
    const [summary, setSummary] = useState<BillingSummary | null>(null);
    const [statement, setStatement] = useState<BillingStatement | null>(null);
    const [txns, setTxns] = useState<BillingTransactionsPage | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [topupOpen, setTopupOpen] = useState(false);
    const [topupAmount, setTopupAmount] = useState("");
    const [toppingUp, setToppingUp] = useState(false);

    const load = useCallback(async () => {
        if (!id) return;
        setLoading(true);
        setError(null);
        try {
            const [s, st, t] = await Promise.all([
                getCompanyBillingSummary(id, month),
                getCompanyBillingStatement(id, month),
                getCompanyBillingTransactions(id, 0, 50).catch(() => null),
            ]);
            setSummary(s);
            setStatement(st);
            setTxns(t);
        } catch (e) {
            setError(e instanceof Error ? e.message : "Failed to load");
        } finally {
            setLoading(false);
        }
    }, [id, month]);

    useEffect(() => {
        load();
    }, [load]);

    const downloadCSV = () => {
        if (!statement) return;
        const blob = new Blob([toCSV(statement)], { type: "text/csv" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `spinr-statement-${month}.csv`;
        a.click();
        URL.revokeObjectURL(url);
    };

    const [downloadingPdf, setDownloadingPdf] = useState(false);

    const downloadPDF = async () => {
        if (!id) return;
        setDownloadingPdf(true);
        try {
            const blob = await fetchCompanyStatementPdfBlob(id, month);
            const url = URL.createObjectURL(blob);
            const a = document.createElement("a");
            a.href = url;
            a.download = `spinr-corporate-statement-${month}.pdf`;
            a.click();
            URL.revokeObjectURL(url);
        } catch (e) {
            toast({
                title: "Failed to download invoice",
                description: e instanceof Error ? e.message : "Could not generate the PDF.",
                variant: "destructive",
            });
        } finally {
            setDownloadingPdf(false);
        }
    };

    const byMember = useTableSort(summary?.by_member ?? []);
    const ledger = useTableSort(txns?.transactions ?? []);

    async function handleTopup() {
        if (!id) return;
        const amount = Number(topupAmount);
        if (!amount || amount < 100 || amount > 10000) {
            toast({
                title: "Invalid amount",
                description: "Enter an amount between $100 and $10,000.",
                variant: "destructive",
            });
            return;
        }
        setToppingUp(true);
        try {
            await selfServeWalletTopup(id, amount);
            toast({
                title: "Top-up submitted",
                description: "Charging your card on file. Your balance updates once the payment completes.",
            });
            setTopupOpen(false);
            setTopupAmount("");
            await load();
        } catch (e) {
            toast({
                title: "Top-up failed",
                description: e instanceof Error ? e.message : "Could not charge your card on file.",
                variant: "destructive",
            });
        } finally {
            setToppingUp(false);
        }
    }

    return (
        <div className="space-y-6">
            <header className="flex flex-wrap items-center justify-between gap-2">
                <div>
                    <h1 className="text-2xl font-semibold">Billing</h1>
                    <p className="text-muted-foreground">
                        Month-to-date spend, monthly statements, wallet ledger.
                    </p>
                </div>
                <div className="flex items-center gap-2">
                    <select
                        className="rounded border border-input bg-background px-2 py-1.5 text-sm"
                        value={month}
                        onChange={(e) => setMonth(e.target.value)}
                    >
                        {months.map((m) => (
                            <option key={m} value={m}>
                                {m}
                            </option>
                        ))}
                    </select>
                    <Button
                        size="sm"
                        variant="outline"
                        onClick={downloadCSV}
                        disabled={!statement}
                    >
                        <Download className="mr-1 h-4 w-4" /> CSV
                    </Button>
                    <Button
                        size="sm"
                        variant="outline"
                        onClick={downloadPDF}
                        disabled={downloadingPdf}
                    >
                        <FileText className="mr-1 h-4 w-4" /> {downloadingPdf ? "Generating…" : "Invoice PDF"}
                    </Button>
                </div>
            </header>

            {error && (
                <p className="rounded bg-red-50 p-3 text-sm text-red-700">{error}</p>
            )}

            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                <Metric
                    label="Wallet balance"
                    value={formatCAD(summary?.wallet_balance)}
                    action={
                        <Button size="sm" variant="outline" onClick={() => setTopupOpen(true)}>
                            <Plus className="mr-1 h-3.5 w-3.5" /> Top up
                        </Button>
                    }
                />
                <Metric
                    label="Total spend"
                    value={formatCAD(summary?.total)}
                    sub={`${summary?.ride_count ?? 0} rides`}
                />
                <Metric
                    label="Allowance"
                    value={formatCAD(summary?.allowance_total)}
                    sub="Member allowances"
                />
                <Metric
                    label="Master fallback"
                    value={formatCAD(summary?.master_total)}
                    sub="Overflow debits"
                />
                {/* Corporate + admin portal review, round 2: "no GST/PST
                    breakdown on corporate statements" — for input-tax-credit
                    reconciliation. */}
                <Metric
                    label="Tax (GST/PST)"
                    value={formatCAD(summary?.tax_total)}
                    sub={
                        summary?.tax_by_type && Object.keys(summary.tax_by_type).length > 0
                            ? Object.entries(summary.tax_by_type)
                                  .map(([label, amount]) => `${label} ${formatCAD(amount)}`)
                                  .join(" · ")
                            : "Included in totals above"
                    }
                />
            </div>

            <Card>
                <CardContent className="p-4">
                    <h2 className="mb-3 font-medium">By member</h2>
                    <Table>
                        <TableHeader>
                            <TableRow>
                                <SortableHead column="member_id" sort={byMember.sort} onSort={byMember.toggle}>Member</SortableHead>
                                <SortableHead column="ride_count" sort={byMember.sort} onSort={byMember.toggle}>Rides</SortableHead>
                                <SortableHead column="allowance_total" sort={byMember.sort} onSort={byMember.toggle}>Allowance</SortableHead>
                                <SortableHead column="master_total" sort={byMember.sort} onSort={byMember.toggle}>Master</SortableHead>
                                <SortableHead column="total" sort={byMember.sort} onSort={byMember.toggle} align="right">Total</SortableHead>
                            </TableRow>
                        </TableHeader>
                        <TableBody>
                            {loading && (
                                <TableRow>
                                    <TableCell colSpan={5} className="text-center text-muted-foreground">
                                        Loading…
                                    </TableCell>
                                </TableRow>
                            )}
                            {!loading && (summary?.by_member?.length ?? 0) === 0 && (
                                <TableRow>
                                    <TableCell colSpan={5} className="text-center text-muted-foreground">
                                        No work rides for this month.
                                    </TableCell>
                                </TableRow>
                            )}
                            {byMember.sorted.map((m) => (
                                <TableRow key={m.member_id}>
                                    <TableCell className="font-mono text-xs">
                                        {m.member_id}
                                    </TableCell>
                                    <TableCell>{m.ride_count}</TableCell>
                                    <TableCell>{formatCAD(m.allowance_total)}</TableCell>
                                    <TableCell>{formatCAD(m.master_total)}</TableCell>
                                    <TableCell className="text-right font-medium">
                                        {formatCAD(m.total)}
                                    </TableCell>
                                </TableRow>
                            ))}
                        </TableBody>
                    </Table>
                </CardContent>
            </Card>

            <Card>
                <CardContent className="p-4">
                    <h2 className="mb-3 font-medium">Wallet ledger</h2>
                    <Table>
                        <TableHeader>
                            <TableRow>
                                <SortableHead column="created_at" sort={ledger.sort} onSort={ledger.toggle}>Date</SortableHead>
                                <SortableHead column="type" sort={ledger.sort} onSort={ledger.toggle}>Type</SortableHead>
                                <SortableHead column="notes" sort={ledger.sort} onSort={ledger.toggle}>Notes</SortableHead>
                                <SortableHead column="amount" sort={ledger.sort} onSort={ledger.toggle} align="right">Amount</SortableHead>
                            </TableRow>
                        </TableHeader>
                        <TableBody>
                            {!txns && !loading && (
                                <TableRow>
                                    <TableCell colSpan={4} className="text-center text-muted-foreground">
                                        No wallet configured.
                                    </TableCell>
                                </TableRow>
                            )}
                            {txns?.transactions.length === 0 && (
                                <TableRow>
                                    <TableCell colSpan={4} className="text-center text-muted-foreground">
                                        No transactions.
                                    </TableCell>
                                </TableRow>
                            )}
                            {ledger.sorted.map((t) => (
                                <TableRow key={t.id}>
                                    <TableCell className="text-xs text-muted-foreground">
                                        {t.created_at?.slice(0, 10)}
                                    </TableCell>
                                    <TableCell>
                                        <Badge variant="secondary" className="text-[10px]">
                                            {t.type}
                                        </Badge>
                                    </TableCell>
                                    <TableCell className="max-w-[40ch] truncate text-xs">
                                        {t.notes ?? ""}
                                    </TableCell>
                                    <TableCell className="text-right font-medium">
                                        {formatCAD(t.amount)}
                                    </TableCell>
                                </TableRow>
                            ))}
                        </TableBody>
                    </Table>
                </CardContent>
            </Card>

            <Dialog open={topupOpen} onOpenChange={(o) => { if (!o) setTopupOpen(false); }}>
                <DialogContent>
                    <DialogHeader>
                        <DialogTitle>Top up wallet</DialogTitle>
                        <DialogDescription>
                            Charges your company&apos;s card on file. Between $100 and $10,000 CAD.
                        </DialogDescription>
                    </DialogHeader>
                    <div className="space-y-2">
                        <div className="flex items-center gap-2">
                            <span className="text-muted-foreground">$</span>
                            <Input
                                type="number"
                                min={100}
                                max={10000}
                                step={1}
                                placeholder="e.g. 500"
                                value={topupAmount}
                                onChange={(e) => setTopupAmount(e.target.value)}
                            />
                        </div>
                    </div>
                    <DialogFooter>
                        <Button variant="outline" onClick={() => setTopupOpen(false)} disabled={toppingUp}>
                            Cancel
                        </Button>
                        <Button onClick={handleTopup} disabled={toppingUp || !topupAmount}>
                            {toppingUp ? "Charging…" : "Top up"}
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </div>
    );
}

function Metric({
    label,
    value,
    sub,
    action,
}: {
    label: string;
    value: string;
    sub?: string;
    action?: ReactNode;
}) {
    return (
        <Card>
            <CardContent className="p-4">
                <div className="flex items-start justify-between gap-2">
                    <div className="text-xs text-muted-foreground">{label}</div>
                    {action}
                </div>
                <div className="text-2xl font-semibold">{value}</div>
                {sub && <div className="text-xs text-muted-foreground">{sub}</div>}
            </CardContent>
        </Card>
    );
}
