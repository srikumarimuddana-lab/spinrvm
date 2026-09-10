"use client";

/**
 * Legacy Saved-Address Backfill — Phase 4 of the 2026-08-27 migration plan
 * (docs/migration/2026-08-27-legacy-data-full-migration-approach.md §4).
 *
 * Backfills rider saved addresses from the previous app's raw Mongo export
 * into the existing, live `saved_addresses` table (routes/addresses.py) —
 * the same destination a rider's own self-serve "save an address" action
 * already writes to. Not a new feature; the plan doc's original claim that
 * no matching Spinr table existed was wrong (confirmed by reading the
 * actual schema before building this).
 *
 * Takes two CSVs: customer_addresses.csv (lat/lng/address text/home-or-work
 * type, keyed by a Mongo ObjectId customer_id) and customers.csv (the same
 * export's customer collection, used only to resolve that ObjectId to a
 * phone number — the same crosswalk role drivers.csv plays for the
 * driver-side backfills). Rows outside a Saskatchewan bounding box are
 * excluded server-side (confirmed against the real export: 20 of 301 raw
 * rows are India-based test/junk data, same class already found in the
 * rider CSV import).
 *
 * Same validate → review → commit flow as the other two-CSV backfills.
 */

import { useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { CheckCircle2, AlertTriangle, Loader2, Info, Upload, Copy } from "lucide-react";
import {
    adminValidateSavedAddressBackfill,
    adminCommitSavedAddressBackfill,
    type SavedAddressBackfillFiles,
    type SavedAddressBackfillReport,
    type SavedAddressBackfillReportItem,
} from "@/lib/api";
import { explainSavedAddressIssue } from "@/lib/bulk-import-error-help";
import { Button } from "@/components/ui/button";
import { PageHeader } from "@/components/page-header";
import { BackToMigrationChecklistLink, BULK_OPERATIONS_HREF } from "@/components/bulk-operations-nav";
import { WhatThisToolDoes } from "@/components/bulk-import/what-this-tool-does";
import { StatTile } from "@/components/bulk-import/stat-tile";
import { IssueTable } from "@/components/bulk-import/issue-table";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
    Card,
    CardHeader,
    CardTitle,
    CardDescription,
    CardContent,
} from "@/components/ui/card";
import { ToastAction } from "@/components/ui/toast";
import { useToast } from "@/components/ui/use-toast";
import { useRequireModule } from "@/hooks/useRequireModule";
import { useUnsavedChangesWarning } from "@/hooks/useUnsavedChangesWarning";
import { exportToCsv } from "@/lib/export-csv";

/** The two files, in the order an operator meets them in the export. */
const FILE_FIELDS = [
    {
        key: "addresses" as const,
        label: "Customer addresses",
        hint: "customer_addresses.csv — lat/lng/address text/type, keyed by customer_id",
    },
    {
        key: "customers" as const,
        label: "Customers",
        hint: "customers.csv — the raw Mongo export's customer collection, used only to resolve customer_id → phone",
    },
];

type FileState = Partial<Record<keyof SavedAddressBackfillFiles, File | null>>;

const REPORT_COLUMNS = [
    { key: "row_num", label: "row_num" },
    { key: "field", label: "field" },
    { key: "message", label: "message" },
];

function SavedAddressIssueTable({ items }: { items: SavedAddressBackfillReportItem[] }) {
    return (
        <IssueTable
            items={items}
            getRowKey={(it, i) => `${it.row_num}-${it.field}-${i}`}
            getRef={(it) => String(it.row_num)}
            getField={(it) => it.field}
            getMessage={(it) => it.message}
            refLabel="Row"
            explain={explainSavedAddressIssue}
        />
    );
}

// Compact, copy-pasteable markdown table mirroring the stat tiles below —
// counts only, no raw address/lat-lng, matching the report's own
// no-raw-address-data guarantee.
function buildSummaryText(report: SavedAddressBackfillReport): string {
    const c = report.counts;
    const rows: [string, number][] = [
        ["Address rows", c?.address_rows ?? 0],
        ["Addresses to insert", c?.addresses_to_insert ?? 0],
        ["Skipped (out of province)", c?.skipped_out_of_province ?? 0],
        ["Skipped (unmatched customer)", c?.skipped_unmatched_customer ?? 0],
        ["Skipped (no matching rider)", c?.skipped_no_rider ?? 0],
        ["Skipped (already imported)", c?.skipped_already_imported ?? 0],
        ["Warnings", report.warnings.length],
        ["Errors", report.errors.length],
    ];
    const lines = [
        `Legacy Saved-Address Backfill — batch ${report.batch}`,
        "| Metric | Count |",
        "|---|---|",
        ...rows.map(([label, value]) => `| ${label} | ${value} |`),
    ];
    return lines.join("\n");
}

export default function LegacySavedAddressBackfillPage() {
    const { allowed } = useRequireModule("users");
    const { toast } = useToast();
    const router = useRouter();
    const fileInputRefs = useRef<Partial<Record<keyof SavedAddressBackfillFiles, HTMLInputElement | null>>>({});

    const [files, setFiles] = useState<FileState>({});
    const [report, setReport] = useState<SavedAddressBackfillReport | null>(null);
    const [validating, setValidating] = useState(false);
    const [committing, setCommitting] = useState(false);
    const [committedSummary, setCommittedSummary] = useState<string | null>(null);

    useUnsavedChangesWarning(!!report?.can_commit && !committedSummary);

    if (!allowed) return null;

    const allFiles: SavedAddressBackfillFiles | null =
        files.addresses && files.customers ? { addresses: files.addresses, customers: files.customers } : null;

    const resetReport = () => {
        setReport(null);
        setCommittedSummary(null);
    };

    // Changing either file invalidates the report — an operator must never
    // review one pair of files and then commit a different pair.
    const setFile = (key: keyof SavedAddressBackfillFiles, file: File | null) => {
        setFiles((prev) => ({ ...prev, [key]: file }));
        resetReport();
    };

    const handleValidate = async () => {
        if (!allFiles) return;
        setValidating(true);
        setCommittedSummary(null);
        try {
            const rep = await adminValidateSavedAddressBackfill(allFiles);
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
            const res = await adminCommitSavedAddressBackfill(allFiles, {
                batch: report.batch,
                validationToken: report.validation_token,
            });
            if (res.committed) {
                setCommittedSummary(
                    `Inserted ${res.addresses_inserted ?? 0} saved address(es).` +
                        (res.warnings && res.warnings.length ? ` ${res.warnings.length} warning(s).` : ""),
                );
                setReport(null);
                setFiles({});
                Object.values(fileInputRefs.current).forEach((el) => {
                    if (el) el.value = "";
                });
                toast({
                    title: "Backfill complete",
                    description: `${res.addresses_inserted ?? 0} address(es) inserted.`,
                    action: (
                        <ToastAction
                            altText="Go to Migration Checklist"
                            onClick={() => router.push(BULK_OPERATIONS_HREF)}
                        >
                            Go to Checklist
                        </ToastAction>
                    ),
                });
            } else {
                // The CSVs no longer validate (data changed since validate).
                setReport({
                    batch: res.batch,
                    can_commit: false,
                    counts:
                        res.counts ?? {
                            address_rows: 0,
                            addresses_to_insert: 0,
                            skipped_out_of_province: 0,
                            skipped_unmatched_customer: 0,
                            skipped_no_rider: 0,
                            skipped_already_imported: 0,
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
            <BackToMigrationChecklistLink className="-ml-3" />

            <PageHeader
                title="Legacy Saved-Address Backfill"
                description={
                    <>
                        Backfill rider saved addresses for riders already in{" "}
                        <Link href="/dashboard/records?tab=bulk-operations" className="underline">
                            Bulk Rider Import
                        </Link>
                        .
                    </>
                }
            />

            <WhatThisToolDoes
                what={
                    <>
                        Fills in a rider&apos;s saved &quot;home&quot;/&quot;work&quot; addresses —
                        the same address book a rider can build themselves in the app — from the
                        previous app&apos;s raw Mongo export, for riders already created by Bulk
                        Rider Import.
                    </>
                }
                why={
                    <>
                        Without this backfill, a migrated rider would need to re-type every saved
                        address by hand after moving to Spinr, even though they already gave us
                        that information once.
                    </>
                }
                whichFiles={
                    <>
                        The same raw MongoDB export&apos;s{" "}
                        <span className="font-mono">customer_addresses.csv</span> (lat/lng/address
                        text/type, keyed by customer_id) and{" "}
                        <span className="font-mono">customers.csv</span> (used only to resolve
                        customer_id to a phone number).
                    </>
                }
                value={
                    <>
                        A rider&apos;s home and work addresses are already there the first time
                        they book a ride on Spinr — no re-entry needed.
                    </>
                }
                safetyNote={
                    <>
                        A row is only skipped, never fabricated — and only real Spinr riders are
                        touched. A row outside Saskatchewan, with no matching customer row, or with
                        no matching Spinr rider account is skipped and reported, not written. An
                        address already saved for that rider (same text) is skipped too — safe to
                        re-run.
                    </>
                }
            />

            <Card>
                <CardHeader>
                    <CardTitle className="flex items-center gap-2">
                        <Upload className="h-5 w-5" />
                        1. Select the two exported CSVs
                    </CardTitle>
                    <CardDescription>
                        Both files come from the same Mongo export used for{" "}
                        <span className="font-mono">Bulk Rider Import</span>.
                    </CardDescription>
                </CardHeader>
                <CardContent>
                    <div className="grid gap-4 sm:grid-cols-2">
                        {FILE_FIELDS.map((f) => (
                            <div key={f.key} className="space-y-1">
                                <Label htmlFor={`saved-address-backfill-${f.key}`} className="text-sm font-medium">
                                    {f.label}
                                    {files[f.key] ? (
                                        <CheckCircle2 className="ml-1 inline h-3 w-3 text-success" />
                                    ) : null}
                                </Label>
                                <Input
                                    id={`saved-address-backfill-${f.key}`}
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
                    <CardContent className="flex flex-wrap items-center justify-between gap-3 py-4">
                        <div className="flex items-center gap-3">
                            <CheckCircle2 className="h-5 w-5 shrink-0 text-success" />
                            <span className="text-sm">{committedSummary}</span>
                        </div>
                        <BackToMigrationChecklistLink />
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
                            <StatTile label="Address rows" value={counts?.address_rows ?? 0} />
                            <StatTile label="Addresses to insert" value={counts?.addresses_to_insert ?? 0} />
                            <StatTile
                                label="Skipped (out of province)"
                                value={counts?.skipped_out_of_province ?? 0}
                                tone="warn"
                            />
                            <StatTile
                                label="Skipped (unmatched customer)"
                                value={counts?.skipped_unmatched_customer ?? 0}
                                tone="warn"
                            />
                            <StatTile
                                label="Skipped (no matching rider)"
                                value={counts?.skipped_no_rider ?? 0}
                                tone="warn"
                            />
                            <StatTile label="Skipped (already imported)" value={counts?.skipped_already_imported ?? 0} />
                            <StatTile label="Warnings" value={report.warnings.length} tone="warn" />
                            <StatTile label="Errors" value={report.errors.length} tone="error" />
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
                                                "legacy-saved-address-backfill-errors",
                                                report.errors,
                                                REPORT_COLUMNS,
                                            )
                                        }
                                    >
                                        Download errors
                                    </Button>
                                </div>
                                <SavedAddressIssueTable items={report.errors} />
                            </div>
                        )}

                        {report.warnings.length > 0 && (
                            <div className="space-y-2">
                                <h3 className="flex items-center gap-2 text-sm font-semibold text-warning">
                                    <Info className="h-4 w-4" /> Warnings ({report.warnings.length})
                                </h3>
                                <SavedAddressIssueTable items={report.warnings} />
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
