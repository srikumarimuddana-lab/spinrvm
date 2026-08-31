"use client";

/**
 * Legacy SIN/DOB Backfill — admin-dashboard wrapper for the CLI-only
 * backend/scripts/backfill_legacy_driver_sin_dob.py (Phase 2 of the
 * 2026-08-27 migration plan; sibling of the vehicle-history backfill).
 *
 * Writes a vault-encrypted SIN + date of birth onto drivers already created
 * by the legacy import, matched by phone via a two-file crosswalk:
 * banks.csv (SIN/DOB, keyed by a Mongo driver_id) + drivers.csv (that same
 * export's driver collection, used only to resolve driver_id -> phone).
 *
 * Same validate -> review -> commit flow as Legacy Driver Import
 * (drivers/legacy-import), extended to two files instead of one. Never
 * clobbers a SIN/DOB already on file (self-entered always wins), and the
 * report never carries a raw SIN or DOB — only counts and
 * old_driver_id/field/message per issue, matching the CLI's own
 * print_sin_dob_report guarantee.
 */

import { useMemo, useState } from "react";
import { Upload, CheckCircle2, AlertTriangle, Loader2, Info, Copy } from "lucide-react";
import {
    adminValidateSinDobBackfill,
    adminCommitSinDobBackfill,
    type SinDobBackfillReport,
    type SinDobBackfillReportItem,
    type SinDobBackfillFiles,
} from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
    Card,
    CardHeader,
    CardTitle,
    CardDescription,
    CardContent,
} from "@/components/ui/card";
import {
    Table,
    TableHeader,
    TableBody,
    TableHead,
    TableRow,
    TableCell,
} from "@/components/ui/table";
import { useToast } from "@/components/ui/use-toast";
import { useRequireModule } from "@/hooks/useRequireModule";
import { exportToCsv } from "@/lib/export-csv";
import Link from "next/link";

/** The two files, in the order an operator meets them in the export. */
const FILE_FIELDS = [
    {
        key: "banks" as const,
        label: "Banks",
        hint: "banks.csv — one row per legacy SIN/DOB entry, keyed by driver_id",
    },
    {
        key: "drivers" as const,
        label: "Drivers",
        hint: "drivers.csv — supplies driver phone numbers to resolve driver_id",
    },
];

type FileState = Partial<Record<keyof SinDobBackfillFiles, File | null>>;

const REPORT_COLUMNS = [
    { key: "old_driver_id", label: "old_driver_id" },
    { key: "field", label: "field" },
    { key: "message", label: "message" },
];

function IssueTable({ items }: { items: SinDobBackfillReportItem[] }) {
    return (
        <div className="overflow-x-auto rounded-md border">
            <Table>
                <TableHeader>
                    <TableRow>
                        <TableHead className="w-40">Row (old_driver_id)</TableHead>
                        <TableHead className="w-48">Field</TableHead>
                        <TableHead>Message</TableHead>
                    </TableRow>
                </TableHeader>
                <TableBody>
                    {items.map((it, i) => (
                        <TableRow key={`${it.old_driver_id}-${it.field}-${i}`}>
                            <TableCell className="font-mono text-xs">{it.old_driver_id}</TableCell>
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
}: {
    label: string;
    value: number;
    tone?: "warn" | "error";
}) {
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
// counts only, same PII guarantee as the report itself (no raw SIN/DOB).
function buildSummaryText(report: SinDobBackfillReport): string {
    const c = report.counts;
    const rows: [string, number][] = [
        ["Rows", c?.rows ?? 0],
        ["Drivers to update", c?.to_update ?? 0],
        ["Unmatched (skipped)", c?.skipped_unmatched ?? 0],
        ["Not a legacy driver (skipped)", c?.skipped_not_legacy_driver ?? 0],
        ["Already on file (skipped)", c?.skipped_already_on_file ?? 0],
        ["Duplicate match (skipped)", c?.skipped_duplicate_match ?? 0],
        ["Warnings", report.warnings.length],
        ["Errors", report.errors.length],
    ];
    const lines = [
        `Legacy SIN/DOB Backfill — batch ${report.batch}`,
        "| Metric | Count |",
        "|---|---|",
        ...rows.map(([label, value]) => `| ${label} | ${value} |`),
    ];
    return lines.join("\n");
}

export default function LegacySinDobBackfillPage() {
    const { allowed } = useRequireModule("drivers");
    const { toast } = useToast();

    const [files, setFiles] = useState<FileState>({});
    const [report, setReport] = useState<SinDobBackfillReport | null>(null);
    const [validating, setValidating] = useState(false);
    const [committing, setCommitting] = useState(false);
    const [committedSummary, setCommittedSummary] = useState<string | null>(null);

    const allFiles = useMemo<SinDobBackfillFiles | null>(() => {
        const { banks, drivers } = files;
        if (!banks || !drivers) return null;
        return { banks, drivers };
    }, [files]);

    if (!allowed) return null;

    const resetReport = () => {
        setReport(null);
        setCommittedSummary(null);
    };

    // Changing either file invalidates the report — an operator must never
    // be able to review one file pair and then commit a different pair.
    const setFile = (key: keyof SinDobBackfillFiles, file: File | null) => {
        setFiles((prev) => ({ ...prev, [key]: file }));
        resetReport();
    };

    const handleValidate = async () => {
        if (!allFiles) return;
        setValidating(true);
        setCommittedSummary(null);
        try {
            const rep = await adminValidateSinDobBackfill(allFiles);
            setReport(rep);
        } catch (e) {
            toast({
                title: "Validation failed",
                description: e instanceof Error ? e.message : "Could not validate the CSVs",
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
            // batch + validationToken must come from this exact report — the
            // backend binds the token to (batch, combined CSV bytes, admin)
            // and refuses commit otherwise.
            const res = await adminCommitSinDobBackfill(allFiles, {
                batch: report.batch,
                validationToken: report.validation_token,
            });
            if (res.committed) {
                setCommittedSummary(
                    `Updated ${res.updated ?? 0} driver(s) with SIN and/or date of birth.` +
                        (res.conflicts && res.conflicts.length
                            ? ` ${res.conflicts.length} skipped — the driver self-entered a value in between validate and commit.`
                            : "") +
                        (res.warnings && res.warnings.length ? ` ${res.warnings.length} warning(s).` : ""),
                );
                setReport(null);
                setFiles({});
                toast({
                    title: "Backfill complete",
                    description: `${res.updated ?? 0} driver(s) updated.`,
                });
            } else {
                // The CSVs no longer validate (data changed since validate).
                setReport({
                    batch: res.batch,
                    can_commit: false,
                    counts:
                        res.counts ?? {
                            rows: 0,
                            to_update: 0,
                            skipped_unmatched: 0,
                            skipped_not_legacy_driver: 0,
                            skipped_already_on_file: 0,
                            skipped_duplicate_match: 0,
                        },
                    warnings: res.warnings ?? [],
                    errors: res.errors ?? [],
                });
                toast({
                    title: "Backfill refused",
                    description: "The CSVs have validation errors. Fix them and try again.",
                    variant: "destructive",
                });
            }
        } catch (e) {
            toast({
                title: "Backfill failed",
                description: e instanceof Error ? e.message : "Could not commit the backfill",
                variant: "destructive",
            });
        } finally {
            setCommitting(false);
        }
    };

    const counts = report?.counts;

    return (
        <div className="mx-auto max-w-4xl space-y-6 p-4">
            <div>
                <h1 className="text-2xl font-semibold">Legacy SIN/DOB Backfill</h1>
                <p className="text-sm text-muted-foreground">
                    Backfill SIN and date of birth for drivers already created by the{" "}
                    <Link href="/dashboard/drivers/legacy-import" className="underline">
                        Legacy Driver Import
                    </Link>{" "}
                    (Mongo export), using the export&apos;s{" "}
                    <span className="font-mono">banks.csv</span> and{" "}
                    <span className="font-mono">drivers.csv</span>. Matches are made by phone
                    number, and only ever touch drivers already tagged as legacy-imported — a
                    phone coincidence can never reach an organic driver&apos;s SIN or DOB.
                </p>
            </div>

            <div className="flex gap-2 rounded-md border border-warning bg-warning/10 p-3 text-sm">
                <Info className="mt-0.5 h-4 w-4 shrink-0 text-warning" />
                <div className="space-y-1">
                    <p className="font-medium">
                        A SIN or date of birth already on file is never overwritten.
                    </p>
                    <p className="text-muted-foreground">
                        Whatever is on file — self-entered by the driver, or written by an earlier
                        run of this backfill — always wins. The SIN is written vault-encrypted, the
                        same way a driver&apos;s own SIN entry is stored. Neither value is ever
                        shown in this tool&apos;s reports.
                    </p>
                </div>
            </div>

            <Card>
                <CardHeader>
                    <CardTitle>1. Select the two exported CSVs</CardTitle>
                    <CardDescription>
                        Both files come from the same raw MongoDB export as the Legacy Driver
                        Import (<span className="font-mono">Mongo.zip</span>).
                    </CardDescription>
                </CardHeader>
                <CardContent>
                    <div className="grid gap-4 sm:grid-cols-2">
                        {FILE_FIELDS.map((f) => (
                            <div key={f.key} className="space-y-1">
                                <Label htmlFor={`sin-dob-backfill-${f.key}`} className="text-sm font-medium">
                                    {f.label}
                                    {files[f.key] ? (
                                        <CheckCircle2 className="ml-1 inline h-3 w-3 text-success" />
                                    ) : null}
                                </Label>
                                <Input
                                    id={`sin-dob-backfill-${f.key}`}
                                    type="file"
                                    accept=".csv,text/csv"
                                    onChange={(e) => setFile(f.key, e.target.files?.[0] ?? null)}
                                />
                                <p className="text-xs text-muted-foreground">{f.hint}</p>
                            </div>
                        ))}
                    </div>
                </CardContent>
            </Card>

            <Card>
                <CardHeader>
                    <CardTitle>2. Validate</CardTitle>
                    <CardDescription>
                        Validation is a dry run — nothing is written until you commit.
                    </CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                    <div className="flex items-center gap-3">
                        <Button onClick={handleValidate} disabled={!allFiles || validating || committing}>
                            {validating ? (
                                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                            ) : (
                                <Upload className="mr-2 h-4 w-4" />
                            )}
                            Validate
                        </Button>
                        {!allFiles ? (
                            <span className="text-sm text-muted-foreground">
                                Both files are required before validating.
                            </span>
                        ) : null}
                    </div>
                </CardContent>
            </Card>

            {committedSummary && (
                <Card className="border-success">
                    <CardContent className="flex items-center gap-3 py-4">
                        <CheckCircle2 className="h-5 w-5 text-success" />
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
                                ? "No errors — you can commit this backfill."
                                : "Fix the errors below, re-export your CSVs, and validate again."}
                        </CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-5">
                        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                            <Stat label="Rows" value={counts?.rows ?? 0} />
                            <Stat label="Drivers to update" value={counts?.to_update ?? 0} />
                            <Stat label="Unmatched (skipped)" value={counts?.skipped_unmatched ?? 0} tone="warn" />
                            <Stat
                                label="Not a legacy driver (skipped)"
                                value={counts?.skipped_not_legacy_driver ?? 0}
                                tone="warn"
                            />
                            <Stat
                                label="Already on file (skipped)"
                                value={counts?.skipped_already_on_file ?? 0}
                            />
                            <Stat
                                label="Duplicate match (skipped)"
                                value={counts?.skipped_duplicate_match ?? 0}
                                tone="warn"
                            />
                            <Stat label="Warnings" value={report.warnings.length} tone="warn" />
                            <Stat label="Errors" value={report.errors.length} tone="error" />
                        </div>

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

                        {report.errors.length > 0 && (
                            <div className="space-y-2">
                                <div className="flex items-center justify-between">
                                    <h3 className="flex items-center gap-2 text-sm font-semibold text-destructive">
                                        <AlertTriangle className="h-4 w-4" /> Errors ({report.errors.length})
                                    </h3>
                                    <Button
                                        variant="ghost"
                                        size="sm"
                                        onClick={() =>
                                            exportToCsv("sin-dob-backfill-errors", report.errors, REPORT_COLUMNS)
                                        }
                                    >
                                        Download errors
                                    </Button>
                                </div>
                                <IssueTable items={report.errors} />
                            </div>
                        )}

                        {report.warnings.length > 0 && (
                            <div className="space-y-2">
                                <h3 className="flex items-center gap-2 text-sm font-semibold text-warning">
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
                                Commit backfill
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
        </div>
    );
}
