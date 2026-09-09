"use client";

/**
 * Legacy Tax-ID (SIN + GST/HST BN) Backfill.
 *
 * Two ways to get to the same validate → review → commit flow every other
 * bulk tool on this page uses:
 *
 *  - "I have the ready CSV" — one file, header exactly "phone,sin,gst_bn",
 *    matched against already-legacy-imported drivers by phone. The original
 *    flow this tool shipped with.
 *  - "Prepare from Mongo export" — the raw banks.csv + drivers.csv straight
 *    from the previous app's MongoDB export. The backend joins them
 *    server-side (backend/routes/admin/tax_id_import.py's prepare-validate/
 *    prepare-commit, reusing the same join scripts/build_legacy_tax_id_csv.py
 *    uses) so an operator never has to build the ready CSV by hand or paste
 *    real SIN/GST values into a chat session to get one built.
 *
 * No typed confirmation phrase, matching the sibling SIN/DOB backfill page's
 * own convention (the NULL-only fill policy plus the backend's
 * compare-and-set write already make an accidental re-commit a no-op, not a
 * silent overwrite) — it does, however, get that same sibling's AlertDialog
 * gut-check before the first commit, since it writes vault-encrypted SIN
 * plus a background push to Stripe, which a mis-click shouldn't trigger
 * unconfirmed.
 */

import { useState } from "react";
import { AlertTriangle, CheckCircle2, Copy, HelpCircle, Info, Loader2, Upload } from "lucide-react";
import {
    adminCommitTaxIdBackfill,
    adminPrepareCommitTaxIdFromLegacyExport,
    adminPrepareValidateTaxIdFromLegacyExport,
    adminValidateTaxIdBackfill,
    type TaxIdBackfillCommitResult,
    type TaxIdBackfillFromLegacyExportReport,
    type TaxIdBackfillReport,
    type TaxIdBackfillReportItem,
} from "@/lib/api";
import { explainTaxIdIssue } from "@/lib/tax-id-error-help";
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
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
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

type UploadMode = "single" | "legacy-export";

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
                    {items.map((it, i) => {
                        const explanation = explainTaxIdIssue(it.message);
                        return (
                            <TableRow key={`${it.row_ref}-${it.field}-${i}`}>
                                <TableCell className="font-mono text-xs align-top">{it.row_ref}</TableCell>
                                <TableCell className="font-mono text-xs align-top">{it.field}</TableCell>
                                <TableCell className="text-sm">
                                    <p>{it.message}</p>
                                    {explanation ? (
                                        <div className="mt-1.5 space-y-0.5 rounded border-l-2 border-muted-foreground/30 pl-2 text-xs text-muted-foreground">
                                            <p>{explanation.cause}</p>
                                            <p className="font-medium">What to do: {explanation.fix}</p>
                                        </div>
                                    ) : null}
                                </TableCell>
                            </TableRow>
                        );
                    })}
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

function WhatThisDoes() {
    return (
        <div className="space-y-3 rounded-md border border-muted bg-muted/30 p-4 text-sm">
            <div className="flex items-center gap-2 font-medium">
                <HelpCircle className="h-4 w-4" />
                What this tool does, in plain terms
            </div>
            <dl className="grid gap-3 sm:grid-cols-2">
                <div>
                    <dt className="text-xs font-semibold uppercase text-muted-foreground">What</dt>
                    <dd className="text-muted-foreground">
                        Fills in a driver&apos;s Social Insurance Number (SIN) and GST/HST business
                        number, but only for drivers who gave us those numbers on the{" "}
                        <span className="font-medium text-foreground">previous app</span> and haven&apos;t
                        been asked again since moving to Spinr.
                    </dd>
                </div>
                <div>
                    <dt className="text-xs font-semibold uppercase text-muted-foreground">Why</dt>
                    <dd className="text-muted-foreground">
                        Drivers need a SIN on file before Stripe will pay them, and a GST/HST number
                        before Spinr can report their GST-registered status. Without this backfill,
                        every migrated driver would be stopped and asked to re-enter numbers they
                        already gave us once.
                    </dd>
                </div>
                <div>
                    <dt className="text-xs font-semibold uppercase text-muted-foreground">Which files</dt>
                    <dd className="text-muted-foreground">
                        Either a ready <span className="font-mono">phone,sin,gst_bn</span> CSV, or the
                        raw <span className="font-mono">banks.csv</span> +{" "}
                        <span className="font-mono">drivers.csv</span> straight from the old app&apos;s
                        MongoDB export — pick whichever you have under the tabs below.
                    </dd>
                </div>
                <div>
                    <dt className="text-xs font-semibold uppercase text-muted-foreground">Value</dt>
                    <dd className="text-muted-foreground">
                        A driver never has to fish out and re-type their SIN or business number, and
                        this tool never overwrites a value a driver has already entered themselves —
                        it only fills in blanks.
                    </dd>
                </div>
            </dl>
            <p className="border-t pt-2 text-xs text-muted-foreground">
                Safety rails: a value that&apos;s already on file is never touched (only blank fields
                get filled), every SIN is encrypted before it&apos;s stored, and nothing you upload or
                see on this screen — including validation errors — ever shows a full SIN, GST number,
                or phone number.
            </p>
        </div>
    );
}

export function LegacyTaxIdImport() {
    const { toast } = useToast();

    const [mode, setMode] = useState<UploadMode>("single");

    // "I have the ready CSV" mode
    const [file, setFile] = useState<File | null>(null);

    // "Prepare from Mongo export" mode
    const [banksFile, setBanksFile] = useState<File | null>(null);
    const [driversFile, setDriversFile] = useState<File | null>(null);

    const [batch, setBatch] = useState("");
    const [report, setReport] = useState<TaxIdBackfillReport | TaxIdBackfillFromLegacyExportReport | null>(null);
    const [reportSource, setReportSource] = useState<UploadMode | null>(null);
    const [committed, setCommitted] = useState<TaxIdBackfillCommitResult | null>(null);
    const [validating, setValidating] = useState(false);
    const [committing, setCommitting] = useState(false);

    const resetReport = () => {
        setReport(null);
        setReportSource(null);
        setCommitted(null);
    };

    const onPickFile = (f: File | null) => {
        setFile(f);
        resetReport();
    };
    const onPickBanksFile = (f: File | null) => {
        setBanksFile(f);
        resetReport();
    };
    const onPickDriversFile = (f: File | null) => {
        setDriversFile(f);
        resetReport();
    };

    const handleValidateSingle = async () => {
        if (!file) return;
        setValidating(true);
        setCommitted(null);
        try {
            const r = await adminValidateTaxIdBackfill(file, batch || undefined);
            setReport(r);
            setReportSource("single");
        } catch (e) {
            setReport(null);
            setReportSource(null);
            toast({
                title: "Validation failed",
                description: e instanceof Error ? e.message : "Could not validate the CSV.",
                variant: "destructive",
            });
        } finally {
            setValidating(false);
        }
    };

    const handleValidateLegacyExport = async () => {
        if (!banksFile || !driversFile) return;
        setValidating(true);
        setCommitted(null);
        try {
            const r = await adminPrepareValidateTaxIdFromLegacyExport(
                { banks: banksFile, drivers: driversFile },
                { batch: batch || undefined }
            );
            setReport(r);
            setReportSource("legacy-export");
        } catch (e) {
            setReport(null);
            setReportSource(null);
            toast({
                title: "Preparation failed",
                description:
                    e instanceof Error ? e.message : "Could not join and validate banks.csv + drivers.csv.",
                variant: "destructive",
            });
        } finally {
            setValidating(false);
        }
    };

    const handleValidate = mode === "single" ? handleValidateSingle : handleValidateLegacyExport;
    const canValidate = mode === "single" ? Boolean(file) : Boolean(banksFile && driversFile);

    const applyRefusedCommit = (res: TaxIdBackfillCommitResult) => {
        setCommitted(res);
        setReport((prev) =>
            prev
                ? {
                      ...prev,
                      batch: res.batch,
                      can_commit: res.can_commit ?? false,
                      counts: res.counts ?? prev.counts,
                      warnings: res.warnings ?? [],
                      errors: res.errors ?? [],
                  }
                : prev
        );
        toast({
            title: "Import refused",
            description: "This no longer validates — fix the errors below and try again.",
            variant: "destructive",
        });
    };

    const handleCommit = async () => {
        if (!report?.can_commit || !reportSource) return;
        setCommitting(true);
        try {
            let res: TaxIdBackfillCommitResult;
            if (reportSource === "single") {
                if (!file) return;
                res = await adminCommitTaxIdBackfill(file, report.batch);
            } else {
                if (!banksFile || !driversFile) return;
                const legacyReport = report as TaxIdBackfillFromLegacyExportReport;
                res = await adminPrepareCommitTaxIdFromLegacyExport(
                    { banks: banksFile, drivers: driversFile },
                    { batch: report.batch, validationToken: legacyReport.validation_token }
                );
            }
            if (!res.committed) {
                // Backend refused and returned the fresh report instead.
                applyRefusedCommit(res);
            } else {
                setCommitted(res);
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
    const joinStats = reportSource === "legacy-export" ? (report as TaxIdBackfillFromLegacyExportReport)?.join_stats : undefined;

    return (
        <Card>
            <CardHeader>
                <CardTitle className="flex items-center gap-2">
                    <Upload className="h-5 w-5" />
                    Legacy tax-ID (SIN + GST/HST BN) backfill
                </CardTitle>
                <CardDescription>
                    Fill SIN and GST/HST business number for drivers whose numbers were collected on
                    the previous app, matched by phone.
                </CardDescription>
            </CardHeader>
            <CardContent className="space-y-6">
                <WhatThisDoes />

                <div className="space-y-3">
                    <h3 className="text-sm font-medium">1. Upload &amp; validate</h3>

                    <Tabs
                        value={mode}
                        onValueChange={(v) => {
                            setMode(v as UploadMode);
                            resetReport();
                        }}
                    >
                        <TabsList>
                            <TabsTrigger value="single">I have the ready CSV</TabsTrigger>
                            <TabsTrigger value="legacy-export">Prepare from Mongo export</TabsTrigger>
                        </TabsList>

                        <TabsContent value="single" className="space-y-3 pt-3">
                            <div className="flex gap-2 rounded-md border border-muted bg-muted/30 p-3 text-sm">
                                <Info className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
                                <p className="text-muted-foreground">
                                    CSV header must be exactly{" "}
                                    <span className="font-mono">phone,sin,gst_bn</span>. Both sin and
                                    gst_bn are NULL-only fills — a driver who already has either value on
                                    file keeps it; corrections go through the driver&apos;s own update-SIN
                                    action, not this tool. Max 500 rows per file. Bank account/routing
                                    numbers are never read from this CSV.
                                </p>
                            </div>
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
                        </TabsContent>

                        <TabsContent value="legacy-export" className="space-y-3 pt-3">
                            <div className="flex gap-2 rounded-md border border-muted bg-muted/30 p-3 text-sm">
                                <Info className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
                                <p className="text-muted-foreground">
                                    Upload the two files exactly as exported from the previous app&apos;s
                                    MongoDB dump — <span className="font-mono">banks.csv</span> and{" "}
                                    <span className="font-mono">drivers.csv</span>. This tool joins them on
                                    the server and builds the ready file for you; the SIN and GST/HST
                                    numbers inside them never leave this admin portal — they are never sent
                                    to Claude, email, or anywhere else. Max 2 MB / 2,000 rows per file.
                                </p>
                            </div>
                            <div className="grid gap-4 sm:grid-cols-2">
                                <div className="space-y-1">
                                    <Label htmlFor="tax-id-banks-csv" className="text-xs">
                                        banks.csv
                                        {banksFile ? (
                                            <CheckCircle2 className="ml-1 inline h-3 w-3 text-success" />
                                        ) : null}
                                    </Label>
                                    <Input
                                        id="tax-id-banks-csv"
                                        type="file"
                                        accept=".csv,text/csv"
                                        onChange={(e) => onPickBanksFile(e.target.files?.[0] ?? null)}
                                    />
                                </div>
                                <div className="space-y-1">
                                    <Label htmlFor="tax-id-drivers-csv" className="text-xs">
                                        drivers.csv
                                        {driversFile ? (
                                            <CheckCircle2 className="ml-1 inline h-3 w-3 text-success" />
                                        ) : null}
                                    </Label>
                                    <Input
                                        id="tax-id-drivers-csv"
                                        type="file"
                                        accept=".csv,text/csv"
                                        onChange={(e) => onPickDriversFile(e.target.files?.[0] ?? null)}
                                    />
                                </div>
                            </div>
                        </TabsContent>
                    </Tabs>

                    <div className="grid gap-4 sm:grid-cols-2">
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
                                    resetReport();
                                }}
                            />
                        </div>
                    </div>
                    <Button onClick={handleValidate} disabled={!canValidate || validating}>
                        {validating ? (
                            <>
                                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                {mode === "single" ? "Validating…" : "Preparing & validating…"}
                            </>
                        ) : mode === "single" ? (
                            "Validate (no writes)"
                        ) : (
                            "Prepare & validate (no writes)"
                        )}
                    </Button>
                </div>

                {report && c ? (
                    <div className="space-y-4">
                        <h3 className="text-sm font-medium">2. Review and commit</h3>

                        {joinStats ? (
                            <div className="space-y-2">
                                <p className="text-xs font-medium text-muted-foreground">
                                    From joining banks.csv + drivers.csv:
                                </p>
                                <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                                    <Stat label="banks.csv rows" value={joinStats.banks_rows} />
                                    <Stat
                                        label="No matching driver"
                                        value={joinStats.unmatched_no_phone}
                                        tone="warn"
                                    />
                                    <Stat
                                        label="Neither SIN nor GST"
                                        value={joinStats.skipped_no_sin_or_gst}
                                        tone="warn"
                                    />
                                    <Stat
                                        label="Same driver, multiple records"
                                        value={joinStats.duplicate_phone_groups}
                                        tone="warn"
                                    />
                                </div>
                            </div>
                        ) : null}

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
                                Nothing to commit — fix the errors above, or every row has already been
                                applied.
                            </p>
                        )}
                    </div>
                ) : null}
            </CardContent>
        </Card>
    );
}
