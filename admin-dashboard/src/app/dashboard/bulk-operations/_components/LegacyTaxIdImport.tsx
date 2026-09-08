"use client";

/**
 * Legacy Tax-ID (SIN + GST/HST BN) Backfill — one CSV, header exactly
 * "phone,sin,gst_bn", matched against already-legacy-imported drivers by
 * phone. Mirrors the validate → review → commit shape every other bulk
 * tool on this page uses; no typed confirmation phrase, matching the
 * sibling SIN/DOB backfill page's own convention (the NULL-only fill
 * policy plus the backend's compare-and-set write already make an
 * accidental re-commit a no-op, not a silent overwrite). It does, however,
 * get that same sibling's AlertDialog gut-check before the first commit —
 * this was the one bulk tool on this page with neither a typed-confirm
 * phrase nor a dialog, and it writes vault-encrypted SIN plus a background
 * push to Stripe, which a mis-click shouldn't trigger unconfirmed.
 */

import { useState } from "react";
import { AlertTriangle, CheckCircle2, Copy, Info, Loader2, Upload } from "lucide-react";
import {
    adminCommitTaxIdBackfill,
    adminValidateTaxIdBackfill,
    type TaxIdBackfillCommitResult,
    type TaxIdBackfillReport,
    type TaxIdBackfillReportItem,
} from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
    Card,
    CardContent,
    CardDescription,
    CardHeader,
    CardTitle,
} from "@/components/ui/card";
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
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

function IssueTable({ items }: { items: TaxIdBackfillReportItem[] }) {
    return (
        <div className="overflow-x-auto rounded-md border">
            <Table>
                <TableHeader>
                    <TableRow>
                        <TableHead className="w-40">Row</TableHead>
                        <TableHead className="w-32">Field</TableHead>
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

function Stat({ label, value, tone }: { label: string; value: number; tone?: "warn" | "error" }) {
    const toneCls =
        tone === "error" && value > 0
            ? "text-destructive"
            : tone === "warn" && value > 0
              ? "text-warning"
              : "text-foreground";
    return (
        <div className="rounded-md border p-3">
            <div className={`text-2xl font-semibold ${toneCls}`}>{value}</div>
            <div className="text-xs text-muted-foreground">{label}</div>
        </div>
    );
}

// Compact, copy-pasteable markdown table mirroring the stat tiles below —
// counts only, never a raw SIN or GST BN.
function buildSummaryText(report: TaxIdBackfillReport): string {
    const c = report.counts;
    const rows: [string, number][] = [
        ["Rows", c.rows],
        ["To write", c.to_write],
        ["SIN to write", c.sin_to_write],
        ["GST BN to write", c.gst_to_write],
        ["Skipped", c.skipped],
        ["Warnings", report.warnings.length],
        ["Errors", report.errors.length],
    ];
    const lines = [
        `Legacy Tax-ID Backfill — batch ${report.batch}`,
        "| Metric | Count |",
        "|---|---|",
        ...rows.map(([label, value]) => `| ${label} | ${value} |`),
    ];
    return lines.join("\n");
}

export function LegacyTaxIdImport() {
    const { toast } = useToast();

    const [file, setFile] = useState<File | null>(null);
    const [batch, setBatch] = useState("");
    const [report, setReport] = useState<TaxIdBackfillReport | null>(null);
    const [committed, setCommitted] = useState<TaxIdBackfillCommitResult | null>(null);
    const [validating, setValidating] = useState(false);
    const [committing, setCommitting] = useState(false);

    const onPickFile = (f: File | null) => {
        setFile(f);
        setReport(null);
        setCommitted(null);
    };

    const handleValidate = async () => {
        if (!file) return;
        setValidating(true);
        setCommitted(null);
        try {
            setReport(await adminValidateTaxIdBackfill(file, batch || undefined));
        } catch (e) {
            setReport(null);
            toast({
                title: "Validation failed",
                description: e instanceof Error ? e.message : "Could not validate the CSV.",
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
            const res = await adminCommitTaxIdBackfill(file, report.batch);
            setCommitted(res);
            if (!res.committed) {
                // Backend refused and returned the fresh report instead.
                setReport({
                    batch: res.batch,
                    can_commit: res.can_commit ?? false,
                    counts: res.counts ?? report.counts,
                    warnings: res.warnings ?? [],
                    errors: res.errors ?? [],
                });
                toast({
                    title: "Import refused",
                    description: "The CSV no longer validates — fix the errors below and try again.",
                    variant: "destructive",
                });
            } else {
                toast({
                    title: "Backfill committed",
                    description: `${res.written_sin ?? 0} SIN, ${res.written_gst ?? 0} GST BN written.`,
                });
            }
        } catch (e) {
            toast({
                title: "Commit failed",
                description: e instanceof Error ? e.message : "The backfill did not complete.",
                variant: "destructive",
            });
        } finally {
            setCommitting(false);
        }
    };

    const c = report?.counts;

    return (
        <Card>
            <CardHeader>
                <CardTitle className="flex items-center gap-2">
                    <Upload className="h-5 w-5" />
                    Legacy tax-ID (SIN + GST/HST BN) backfill
                </CardTitle>
                <CardDescription>
                    Fill SIN and GST/HST business number for drivers whose numbers were collected on
                    the previous app, matched by phone. Bank account/routing numbers are never read
                    from this CSV — only phone, sin, and gst_bn.
                </CardDescription>
            </CardHeader>
            <CardContent className="space-y-6">
                <div className="flex gap-2 rounded-md border border-muted bg-muted/30 p-3 text-sm">
                    <Info className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
                    <p className="text-muted-foreground">
                        CSV header must be exactly <span className="font-mono">phone,sin,gst_bn</span>.
                        Both sin and gst_bn are NULL-only fills — a driver who already has either value
                        on file keeps it; corrections go through the driver&apos;s own update-SIN action,
                        not this tool. Max 500 rows per file.
                    </p>
                </div>

                <div className="space-y-3">
                    <h3 className="text-sm font-medium">1. Upload &amp; validate</h3>
                    <div className="grid gap-4 sm:grid-cols-2">
                        <div className="space-y-1">
                            <Label htmlFor="tax-id-csv" className="text-xs">
                                Tax-ID CSV
                                {file ? <CheckCircle2 className="ml-1 inline h-3 w-3 text-success" /> : null}
                            </Label>
                            <Input
                                id="tax-id-csv"
                                type="file"
                                accept=".csv,text/csv"
                                onChange={(e) => onPickFile(e.target.files?.[0] ?? null)}
                            />
                        </div>
                        <div className="space-y-1">
                            <Label htmlFor="tax-id-batch" className="text-xs">
                                Batch name (optional)
                            </Label>
                            <Input
                                id="tax-id-batch"
                                placeholder="e.g. tax-ids-batch-1"
                                value={batch}
                                onChange={(e) => {
                                    setBatch(e.target.value);
                                    setReport(null);
                                    setCommitted(null);
                                }}
                            />
                        </div>
                    </div>
                    <Button onClick={handleValidate} disabled={!file || validating}>
                        {validating ? (
                            <>
                                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                Validating…
                            </>
                        ) : (
                            "Validate (no writes)"
                        )}
                    </Button>
                </div>

                {report && c ? (
                    <div className="space-y-4">
                        <h3 className="text-sm font-medium">2. Review and commit</h3>

                        <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
                            <Stat label="Rows" value={c.rows} />
                            <Stat label="To write" value={c.to_write} />
                            <Stat label="SIN to write" value={c.sin_to_write} />
                            <Stat label="GST BN to write" value={c.gst_to_write} />
                            <Stat label="Skipped" value={c.skipped} tone="warn" />
                        </div>

                        <p className="text-xs text-muted-foreground">
                            Batch <span className="font-mono">{report.batch}</span>.
                        </p>

                        <div className="flex justify-end">
                            <Button
                                variant="ghost"
                                size="sm"
                                onClick={() => {
                                    navigator.clipboard.writeText(buildSummaryText(report));
                                    toast({ description: "Summary copied", duration: 1500 });
                                }}
                            >
                                <Copy className="mr-2 h-4 w-4" />
                                Copy summary
                            </Button>
                        </div>

                        {report.errors.length > 0 ? (
                            <div className="space-y-2">
                                <div className="flex items-center justify-between">
                                    <p className="flex items-center gap-2 text-sm font-medium text-destructive">
                                        <AlertTriangle className="h-4 w-4" />
                                        {report.errors.length} error(s) — commit is blocked
                                    </p>
                                    <Button
                                        variant="outline"
                                        size="sm"
                                        onClick={() =>
                                            exportToCsv("tax-id-backfill-errors", report.errors, [
                                                { key: "row_ref", label: "Row" },
                                                { key: "field", label: "Field" },
                                                { key: "message", label: "Message" },
                                            ])
                                        }
                                    >
                                        Download errors
                                    </Button>
                                </div>
                                <IssueTable items={report.errors} />
                            </div>
                        ) : null}

                        {report.warnings.length > 0 ? (
                            <div className="space-y-2">
                                <p className="text-sm font-medium text-warning">
                                    {report.warnings.length} warning(s) — these do not block the import
                                </p>
                                <IssueTable items={report.warnings} />
                            </div>
                        ) : null}

                        {committed?.committed ? (
                            <div className="flex items-center gap-2 rounded-md border border-success bg-success/10 p-3 text-sm">
                                <CheckCircle2 className="h-4 w-4 text-success" />
                                <span>
                                    {committed.written_sin ?? 0} SIN, {committed.written_gst ?? 0} GST BN
                                    written.{" "}
                                    {committed.stripe_push === "started"
                                        ? "Freshly-written SINs are being pushed to Stripe in the background."
                                        : null}
                                </span>
                            </div>
                        ) : report.can_commit ? (
                            <AlertDialog>
                                <AlertDialogTrigger asChild>
                                    <Button disabled={committing}>
                                        {committing ? (
                                            <>
                                                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                                Committing…
                                            </>
                                        ) : (
                                            "Commit backfill"
                                        )}
                                    </Button>
                                </AlertDialogTrigger>
                                <AlertDialogContent>
                                    <AlertDialogHeader>
                                        <AlertDialogTitle>
                                            Write tax ID(s) for {c.to_write} driver(s)?
                                        </AlertDialogTitle>
                                        <AlertDialogDescription>
                                            This writes {c.sin_to_write} vault-encrypted SIN and{" "}
                                            {c.gst_to_write} GST/HST BN value(s) to driver records in
                                            batch <span className="font-mono">{report.batch}</span>, and
                                            pushes any freshly-written SINs to Stripe in the background.
                                            A value already on file is never overwritten — only NULL
                                            columns are filled. Once written, a SIN can only be changed
                                            later through the driver&apos;s own update-SIN action
                                            (audited, with a reason), not by re-running this tool.
                                        </AlertDialogDescription>
                                    </AlertDialogHeader>
                                    <AlertDialogFooter>
                                        <AlertDialogCancel>Cancel</AlertDialogCancel>
                                        <AlertDialogAction onClick={handleCommit}>
                                            Commit backfill
                                        </AlertDialogAction>
                                    </AlertDialogFooter>
                                </AlertDialogContent>
                            </AlertDialog>
                        ) : (
                            <p className="text-sm text-muted-foreground">
                                Nothing to commit — fix the errors above, or every row in this CSV has
                                already been applied.
                            </p>
                        )}
                    </div>
                ) : null}
            </CardContent>
        </Card>
    );
}
