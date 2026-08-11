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
    Users,
    ArrowRight,
} from "lucide-react";
import {
    adminDiscoverStripeDriverAccounts,
    adminValidateStripeImport,
    adminCommitStripeImport,
    adminStripeImportStatus,
    adminUpdateDriverStripeAccount,
    adminValidateRiderImport,
    adminCommitRiderImport,
    type StripeImportKind,
    type StripeImportReport,
    type StripeImportReportItem,
    type StripeImportNeedsUpdateItem,
    type StripeImportStatus,
    type RiderImportReport,
    type RiderImportReportItem,
    type RiderImportDuplicate,
} from "@/lib/api";
import { LegacyBookingImport } from "./_components/LegacyBookingImport";
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
import {
    AlertDialog,
    AlertDialogTrigger,
    AlertDialogContent,
    AlertDialogHeader,
    AlertDialogTitle,
    AlertDialogDescription,
    AlertDialogFooter,
    AlertDialogCancel,
    AlertDialogAction,
} from "@/components/ui/alert-dialog";
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

/**
 * Drivers who already carry a DIFFERENT Stripe account than the CSV. The bulk
 * commit skips them (it only fills NULL accounts); each is resolved here one at
 * a time. Updating OVERWRITES the payout destination, so every row goes through
 * a confirm dialog. The name lives on the linked driver page — the import report
 * itself is deliberately PII-free (no names), so we key on the operator's CSV
 * row_ref + driver_id and link out.
 */
function NeedsUpdateSection({
    items,
    batch,
}: {
    items: StripeImportNeedsUpdateItem[];
    batch: string;
}) {
    const { toast } = useToast();
    const [done, setDone] = useState<Record<string, true>>({});
    const [busy, setBusy] = useState<string | null>(null);

    const runUpdate = async (it: StripeImportNeedsUpdateItem) => {
        setBusy(it.driver_id);
        try {
            const res = await adminUpdateDriverStripeAccount({
                driver_id: it.driver_id,
                new_stripe_account_id: it.new_stripe_account_id,
                current_stripe_account_id: it.current_stripe_account_id,
                batch,
            });
            setDone((d) => ({ ...d, [it.driver_id]: true }));
            const note = res.warnings?.length
                ? ` Note: ${res.warnings.map((w) => w.message).join("; ")}`
                : "";
            toast({
                title: "Payout account updated",
                description: `Driver now paid to ${it.new_stripe_account_id}.${note}`,
            });
        } catch (e: unknown) {
            toast({
                variant: "destructive",
                title: "Update failed",
                description:
                    e instanceof Error ? e.message : "Could not update this driver's Stripe account.",
            });
        } finally {
            setBusy(null);
        }
    };

    return (
        <div className="space-y-2">
            <h3 className="flex items-center gap-2 text-sm font-semibold text-sky-600">
                <RefreshCw className="h-4 w-4" /> Already mapped — review to update ({items.length})
            </h3>
            <p className="text-xs text-muted-foreground">
                These drivers already have a Stripe account, so the commit above skips them. Updating
                one <strong>redirects that driver&apos;s payouts</strong> to the new account — only do it
                when you mean to. The new account is re-validated against Stripe and the change is logged.
            </p>
            <div className="overflow-x-auto rounded-md border">
                <Table>
                    <TableHeader>
                        <TableRow>
                            <TableHead className="w-32">CSV row</TableHead>
                            <TableHead>Driver</TableHead>
                            <TableHead>Current → New account</TableHead>
                            <TableHead className="w-28 text-right">Action</TableHead>
                        </TableRow>
                    </TableHeader>
                    <TableBody>
                        {items.map((it) => (
                            <TableRow key={it.driver_id}>
                                <TableCell className="font-mono text-xs">{it.row_ref}</TableCell>
                                <TableCell className="text-sm">
                                    <Link
                                        href="/dashboard/drivers"
                                        target="_blank"
                                        className="text-primary underline underline-offset-2"
                                    >
                                        Drivers page ↗
                                    </Link>
                                    <div className="font-mono text-[10px] text-muted-foreground">
                                        {it.driver_id}
                                    </div>
                                </TableCell>
                                <TableCell className="font-mono text-xs">
                                    <span className="inline-flex items-center gap-1">
                                        <span className="text-muted-foreground">
                                            {it.current_stripe_account_id}
                                        </span>
                                        <ArrowRight className="h-3 w-3" />
                                        <span className="font-semibold">{it.new_stripe_account_id}</span>
                                    </span>
                                </TableCell>
                                <TableCell className="text-right">
                                    {done[it.driver_id] ? (
                                        <span className="inline-flex items-center gap-1 text-xs font-medium text-green-600">
                                            <CheckCircle2 className="h-4 w-4" /> Updated
                                        </span>
                                    ) : (
                                        <AlertDialog>
                                            <AlertDialogTrigger asChild>
                                                <Button
                                                    size="sm"
                                                    variant="outline"
                                                    disabled={busy === it.driver_id}
                                                >
                                                    {busy === it.driver_id && (
                                                        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                                    )}
                                                    Update
                                                </Button>
                                            </AlertDialogTrigger>
                                            <AlertDialogContent>
                                                <AlertDialogHeader>
                                                    <AlertDialogTitle>
                                                        Redirect this driver&apos;s payouts?
                                                    </AlertDialogTitle>
                                                    <AlertDialogDescription>
                                                        This changes where driver {it.driver_id} (CSV row{" "}
                                                        {it.row_ref}) is paid. The new account is
                                                        re-validated against Stripe before the switch, and
                                                        the change is logged.
                                                    </AlertDialogDescription>
                                                </AlertDialogHeader>
                                                <div className="rounded-md border bg-muted/40 p-3 font-mono text-xs">
                                                    {it.current_stripe_account_id} →{" "}
                                                    {it.new_stripe_account_id}
                                                </div>
                                                <AlertDialogFooter>
                                                    <AlertDialogCancel>Cancel</AlertDialogCancel>
                                                    <AlertDialogAction onClick={() => runUpdate(it)}>
                                                        Update payout account
                                                    </AlertDialogAction>
                                                </AlertDialogFooter>
                                            </AlertDialogContent>
                                        </AlertDialog>
                                    )}
                                </TableCell>
                            </TableRow>
                        ))}
                    </TableBody>
                </Table>
            </div>
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
    const [discovering, setDiscovering] = useState(false);

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

    const handleDiscover = async () => {
        setDiscovering(true);
        try {
            const rep = await adminDiscoverStripeDriverAccounts();
            const bits = [
                `${rep.matched} matched by email`,
                rep.ambiguous.length ? `${rep.ambiguous.length} ambiguous (skipped)` : null,
                rep.unmatched_drivers ? `${rep.unmatched_drivers} drivers with no matching account` : null,
                rep.matches_without_phone.length
                    ? `${rep.matches_without_phone.length} matched but missing a phone (cannot ride the CSV)`
                    : null,
            ].filter(Boolean).join(" · ");
            if (!rep.csv) {
                toast({ title: "No importable matches", description: bits || "Nothing to download.", variant: "destructive" });
                return;
            }
            // Download the CSV; the operator uploads it below through the same
            // validate → commit flow as a hand-built file. Nothing was written.
            const blob = new Blob([rep.csv], { type: "text/csv" });
            const url = URL.createObjectURL(blob);
            const a = document.createElement("a");
            a.href = url;
            a.download = "drivers_mapping_by_email.csv";
            a.click();
            URL.revokeObjectURL(url);
            toast({ title: "Mapping CSV downloaded", description: `${bits}. Upload it below, validate, then commit.` });
        } catch (e) {
            toast({
                title: "Discovery failed",
                description: e instanceof Error ? e.message : "Could not read Stripe accounts",
                variant: "destructive",
            });
        } finally {
            setDiscovering(false);
        }
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
                const pendingUpdates = report?.needs_update ?? [];
                if (pendingUpdates.length > 0) {
                    setReport({
                        batch: res.batch ?? report.batch,
                        kind: report.kind,
                        can_commit: false,
                        counts: { rows: 0, to_map: 0, skipped_already_mapped: 0, needs_update: pendingUpdates.length },
                        warnings: [],
                        errors: [],
                        needs_update: pendingUpdates,
                    });
                } else {
                    setReport(null);
                }
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
                    counts: res.counts ?? {
                        rows: 0,
                        to_map: 0,
                        skipped_already_mapped: 0,
                        needs_update: 0,
                    },
                    warnings: res.warnings ?? [],
                    errors: res.errors ?? [],
                    needs_update: res.needs_update ?? [],
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
                        <div className="flex items-end gap-2">
                            <Button variant="outline" onClick={() => downloadTemplate(kind)}>
                                <FileDown className="mr-2 h-4 w-4" />
                                Download CSV template
                            </Button>
                            {kind === "drivers" && (
                                <Button variant="outline" onClick={handleDiscover} disabled={discovering}>
                                    {discovering ? (
                                        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                    ) : (
                                        <FileDown className="mr-2 h-4 w-4" />
                                    )}
                                    Find matches by email
                                </Button>
                            )}
                        </div>
                    </div>
                    {kind === "drivers" && (
                        <p className="text-xs text-muted-foreground">
                            &ldquo;Find matches by email&rdquo; reads your Stripe connected accounts and matches
                            them to drivers that have no Stripe account linked, by exact email. It writes
                            nothing — it downloads a pre-filled mapping CSV that you upload and validate below
                            like any other. Ambiguous emails are skipped and reported, never guessed.
                        </p>
                    )}
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
                        <div className="grid grid-cols-2 gap-3 sm:grid-cols-6">
                            <Stat label="Rows" value={counts?.rows ?? 0} />
                            <Stat label="To map" value={counts?.to_map ?? 0} />
                            <Stat
                                label="Skipped (already mapped)"
                                value={counts?.skipped_already_mapped ?? 0}
                            />
                            <Stat label="Needs update" value={counts?.needs_update ?? 0} />
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

                        {report.kind === "drivers" && report.needs_update?.length > 0 && (
                            <NeedsUpdateSection items={report.needs_update} batch={report.batch} />
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

            {/* Rider bulk import has moved to the Data Transfer module (Import
                tab), which also carries documents, ride history, and the
                insurance-period audit trail — not just profile CSV rows.
                RiderImportSection is kept below (unused) rather than deleted
                in case a rollback needs it back quickly. */}
            <Card>
                <CardHeader>
                    <CardTitle>Rider Bulk Import has moved</CardTitle>
                    <CardDescription>
                        Rider import now lives in the Data Transfer module, alongside driver import, export, and SGI
                        compliance forms.
                    </CardDescription>
                </CardHeader>
                <CardContent>
                    <Button asChild>
                        <a href="/dashboard/data-transfer">Go to Data Transfer</a>
                    </Button>
                </CardContent>
            </Card>

            <div className="flex items-center gap-2 text-sm font-medium text-muted-foreground">
                <Upload className="h-4 w-4" />
                Legacy Booking Import — bring completed rides from the previous app into rider
                and driver trip history
            </div>
            <LegacyBookingImport />
        </div>
    );
}

/* ── Rider Bulk Import Section ─────────────────────── */

const RIDER_CSV_HEADER = ["customer_id", "email", "gender", "phone", "ratings", "temp_email", "timeZone"];
const RIDER_CSV_SAMPLE = ["cus_AbC123dEf", "rider@example.com", "female", "+13065551234", "4.5", "", "America/Regina"];

function downloadRiderTemplate() {
    const csv = `${RIDER_CSV_HEADER.join(",")}\n${RIDER_CSV_SAMPLE.join(",")}\n`;
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "spinr-rider-import-template.csv";
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
}

function RiderIssueTable({ items }: { items: RiderImportReportItem[] }) {
    return (
        <div className="overflow-x-auto rounded-md border">
            <Table>
                <TableHeader>
                    <TableRow>
                        <TableHead className="w-20">Row</TableHead>
                        <TableHead className="w-40">Field</TableHead>
                        <TableHead>Message</TableHead>
                    </TableRow>
                </TableHeader>
                <TableBody>
                    {items.map((it, i) => (
                        <TableRow key={`${it.row_num}-${it.field}-${i}`}>
                            <TableCell className="font-mono text-xs">{it.row_num}</TableCell>
                            <TableCell className="font-mono text-xs">{it.field}</TableCell>
                            <TableCell className="text-sm">{it.message}</TableCell>
                        </TableRow>
                    ))}
                </TableBody>
            </Table>
        </div>
    );
}

function DuplicateTable({ items }: { items: RiderImportDuplicate[] }) {
    return (
        <div className="overflow-x-auto rounded-md border">
            <Table>
                <TableHeader>
                    <TableRow>
                        <TableHead className="w-20">Row</TableHead>
                        <TableHead className="w-40">Phone</TableHead>
                        <TableHead className="w-32">Match type</TableHead>
                        <TableHead>User ID</TableHead>
                    </TableRow>
                </TableHeader>
                <TableBody>
                    {items.map((it, i) => (
                        <TableRow key={`${it.row}-${i}`}>
                            <TableCell className="font-mono text-xs">{it.row}</TableCell>
                            <TableCell className="font-mono text-xs">{it.phone}</TableCell>
                            <TableCell>
                                <span
                                    className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${
                                        it.match_type === "protected_skip"
                                            ? "bg-red-100 text-red-800 dark:bg-red-900 dark:text-red-200"
                                            : it.match_type === "driver"
                                              ? "bg-orange-100 text-orange-800 dark:bg-orange-900 dark:text-orange-200"
                                              : "bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-200"
                                    }`}
                                >
                                    {it.match_type === "protected_skip"
                                        ? "Skipped — needs review"
                                        : it.match_type === "driver"
                                          ? "Driver"
                                          : "Existing rider"}
                                </span>
                            </TableCell>
                            <TableCell className="font-mono text-xs">{it.existing_user_id}</TableCell>
                        </TableRow>
                    ))}
                </TableBody>
            </Table>
        </div>
    );
}

function RiderImportSection() {
    const { toast } = useToast();
    const fileInputRef = useRef<HTMLInputElement>(null);

    const [file, setFile] = useState<File | null>(null);
    const [batch, setBatch] = useState("");
    const [report, setReport] = useState<RiderImportReport | null>(null);
    const [validating, setValidating] = useState(false);
    const [committing, setCommitting] = useState(false);
    const [committedSummary, setCommittedSummary] = useState<string | null>(null);

    const resetReport = () => {
        setReport(null);
        setCommittedSummary(null);
    };

    const handleValidate = async () => {
        if (!file) return;
        setValidating(true);
        setCommittedSummary(null);
        try {
            const rep = await adminValidateRiderImport(file, batch || undefined);
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

    const handleCommit = async () => {
        if (!file || !report?.can_commit) return;
        setCommitting(true);
        try {
            const res = await adminCommitRiderImport(file, report.batch);
            if (res.committed) {
                const created = res.created_users ?? 0;
                const updated = res.updated_users ?? 0;
                const protectedSkips = res.duplicates?.filter((d) => d.match_type === "protected_skip").length ?? 0;
                setCommittedSummary(
                    `Created ${created} rider(s), updated ${updated} existing user(s).` +
                        (res.duplicates?.length
                            ? ` ${res.duplicates.length} duplicate(s) detected (${res.duplicates.filter((d) => d.match_type === "driver").length} are drivers).`
                            : "") +
                        (protectedSkips
                            ? ` ${protectedSkips} row(s) skipped — matched a pending-deletion/deleted account and require manual review.`
                            : ""),
                );
                setReport(null);
                setFile(null);
                if (fileInputRef.current) fileInputRef.current.value = "";
                toast({ title: "Rider import committed", description: `${created + updated} record(s) processed.` });
            } else {
                setReport({
                    batch: res.batch,
                    can_commit: false,
                    counts: res.counts ?? {
                        rows: 0,
                        to_create: 0,
                        to_update: 0,
                        duplicates: 0,
                        duplicate_drivers: 0,
                        protected_skips: 0,
                    },
                    duplicates: res.duplicates ?? [],
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
                description: e instanceof Error ? e.message : "Could not commit the import",
                variant: "destructive",
            });
        } finally {
            setCommitting(false);
        }
    };

    const counts = report?.counts;

    return (
        <>
            <div className="flex items-center gap-2 pt-6 text-sm font-medium text-muted-foreground">
                <Users className="h-4 w-4" />
                Bulk Rider Import — create rider accounts from a CSV with phone-number duplicate
                detection against existing users and drivers
            </div>

            <Card>
                <CardHeader>
                    <CardTitle>1. Prepare your rider CSV</CardTitle>
                    <CardDescription>
                        Columns: customer_id (Stripe cus_…), email, gender, phone (required), ratings,
                        temp_email, timeZone. You may also include name / first_name / last_name. Max
                        500 rows per file.
                    </CardDescription>
                </CardHeader>
                <CardContent>
                    <Button variant="outline" onClick={downloadRiderTemplate}>
                        <FileDown className="mr-2 h-4 w-4" />
                        Download CSV template
                    </Button>
                </CardContent>
            </Card>

            <Card>
                <CardHeader>
                    <CardTitle>2. Upload &amp; validate</CardTitle>
                    <CardDescription>
                        Validation is a dry run — phones are checked against existing users and drivers,
                        nothing is written until you commit.
                    </CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                    <div className="grid gap-4 sm:grid-cols-2">
                        <div className="space-y-1">
                            <label htmlFor="rider-csv" className="text-sm font-medium">
                                Rider CSV
                            </label>
                            <Input
                                id="rider-csv"
                                ref={fileInputRef}
                                type="file"
                                accept=".csv,text/csv"
                                onChange={(e) => {
                                    setFile(e.target.files?.[0] ?? null);
                                    resetReport();
                                }}
                            />
                        </div>
                        <div className="space-y-1">
                            <label htmlFor="rider-batch" className="text-sm font-medium">
                                Batch name (optional)
                            </label>
                            <Input
                                id="rider-batch"
                                placeholder="e.g. riders-batch-1"
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
                                ? "No errors — you can commit this import. Duplicates will be updated, new riders will be created."
                                : "Fix the errors below, re-export your CSV, and validate again."}
                        </CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-5">
                        <div className="grid grid-cols-2 gap-3 sm:grid-cols-6">
                            <Stat label="Rows" value={counts?.rows ?? 0} />
                            <Stat label="New riders" value={counts?.to_create ?? 0} />
                            <Stat label="To update" value={counts?.to_update ?? 0} />
                            <Stat label="Duplicate (driver)" value={counts?.duplicate_drivers ?? 0} tone="warn" />
                            {/* P0-C: rows matched to a pending_deletion/deleted account — PII
                                left untouched, needs manual admin review before importing. */}
                            <Stat label="Needs review" value={counts?.protected_skips ?? 0} tone="error" />
                            <Stat label="Errors" value={report.errors.length} tone="error" />
                        </div>

                        {report.duplicates.length > 0 && (
                            <div className="space-y-2">
                                <h3 className="flex items-center gap-2 text-sm font-semibold text-amber-600">
                                    <Info className="h-4 w-4" /> Phone duplicates ({report.duplicates.length})
                                </h3>
                                <p className="text-xs text-muted-foreground">
                                    These phone numbers already exist in the system. Riders will have their
                                    Stripe customer ID and missing fields updated. Drivers are flagged — they
                                    already have accounts.
                                </p>
                                <DuplicateTable items={report.duplicates} />
                            </div>
                        )}

                        {report.errors.length > 0 && (
                            <div className="space-y-2">
                                <h3 className="flex items-center gap-2 text-sm font-semibold text-red-600">
                                    <AlertTriangle className="h-4 w-4" /> Errors ({report.errors.length})
                                </h3>
                                <RiderIssueTable items={report.errors} />
                            </div>
                        )}

                        {report.warnings.length > 0 && (
                            <div className="space-y-2">
                                <h3 className="flex items-center gap-2 text-sm font-semibold text-amber-600">
                                    <Info className="h-4 w-4" /> Warnings ({report.warnings.length})
                                </h3>
                                <RiderIssueTable items={report.warnings} />
                            </div>
                        )}

                        <div className="flex items-center gap-3 pt-2">
                            <Button onClick={handleCommit} disabled={!report.can_commit || committing}>
                                {committing ? (
                                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                ) : (
                                    <CheckCircle2 className="mr-2 h-4 w-4" />
                                )}
                                Commit rider import
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
        </>
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
