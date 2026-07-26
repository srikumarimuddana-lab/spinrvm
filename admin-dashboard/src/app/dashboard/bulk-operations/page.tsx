"use client";

/**
 * Bulk Operations — super-admin-only home for CSV-driven data tools.
 *
 * Strictly role === "super_admin" (AI-console pattern): the sidebar entry uses
 * a module no staff role grants, this page renders a denial card for anyone
 * else, and the backend re-checks the role on every endpoint (403).
 *
 * First tool: the legacy Stripe mapping import (validate → commit → KYC
 * status). Future bulk tools (rider details upload, driver/customer updates)
 * should be added as further TOOLS entries + sections on this page, keeping
 * the validate-first / dry-run-report contract.
 */

import { useRef, useState } from "react";
import Link from "next/link";
import {
    Upload,
    FileDown,
    CheckCircle2,
    AlertTriangle,
    Loader2,
    Info,
    ShieldAlert,
    RefreshCw,
    CreditCard,
} from "lucide-react";
import {
    adminValidateStripeImport,
    adminCommitStripeImport,
    adminStripeImportStatus,
    type StripeImportKind,
    type StripeImportReport,
    type StripeImportReportItem,
    type StripeImportStatus,
} from "@/lib/api";
import { useAuthStore } from "@/store/authStore";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
    Card,
    CardHeader,
    CardTitle,
    CardDescription,
    CardContent,
} from "@/components/ui/card";
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select";
import {
    Table,
    TableHeader,
    TableBody,
    TableHead,
    TableRow,
    TableCell,
} from "@/components/ui/table";
import { useToast } from "@/components/ui/use-toast";
import { exportToCsv } from "@/lib/export-csv";

// Per-kind CSV contract. Keep in sync with
// backend/services/stripe_mapping_import_service.py (parse_mapping_rows) and
// docs/runbooks/stripe-legacy-migration.md.
const KIND_CONFIG: Record<
    StripeImportKind,
    { label: string; columns: string; header: string[]; sample: string[] }
> = {
    drivers: {
        label: "Drivers → Stripe Connect accounts (payouts)",
        columns:
            "stripe_account_id (acct_…, required) + old_driver_id and/or phone. Optional: old_stripe_account_id.",
        header: ["old_driver_id", "phone", "stripe_account_id", "old_stripe_account_id"],
        sample: ["LEGACY-001", "3065551234", "acct_1AbC2dEfG3hIj", ""],
    },
    riders: {
        label: "Riders → Stripe customers (saved cards)",
        columns:
            "stripe_customer_id (cus_…, required) + phone and/or email. Optional: old_stripe_customer_id, old_user_id.",
        header: ["phone", "email", "stripe_customer_id", "old_stripe_customer_id", "old_user_id"],
        sample: ["3065559999", "rider@example.com", "cus_AbC123dEf", "", "U-1001"],
    },
};

function downloadTemplate(kind: StripeImportKind) {
    const cfg = KIND_CONFIG[kind];
    const csv = `${cfg.header.join(",")}\n${cfg.sample.join(",")}\n`;
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `spinr-stripe-mapping-${kind}-template.csv`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
}

const REPORT_COLUMNS = [
    { key: "row_ref", label: "row_ref" },
    { key: "field", label: "field" },
    { key: "message", label: "message" },
];

function IssueTable({ items }: { items: StripeImportReportItem[] }) {
    return (
        <div className="overflow-x-auto rounded-md border">
            <Table>
                <TableHeader>
                    <TableRow>
                        <TableHead className="w-40">Row</TableHead>
                        <TableHead className="w-48">Field</TableHead>
                        <TableHead>Message</TableHead>
                    </TableRow>
                </TableHeader>
                <TableBody>
                    {items.map((it, i) => (
                        <TableRow key={`${it.row_ref}-${it.field}-${i}`}>
                            <TableCell className="font-mono text-xs">{it.row_ref}</TableCell>
                            <TableCell className="font-mono text-xs">{it.field}</TableCell>
                            <TableCell className="text-sm">{it.message}</TableCell>
                        </TableRow>
                    ))}
                </TableBody>
            </Table>
        </div>
    );
}

export default function BulkOperationsPage() {
    const user = useAuthStore((s) => s.user);
    const { toast } = useToast();
    const fileInputRef = useRef<HTMLInputElement>(null);

    const [kind, setKind] = useState<StripeImportKind>("drivers");
    const [file, setFile] = useState<File | null>(null);
    const [batch, setBatch] = useState("");
    const [report, setReport] = useState<StripeImportReport | null>(null);
    const [validating, setValidating] = useState(false);
    const [committing, setCommitting] = useState(false);
    const [committedSummary, setCommittedSummary] = useState<string | null>(null);
    const [statusBatch, setStatusBatch] = useState<string | null>(null);
    const [status, setStatus] = useState<StripeImportStatus | null>(null);
    const [statusLoading, setStatusLoading] = useState(false);

    const isSuperAdmin = user?.role === "super_admin";
    if (!isSuperAdmin) {
        return (
            <div className="mx-auto flex max-w-md flex-col items-center gap-3 p-12 text-center">
                <ShieldAlert className="h-10 w-10 text-muted-foreground" />
                <h2 className="text-lg font-semibold">Super admin only</h2>
                <p className="text-sm text-muted-foreground">
                    Bulk Operations can change payout destinations and billing identities, so it
                    requires the super admin role.
                </p>
            </div>
        );
    }

    const resetReport = () => {
        setReport(null);
        setCommittedSummary(null);
    };

    const onPickFile = (f: File | null) => {
        setFile(f);
        resetReport();
    };

    const onPickKind = (k: StripeImportKind) => {
        setKind(k);
        resetReport();
    };

    const handleValidate = async () => {
        if (!file) return;
        setValidating(true);
        setCommittedSummary(null);
        try {
            const rep = await adminValidateStripeImport(file, kind, batch || undefined);
            setReport(rep);
        } catch (e) {
            toast({
                title: "Validation failed",
                description: e instanceof Error ? e.message : "Could not validate the CSV",
                variant: "destructive",
            });
        } finally {
            setValidating(false);
        }
    };

    const refreshStatus = async (b: string) => {
        setStatusLoading(true);
        try {
            setStatus(await adminStripeImportStatus(b));
        } catch (e) {
            toast({
                title: "Status unavailable",
                description: e instanceof Error ? e.message : "Could not load batch status",
                variant: "destructive",
            });
        } finally {
            setStatusLoading(false);
        }
    };

    const handleCommit = async () => {
        if (!file || !report?.can_commit) return;
        setCommitting(true);
        try {
            const res = await adminCommitStripeImport(file, kind, report.batch);
            if (res.committed) {
                const mapped = (res.updated_drivers ?? 0) + (res.updated_users ?? 0);
                setCommittedSummary(
                    `Mapped ${mapped} ${res.kind === "drivers" ? "driver(s)" : "rider(s)"}.` +
                        (res.conflicts?.length
                            ? ` ${res.conflicts.length} row(s) hit commit-time conflicts (see logs).`
                            : "") +
                        (res.warnings?.length ? ` ${res.warnings.length} warning(s).` : ""),
                );
                setReport(null);
                setFile(null);
                if (fileInputRef.current) fileInputRef.current.value = "";
                if (res.kyc_sync === "started") {
                    setStatusBatch(res.batch);
                    void refreshStatus(res.batch);
                }
                toast({ title: "Mapping committed", description: `${mapped} record(s) mapped.` });
            } else {
                // The CSV no longer validates (data changed since validate).
                setReport({
                    batch: res.batch,
                    kind: res.kind,
                    can_commit: false,
                    counts: res.counts ?? { rows: 0, to_map: 0, skipped_already_mapped: 0 },
                    warnings: res.warnings ?? [],
                    errors: res.errors ?? [],
                });
                toast({
                    title: "Commit refused",
                    description: "The CSV has validation errors. Fix them and try again.",
                    variant: "destructive",
                });
            }
        } catch (e) {
            toast({
                title: "Commit failed",
                description: e instanceof Error ? e.message : "Could not commit the mapping",
                variant: "destructive",
            });
        } finally {
            setCommitting(false);
        }
    };

    const counts = report?.counts;
    const cfg = KIND_CONFIG[kind];

    return (
        <div className="mx-auto max-w-4xl space-y-6 p-4">
            <div>
                <h1 className="text-2xl font-semibold">Bulk Operations</h1>
                <p className="text-sm text-muted-foreground">
                    Super-admin CSV tools. Every tool is dry-run first: validate, review the report,
                    then commit. Driver account/profile imports live on the{" "}
                    <Link href="/dashboard/drivers/import" className="underline">
                        Bulk Driver Import
                    </Link>{" "}
                    page.
                </p>
            </div>

            <div className="flex items-center gap-2 text-sm font-medium text-muted-foreground">
                <CreditCard className="h-4 w-4" />
                Stripe Mapping Import — carry legacy Stripe IDs over so drivers keep payout
                accounts and riders keep saved cards
            </div>

            <Card>
                <CardHeader>
                    <CardTitle>1. Prepare your CSV</CardTitle>
                    <CardDescription>{cfg.columns} Max 200 rows per file.</CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                    <div className="grid gap-4 sm:grid-cols-2">
                        <div className="space-y-1">
                            <label htmlFor="mapping-kind" className="text-sm font-medium">
                                What are you mapping?
                            </label>
                            <Select value={kind} onValueChange={(v) => onPickKind(v as StripeImportKind)}>
                                <SelectTrigger id="mapping-kind">
                                    <SelectValue />
                                </SelectTrigger>
                                <SelectContent>
                                    {(Object.keys(KIND_CONFIG) as StripeImportKind[]).map((k) => (
                                        <SelectItem key={k} value={k}>
                                            {KIND_CONFIG[k].label}
                                        </SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                        </div>
                        <div className="flex items-end">
                            <Button variant="outline" onClick={() => downloadTemplate(kind)}>
                                <FileDown className="mr-2 h-4 w-4" />
                                Download CSV template
                            </Button>
                        </div>
                    </div>
                </CardContent>
            </Card>

            <Card>
                <CardHeader>
                    <CardTitle>2. Upload &amp; validate</CardTitle>
                    <CardDescription>
                        Validation is a dry run — every Stripe ID is checked live, nothing is written
                        until you commit.
                    </CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                    <div className="grid gap-4 sm:grid-cols-2">
                        <div className="space-y-1">
                            <label htmlFor="mapping-csv" className="text-sm font-medium">
                                Mapping CSV
                            </label>
                            <Input
                                id="mapping-csv"
                                ref={fileInputRef}
                                type="file"
                                accept=".csv,text/csv"
                                onChange={(e) => onPickFile(e.target.files?.[0] ?? null)}
                            />
                        </div>
                        <div className="space-y-1">
                            <label htmlFor="mapping-batch" className="text-sm font-medium">
                                Batch name (optional)
                            </label>
                            <Input
                                id="mapping-batch"
                                placeholder="e.g. drivers-batch-1"
                                value={batch}
                                onChange={(e) => {
                                    setBatch(e.target.value);
                                    resetReport();
                                }}
                            />
                        </div>
                    </div>
                    <div className="flex items-center gap-3">
                        <Button onClick={handleValidate} disabled={!file || validating || committing}>
                            {validating ? (
                                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                            ) : (
                                <Upload className="mr-2 h-4 w-4" />
                            )}
                            Validate
                        </Button>
                        {file && <span className="text-sm text-muted-foreground">{file.name}</span>}
                    </div>
                </CardContent>
            </Card>

            {committedSummary && (
                <Card className="border-emerald-300 dark:border-emerald-800">
                    <CardContent className="flex items-center gap-3 py-4">
                        <CheckCircle2 className="h-5 w-5 text-emerald-600" />
                        <span className="text-sm">{committedSummary}</span>
                    </CardContent>
                </Card>
            )}

            {report && (
                <Card>
                    <CardHeader>
                        <CardTitle>3. Review &amp; commit</CardTitle>
                        <CardDescription>
                            {report.can_commit
                                ? "No errors — you can commit this mapping."
                                : "Fix the errors below, re-export your CSV, and validate again."}
                        </CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-5">
                        <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
                            <Stat label="Rows" value={counts?.rows ?? 0} />
                            <Stat label="To map" value={counts?.to_map ?? 0} />
                            <Stat
                                label="Skipped (already mapped)"
                                value={counts?.skipped_already_mapped ?? 0}
                            />
                            <Stat label="Warnings" value={report.warnings.length} tone="warn" />
                            <Stat label="Errors" value={report.errors.length} tone="error" />
                        </div>

                        {report.errors.length > 0 && (
                            <div className="space-y-2">
                                <div className="flex items-center justify-between">
                                    <h3 className="flex items-center gap-2 text-sm font-semibold text-red-600">
                                        <AlertTriangle className="h-4 w-4" /> Errors ({report.errors.length})
                                    </h3>
                                    <Button
                                        variant="ghost"
                                        size="sm"
                                        onClick={() =>
                                            exportToCsv("stripe-mapping-errors", report.errors, REPORT_COLUMNS)
                                        }
                                    >
                                        <FileDown className="mr-2 h-4 w-4" />
                                        Download errors
                                    </Button>
                                </div>
                                <IssueTable items={report.errors} />
                            </div>
                        )}

                        {report.warnings.length > 0 && (
                            <div className="space-y-2">
                                <h3 className="flex items-center gap-2 text-sm font-semibold text-amber-600">
                                    <Info className="h-4 w-4" /> Warnings ({report.warnings.length})
                                </h3>
                                <IssueTable items={report.warnings} />
                            </div>
                        )}

                        <div className="flex items-center gap-3 pt-2">
                            <Button onClick={handleCommit} disabled={!report.can_commit || committing}>
                                {committing ? (
                                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                ) : (
                                    <CheckCircle2 className="mr-2 h-4 w-4" />
                                )}
                                Commit mapping
                            </Button>
                            {!report.can_commit && (
                                <span className="text-sm text-muted-foreground">
                                    Resolve all errors to enable commit.
                                </span>
                            )}
                        </div>
                    </CardContent>
                </Card>
            )}

            {statusBatch && (
                <Card>
                    <CardHeader>
                        <CardTitle>4. KYC sync status</CardTitle>
                        <CardDescription>
                            Batch <span className="font-mono">{statusBatch}</span> — each mapped driver&apos;s
                            real Stripe state is being mirrored in the background. Refresh until the
                            counts converge.
                        </CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-4">
                        {status && (
                            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                                <Stat label="Drivers in batch" value={status.drivers} />
                                <Stat label="KYC synced (ok)" value={status.kyc_sync?.ok ?? 0} />
                                <Stat
                                    label="Sync failed"
                                    value={status.kyc_sync?.stripe_error ?? 0}
                                    tone="error"
                                />
                                <Stat label="Payouts enabled" value={status.payouts_enabled} />
                            </div>
                        )}
                        <Button
                            variant="outline"
                            onClick={() => refreshStatus(statusBatch)}
                            disabled={statusLoading}
                        >
                            {statusLoading ? (
                                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                            ) : (
                                <RefreshCw className="mr-2 h-4 w-4" />
                            )}
                            Refresh status
                        </Button>
                    </CardContent>
                </Card>
            )}
        </div>
    );
}

function Stat({
    label,
    value,
    tone,
}: {
    label: string;
    value: number;
    tone?: "warn" | "error";
}) {
    const toneCls =
        tone === "error" && value > 0
            ? "text-red-600"
            : tone === "warn" && value > 0
              ? "text-amber-600"
              : "text-foreground";
    return (
        <div className="rounded-md border p-3">
            <div className={`text-2xl font-semibold ${toneCls}`}>{value}</div>
            <div className="text-xs text-muted-foreground">{label}</div>
        </div>
    );
}
