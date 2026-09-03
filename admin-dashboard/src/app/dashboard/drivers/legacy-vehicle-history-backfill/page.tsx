"use client";

/**
 * Legacy Vehicle-History Backfill — Phase 2 of the 2026-08-27 migration plan
 * (docs/migration/2026-08-27-legacy-data-full-migration-approach.md §4).
 *
 * Backfills append-only `driver_vehicle_history` rows (regulatory audit
 * table, migration 157) from the previous app's raw Mongo export. Takes two
 * CSVs: vehicle_details.csv (VIN/plate/make/model/colour/year, keyed by a
 * Mongo ObjectId driver_id) and drivers.csv (the same export's driver
 * collection, used only to resolve that ObjectId to a phone number — the
 * same crosswalk role it plays in Legacy Driver Import).
 *
 * Same validate → review → commit flow as Legacy Driver Import
 * (drivers/legacy-import), adapted for two files instead of one. Unlike
 * that importer, this tool never creates/links/enriches a driver row and
 * never touches a live vehicle field — it only appends history rows, so
 * there is no "existing account" ambiguity to review here.
 */

import { useRef, useState } from "react";
import Link from "next/link";
import { CheckCircle2, AlertTriangle, Loader2, Info, Upload, Copy } from "lucide-react";
import {
    adminValidateVehicleHistoryBackfill,
    adminCommitVehicleHistoryBackfill,
    type VehicleHistoryBackfillFiles,
    type VehicleHistoryBackfillReport,
    type VehicleHistoryBackfillReportItem,
} from "@/lib/api";
import { PageHeader } from "@/components/page-header";
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

/** The two files, in the order an operator meets them in the export. */
const FILE_FIELDS = [
    {
        key: "vehicleDetails" as const,
        label: "Vehicle details",
        hint: "vehicle_details.csv — VIN/plate/make/model/colour/year, keyed by driver_id",
    },
    {
        key: "drivers" as const,
        label: "Drivers",
        hint: "drivers.csv — the raw Mongo export's driver collection, used only to resolve driver_id → phone",
    },
];

type FileState = Partial<Record<keyof VehicleHistoryBackfillFiles, File | null>>;

const REPORT_COLUMNS = [
    { key: "old_driver_id", label: "old_driver_id" },
    { key: "field", label: "field" },
    { key: "message", label: "message" },
];

function IssueTable({ items }: { items: VehicleHistoryBackfillReportItem[] }) {
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
// counts only, no plate/VIN, matching the report's own no-raw-vehicle-data
// guarantee.
function buildSummaryText(report: VehicleHistoryBackfillReport): string {
    const c = report.counts;
    const rows: [string, number][] = [
        ["Vehicle rows", c?.vehicle_rows ?? 0],
        ["History rows to insert", c?.history_rows_to_insert ?? 0],
        ["Skipped (unmatched)", c?.skipped_unmatched ?? 0],
        ["Not a legacy driver (skipped)", c?.skipped_not_legacy_driver ?? 0],
        ["Already backfilled (skipped)", c?.skipped_already_backfilled ?? 0],
        ["Warnings", report.warnings.length],
        ["Errors", report.errors.length],
    ];
    const lines = [
        `Legacy Vehicle History Backfill — batch ${report.batch}`,
        "| Metric | Count |",
        "|---|---|",
        ...rows.map(([label, value]) => `| ${label} | ${value} |`),
    ];
    return lines.join("\n");
}

export default function LegacyVehicleHistoryBackfillPage() {
    const { allowed } = useRequireModule("drivers");
    const { toast } = useToast();
    const fileInputRefs = useRef<Partial<Record<keyof VehicleHistoryBackfillFiles, HTMLInputElement | null>>>({});

    const [files, setFiles] = useState<FileState>({});
    const [report, setReport] = useState<VehicleHistoryBackfillReport | null>(null);
    const [validating, setValidating] = useState(false);
    const [committing, setCommitting] = useState(false);
    const [committedSummary, setCommittedSummary] = useState<string | null>(null);

    if (!allowed) return null;

    const allFiles: VehicleHistoryBackfillFiles | null =
        files.vehicleDetails && files.drivers
            ? { vehicleDetails: files.vehicleDetails, drivers: files.drivers }
            : null;

    const resetReport = () => {
        setReport(null);
        setCommittedSummary(null);
    };

    // Changing either file invalidates the report — an operator must never
    // review one pair of files and then commit a different pair.
    const setFile = (key: keyof VehicleHistoryBackfillFiles, file: File | null) => {
        setFiles((prev) => ({ ...prev, [key]: file }));
        resetReport();
    };

    const handleValidate = async () => {
        if (!allFiles) return;
        setValidating(true);
        setCommittedSummary(null);
        try {
            const rep = await adminValidateVehicleHistoryBackfill(allFiles);
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
            // backend binds the token to (batch, both files' bytes, admin)
            // and refuses commit otherwise.
            const res = await adminCommitVehicleHistoryBackfill(allFiles, {
                batch: report.batch,
                validationToken: report.validation_token,
            });
            if (res.committed) {
                setCommittedSummary(
                    `Inserted ${res.history_rows_inserted ?? 0} vehicle-history row(s).` +
                        (res.warnings && res.warnings.length ? ` ${res.warnings.length} warning(s).` : ""),
                );
                setReport(null);
                setFiles({});
                Object.values(fileInputRefs.current).forEach((el) => {
                    if (el) el.value = "";
                });
                toast({
                    title: "Backfill complete",
                    description: `${res.history_rows_inserted ?? 0} history row(s) inserted.`,
                });
            } else {
                // The CSVs no longer validate (data changed since validate).
                setReport({
                    batch: res.batch,
                    can_commit: false,
                    counts:
                        res.counts ?? {
                            vehicle_rows: 0,
                            history_rows_to_insert: 0,
                            skipped_unmatched: 0,
                            skipped_not_legacy_driver: 0,
                            skipped_already_backfilled: 0,
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
            <PageHeader
                title="Legacy Vehicle-History Backfill"
                description={
                    <>
                        Backfill <span className="font-mono">driver_vehicle_history</span> (the 7-year
                        regulatory driver/vehicle-linkage record) from the previous app&apos;s raw Mongo
                        export, for drivers already created by the{" "}
                        <Link href="/dashboard/drivers/legacy-import" className="underline">
                            Legacy Driver Import
                        </Link>
                        . This is append-only — it never mutates or deletes an existing history row,
                        and it never touches a live driver or vehicle field.
                    </>
                }
            />

            <div className="flex gap-2 rounded-md border border-warning bg-warning/10 p-3 text-sm">
                <Info className="mt-0.5 h-4 w-4 shrink-0 text-warning" />
                <div className="space-y-1">
                    <p className="font-medium">
                        A row is only skipped, never fabricated — and only known legacy-imported
                        drivers are touched.
                    </p>
                    <p className="text-muted-foreground">
                        A <span className="font-mono">driver_id</span> with no matching phone, or a
                        phone matching a driver never tagged as legacy-imported, is skipped and
                        reported as a warning — a phone coincidence can never touch an organic
                        driver&apos;s vehicle history. A row whose value hasn&apos;t changed from the
                        previously-known value for that field is not logged again.
                    </p>
                </div>
            </div>

            <Card>
                <CardHeader>
                    <CardTitle className="flex items-center gap-2">
                        <Upload className="h-5 w-5" />
                        1. Select the two exported CSVs
                    </CardTitle>
                    <CardDescription>
                        Both files come from the same Mongo export used for{" "}
                        <span className="font-mono">Legacy Driver Import</span>.
                    </CardDescription>
                </CardHeader>
                <CardContent>
                    <div className="grid gap-4 sm:grid-cols-2">
                        {FILE_FIELDS.map((f) => (
                            <div key={f.key} className="space-y-1">
                                <Label htmlFor={`vehicle-history-backfill-${f.key}`} className="text-sm font-medium">
                                    {f.label}
                                    {files[f.key] ? (
                                        <CheckCircle2 className="ml-1 inline h-3 w-3 text-success" />
                                    ) : null}
                                </Label>
                                <Input
                                    id={`vehicle-history-backfill-${f.key}`}
                                    ref={(el) => {
                                        fileInputRefs.current[f.key] = el;
                                    }}
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
                <CardContent>
                    <Button onClick={handleValidate} disabled={!allFiles || validating || committing}>
                        {validating ? (
                            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                        ) : (
                            <Upload className="mr-2 h-4 w-4" />
                        )}
                        Validate
                    </Button>
                    {!allFiles && (
                        <p className="mt-2 text-sm text-muted-foreground">
                            Both files are required before validating.
                        </p>
                    )}
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
                        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
                            <Stat label="Vehicle rows" value={counts?.vehicle_rows ?? 0} />
                            <Stat label="History rows to insert" value={counts?.history_rows_to_insert ?? 0} />
                            <Stat label="Skipped (unmatched)" value={counts?.skipped_unmatched ?? 0} tone="warn" />
                            <Stat
                                label="Skipped (not a known legacy driver)"
                                value={counts?.skipped_not_legacy_driver ?? 0}
                                tone="warn"
                            />
                            <Stat
                                label="Skipped (already backfilled)"
                                value={counts?.skipped_already_backfilled ?? 0}
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
                                            exportToCsv(
                                                "legacy-vehicle-history-backfill-errors",
                                                report.errors,
                                                REPORT_COLUMNS,
                                            )
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
