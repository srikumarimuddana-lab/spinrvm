"use client";

/**
 * Legacy Wallet-Balance Import — previous-app prepaid rider/driver wallet
 * credit into `wallets`/`wallet_transactions`.
 *
 * Three CSVs in one request (wallets, customers, drivers), following the
 * same validate-first / dry-run-report contract as the other bulk tools on
 * this page.
 *
 * Unlike the legacy booking import (which offsets imported earnings to a net
 * $0 payable change), this tool directly credits/debits real wallet
 * balances — there is no offsetting mechanism. Same two guards as the
 * booking importer for that reason:
 *   1. Commit is disabled until a validate for the CURRENT file set has come
 *      back clean. Changing any file clears the report.
 *   2. Commit requires typing the confirmation phrase.
 *
 * Column-name caveat (see backend/services/wallet_import_service.py's own
 * docstring): the expected CSV columns are inferred from this same export's
 * sibling collections, not confirmed against a real wallets.csv header row.
 * Always run the dry run first and read the error list carefully before the
 * first real commit.
 */

import { useMemo, useState } from "react";
import { AlertTriangle, CheckCircle2, Info, Loader2, Upload } from "lucide-react";
import {
    adminCommitWalletImport,
    adminValidateWalletImport,
    type WalletImportCommitResult,
    type WalletImportFiles,
    type WalletImportReport,
    type WalletImportReportItem,
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
import { useToast } from "@/components/ui/use-toast";
import { exportToCsv } from "@/lib/export-csv";

const CONFIRM_PHRASE = "APPLY";

/** The three files, in the order an operator meets them in the export. */
const FILE_FIELDS = [
    {
        key: "wallets" as const,
        label: "Wallets",
        hint: "wallets.csv — one row per legacy wallet credit/debit entry",
    },
    {
        key: "customers" as const,
        label: "Customers",
        hint: "customers.csv — supplies rider phone numbers for matching",
    },
    {
        key: "drivers" as const,
        label: "Drivers",
        hint: "drivers.csv — supplies driver phone numbers for matching",
    },
];

type FileState = Partial<Record<keyof WalletImportFiles, File | null>>;

function IssueTable({ items }: { items: WalletImportReportItem[] }) {
    return (
        <div className="overflow-x-auto rounded-md border">
            <Table>
                <TableHeader>
                    <TableRow>
                        <TableHead className="w-24">Row</TableHead>
                        <TableHead className="w-40">Wallet entry</TableHead>
                        <TableHead className="w-40">Field</TableHead>
                        <TableHead>Message</TableHead>
                    </TableRow>
                </TableHeader>
                <TableBody>
                    {items.map((it, i) => (
                        <TableRow key={`${it.row_num}-${it.field}-${i}`}>
                            <TableCell className="font-mono text-xs">{it.row_num}</TableCell>
                            <TableCell className="font-mono text-xs">{it.old_id}</TableCell>
                            <TableCell className="font-mono text-xs">{it.field}</TableCell>
                            <TableCell className="text-sm">{it.message}</TableCell>
                        </TableRow>
                    ))}
                </TableBody>
            </Table>
        </div>
    );
}

function Stat({
    label,
    value,
    tone,
    money,
}: {
    label: string;
    value: number;
    tone?: "warn" | "error";
    money?: boolean;
}) {
    const toneCls =
        tone === "error" && value > 0
            ? "text-destructive"
            : tone === "warn" && value > 0
              ? "text-warning"
              : "text-foreground";
    return (
        <div className="rounded-md border p-3">
            <div className={`text-2xl font-semibold ${toneCls}`}>
                {money ? `$${value.toFixed(2)}` : value}
            </div>
            <div className="text-xs text-muted-foreground">{label}</div>
        </div>
    );
}

export function LegacyWalletImport() {
    const { toast } = useToast();

    const [files, setFiles] = useState<FileState>({});
    const [report, setReport] = useState<WalletImportReport | null>(null);
    const [committed, setCommitted] = useState<WalletImportCommitResult | null>(null);
    const [confirmText, setConfirmText] = useState("");
    const [validating, setValidating] = useState(false);
    const [committing, setCommitting] = useState(false);

    const allFiles = useMemo<WalletImportFiles | null>(() => {
        const { wallets, customers, drivers } = files;
        if (!wallets || !customers || !drivers) return null;
        return { wallets, customers, drivers };
    }, [files]);

    // Changing any file invalidates the report: an operator must never be
    // able to review one set of files and then commit a different set.
    const setFile = (key: keyof WalletImportFiles, file: File | null) => {
        setFiles((prev) => ({ ...prev, [key]: file }));
        setReport(null);
        setCommitted(null);
        setConfirmText("");
    };

    const opts = () => ({
        // Reuse the validated batch so a re-send dedups via wallet_apply_delta
        // instead of double-crediting.
        ...(report?.batch ? { batch: report.batch } : {}),
    });

    const handleValidate = async () => {
        if (!allFiles) return;
        setValidating(true);
        setCommitted(null);
        try {
            setReport(await adminValidateWalletImport(allFiles, opts()));
        } catch (e) {
            setReport(null);
            toast({
                title: "Validation failed",
                description: e instanceof Error ? e.message : "Could not validate the export.",
                variant: "destructive",
            });
        } finally {
            setValidating(false);
        }
    };

    const handleCommit = async () => {
        if (!allFiles || !report?.can_commit) return;
        setCommitting(true);
        try {
            const res = await adminCommitWalletImport(allFiles, opts());
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
                    description:
                        "Nothing was written. The report below shows why — most often the rows were already applied.",
                    variant: "destructive",
                });
            } else {
                setConfirmText("");
                const failed = res.failed ?? 0;
                toast({
                    title: failed > 0 ? "Import completed with failures" : "Import committed",
                    description: `${res.applied ?? 0} applied, ${res.deduped ?? 0} already applied, ${failed} failed.`,
                    variant: failed > 0 ? "destructive" : undefined,
                });
            }
        } catch (e) {
            toast({
                title: "Commit failed",
                description: e instanceof Error ? e.message : "The import did not complete.",
                variant: "destructive",
            });
        } finally {
            setCommitting(false);
        }
    };

    const c = report?.counts;
    const failedResults = committed?.results?.filter((r) => r.status === "failed") ?? [];
    const canCommit =
        Boolean(allFiles) &&
        Boolean(report?.can_commit) &&
        confirmText.trim().toUpperCase() === CONFIRM_PHRASE &&
        !committing &&
        !committed?.committed;

    return (
        <Card>
            <CardHeader>
                <CardTitle className="flex items-center gap-2">
                    <Upload className="h-5 w-5" />
                    Legacy wallet-balance import
                </CardTitle>
                <CardDescription>
                    Import prepaid rider/driver wallet credits from the previous app. Riders and
                    drivers are matched by phone number; an entry whose party is not in Spinr is
                    skipped and reported, not fabricated.
                </CardDescription>
            </CardHeader>
            <CardContent className="space-y-6">
                <div className="flex gap-2 rounded-md border border-warning bg-warning/10 p-3 text-sm">
                    <Info className="mt-0.5 h-4 w-4 shrink-0 text-warning" />
                    <div className="space-y-1">
                        <p className="font-medium">
                            This directly credits/debits real wallet balances — unlike the booking
                            importer, there is no offsetting mechanism.
                        </p>
                        <p className="text-muted-foreground">
                            Every delta goes through the row-locked wallet_apply_delta function
                            (never a plain balance write), and a re-sent commit for the same batch
                            dedups automatically instead of double-crediting. Always run the dry
                            run first and check the net total.
                        </p>
                    </div>
                </div>

                <div className="flex gap-2 rounded-md border border-muted bg-muted/30 p-3 text-sm">
                    <Info className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
                    <p className="text-muted-foreground">
                        The CSV columns this tool expects are inferred from the export&apos;s other
                        files, not yet confirmed against a real wallets.csv header row. Read the
                        error list carefully on your first dry run.
                    </p>
                </div>

                {/* 1. Files */}
                <div className="space-y-3">
                    <h3 className="text-sm font-medium">1. Select the three exported CSVs</h3>
                    <div className="grid gap-3 sm:grid-cols-3">
                        {FILE_FIELDS.map((f) => (
                            <div key={f.key} className="space-y-1">
                                <Label htmlFor={`wallet-import-${f.key}`} className="text-xs">
                                    {f.label}
                                    {files[f.key] ? (
                                        <CheckCircle2 className="ml-1 inline h-3 w-3 text-success" />
                                    ) : null}
                                </Label>
                                <Input
                                    id={`wallet-import-${f.key}`}
                                    type="file"
                                    accept=".csv,text/csv"
                                    onChange={(e) => setFile(f.key, e.target.files?.[0] ?? null)}
                                />
                                <p className="text-xs text-muted-foreground">{f.hint}</p>
                            </div>
                        ))}
                    </div>
                </div>

                {/* 2. Validate */}
                <div className="space-y-3">
                    <h3 className="text-sm font-medium">2. Dry run</h3>
                    <Button onClick={handleValidate} disabled={!allFiles || validating}>
                        {validating ? (
                            <>
                                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                Validating…
                            </>
                        ) : (
                            "Validate (no writes)"
                        )}
                    </Button>
                    {!allFiles ? (
                        <p className="text-xs text-muted-foreground">
                            All three files are required before validating.
                        </p>
                    ) : null}
                </div>

                {/* 3. Report + commit */}
                {report && c ? (
                    <div className="space-y-4">
                        <h3 className="text-sm font-medium">3. Review and commit</h3>

                        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
                            <Stat label="Deltas to apply" value={c.target_rows} />
                            <Stat label="Rider-owned" value={c.rider_rows} />
                            <Stat label="Driver-owned" value={c.driver_rows} />
                            <Stat label="Unmatched (skipped)" value={c.skipped_unmatched} tone="warn" />
                            <Stat label="Sum credited" value={c.sum_add} money />
                            <Stat label="Sum debited" value={c.sum_deduct} money />
                            <Stat label="Net" value={c.sum_net} money />
                            <Stat label="Zero-amount (skipped)" value={c.skipped_zero_amount} tone="warn" />
                        </div>

                        <p className="text-xs text-muted-foreground">
                            Read {c.rows_read} row(s); {c.skipped_missing_id} missing an id,{" "}
                            {c.skipped_duplicate_id} duplicate id(s) in the CSV. Batch{" "}
                            <span className="font-mono">{report.batch}</span>.
                        </p>

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
                                            exportToCsv("wallet-import-errors", report.errors, [
                                                { key: "row_num", label: "Row" },
                                                { key: "old_id", label: "Wallet entry" },
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
                            <div className="space-y-3">
                                <div className="flex items-center gap-2 rounded-md border border-success bg-success/10 p-3 text-sm">
                                    <CheckCircle2 className="h-4 w-4 text-success" />
                                    <span>
                                        {committed.applied ?? 0} delta(s) applied,{" "}
                                        {committed.deduped ?? 0} already applied (deduped),{" "}
                                        {committed.failed ?? 0} failed.
                                    </span>
                                </div>
                                {failedResults.length > 0 ? (
                                    <div className="space-y-2 rounded-md border border-destructive bg-destructive/10 p-3 text-sm">
                                        <p className="flex items-center gap-2 font-medium text-destructive">
                                            <AlertTriangle className="h-4 w-4" />
                                            {failedResults.length} delta(s) failed to apply — check the
                                            backend logs for the reason (e.g. a debit exceeding the
                                            wallet&apos;s balance). Re-running the same batch will retry
                                            only what did not already succeed.
                                        </p>
                                        <ul className="space-y-1 font-mono text-xs">
                                            {failedResults.map((r) => (
                                                <li key={r.reference_id}>{r.reference_id}</li>
                                            ))}
                                        </ul>
                                    </div>
                                ) : null}
                            </div>
                        ) : report.can_commit ? (
                            <div className="space-y-2 rounded-md border p-3">
                                <Label htmlFor="wallet-import-confirm" className="text-xs">
                                    This applies {c.target_rows} real wallet delta(s) (
                                    {`$${c.sum_add.toFixed(2)} credited, $${c.sum_deduct.toFixed(2)} debited`})
                                    to live rider/driver balances and cannot be undone from here. Type{" "}
                                    <span className="font-mono">{CONFIRM_PHRASE}</span> to enable.
                                </Label>
                                <div className="flex gap-2">
                                    <Input
                                        id="wallet-import-confirm"
                                        value={confirmText}
                                        onChange={(e) => setConfirmText(e.target.value)}
                                        placeholder={CONFIRM_PHRASE}
                                        className="max-w-40 font-mono"
                                    />
                                    <Button onClick={handleCommit} disabled={!canCommit}>
                                        {committing ? (
                                            <>
                                                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                                Applying…
                                            </>
                                        ) : (
                                            "Apply deltas"
                                        )}
                                    </Button>
                                </div>
                            </div>
                        ) : (
                            <p className="text-sm text-muted-foreground">
                                Nothing to commit — fix the errors above, or every entry in this
                                export has already been applied.
                            </p>
                        )}
                    </div>
                ) : null}
            </CardContent>
        </Card>
    );
}
