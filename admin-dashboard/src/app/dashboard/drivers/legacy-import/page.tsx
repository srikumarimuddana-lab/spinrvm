"use client";

import { useRef, useState } from "react";
import {
    Upload,
    FileDown,
    CheckCircle2,
    AlertTriangle,
    Loader2,
    Info,
} from "lucide-react";
import {
    adminValidateLegacyDriverImport,
    adminCommitLegacyDriverImport,
    type LegacyDriverImportReport,
    type LegacyDriverImportReportItem,
} from "@/lib/api";
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

// A minimal, realistic-shaped sample matching the RAW Mongo export's own
// column names (backend/services/driver_import_service.py's
// REQUIRED_MONGO_DRIVER_COLUMNS = {"_id", "name", "phone"}; the rest are
// read if present, ignored otherwise). This is NOT the same template as
// Bulk Driver Import's (that one is the bespoke Saskatoon recruitment CSV
// shape) — do not merge the two templates, the backend reads them with two
// different, deliberately separate parsers.
const TEMPLATE_HEADER = [
    "_id",
    "name",
    "phone",
    "email",
    "ratings",
    "created_at",
    "is_deleted",
    "is_block",
    "set_up_profile",
];
const TEMPLATE_SAMPLE = [
    "6923ea32d1bde481895439f4",
    "Jane Doe",
    "3065551234",
    "jane@example.com",
    "4.5",
    "1700000000000",
    "false",
    "false",
    "true",
];

function downloadTemplate() {
    const csv = `${TEMPLATE_HEADER.join(",")}\n${TEMPLATE_SAMPLE.join(",")}\n`;
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "spinr-legacy-mongo-driver-import-template.csv";
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
}

const REPORT_COLUMNS = [
    { key: "old_driver_id", label: "old_driver_id" },
    { key: "field", label: "field" },
    { key: "message", label: "message" },
];

function IssueTable({ items }: { items: LegacyDriverImportReportItem[] }) {
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

export default function LegacyDriverImportPage() {
    const { allowed } = useRequireModule("drivers");
    const { toast } = useToast();
    const fileInputRef = useRef<HTMLInputElement>(null);

    const [file, setFile] = useState<File | null>(null);
    const [serviceAreaName, setServiceAreaName] = useState("Saskatoon");
    // Name-only lookup is ambiguous in production: "Saskatoon" and "Saskatoon
    // Airport" both match get_service_area()'s `ilike("name", "%Saskatoon%")`,
    // which then refuses to guess and requires an explicit id. Default to the
    // real Saskatoon service area's id so the common case (this page is
    // Saskatoon-specific, same as the name default above) works without the
    // operator needing to know about the ambiguity; the field stays editable
    // for any other service area.
    const [serviceAreaId, setServiceAreaId] = useState("361d17bb-ec55-4561-943f-e3bbee5d7a55");
    const [report, setReport] = useState<LegacyDriverImportReport | null>(null);
    const [validating, setValidating] = useState(false);
    const [committing, setCommitting] = useState(false);
    const [committedSummary, setCommittedSummary] = useState<string | null>(null);

    if (!allowed) return null;

    const importOpts = () => ({
        serviceAreaName: serviceAreaName.trim() || "Saskatoon",
        serviceAreaId: serviceAreaId.trim() || undefined,
    });

    const resetReport = () => {
        setReport(null);
        setCommittedSummary(null);
    };

    const onPickFile = (f: File | null) => {
        setFile(f);
        resetReport();
    };

    const handleValidate = async () => {
        if (!file) return;
        setValidating(true);
        setCommittedSummary(null);
        try {
            const rep = await adminValidateLegacyDriverImport(file, importOpts());
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
            // batch + validationToken must come from this exact report — the
            // backend binds the token to (batch, CSV bytes, admin) and
            // refuses commit otherwise.
            const res = await adminCommitLegacyDriverImport(file, {
                ...importOpts(),
                batch: report.batch,
                validationToken: report.validation_token,
            });
            if (res.committed) {
                setCommittedSummary(
                    `Created ${res.new_drivers ?? 0} new driver(s) (${res.new_users ?? 0} new account(s)).` +
                        (res.linked_accounts
                            ? ` Linked ${res.linked_accounts} new driver profile(s) to existing account(s).`
                            : "") +
                        (res.enriched_drivers
                            ? ` Enriched ${res.enriched_drivers} existing driver(s) with legacy history.`
                            : "") +
                        (res.warnings && res.warnings.length ? ` ${res.warnings.length} warning(s).` : ""),
                );
                setReport(null);
                setFile(null);
                if (fileInputRef.current) fileInputRef.current.value = "";
                toast({
                    title: "Import complete",
                    description: `${res.new_drivers ?? 0} new, ${res.linked_accounts ?? 0} linked, ${res.enriched_drivers ?? 0} enriched.`,
                });
            } else {
                // The CSV no longer validates (data changed since validate).
                setReport({
                    batch: res.batch,
                    can_commit: false,
                    counts:
                        res.counts ?? {
                            rows: 0,
                            new_users: 0,
                            new_drivers: 0,
                            linked_accounts: 0,
                            enriched_drivers: 0,
                            skipped_resume: 0,
                        },
                    warnings: res.warnings ?? [],
                    errors: res.errors ?? [],
                });
                toast({
                    title: "Import refused",
                    description: "The CSV has validation errors. Fix them and try again.",
                    variant: "destructive",
                });
            }
        } catch (e) {
            toast({
                title: "Import failed",
                description: e instanceof Error ? e.message : "Could not commit the import",
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
                <h1 className="text-2xl font-semibold">Legacy Driver Import (Mongo export)</h1>
                <p className="text-sm text-muted-foreground">
                    Import driver profiles from the previous app&apos;s raw MongoDB export
                    (<span className="font-mono">drivers.csv</span>). A separate population from
                    the Saskatoon recruitment sheet — see{" "}
                    <Link href="/dashboard/drivers/import" className="underline">
                        Bulk Driver Import
                    </Link>{" "}
                    for that one. Every newly-created driver here is forced{" "}
                    <span className="font-mono">needs_review</span>, unverified, and offline
                    regardless of what the export says — no document files are imported (the
                    export only has filenames, no images). Once a driver exists here, backfill
                    their{" "}
                    <Link href="/dashboard/drivers/legacy-sin-dob-backfill" className="underline">
                        SIN/DOB
                    </Link>{" "}
                    or{" "}
                    <Link href="/dashboard/drivers/legacy-vehicle-history-backfill" className="underline">
                        vehicle history
                    </Link>{" "}
                    from the same export.
                </p>
            </div>

            <div className="flex gap-2 rounded-md border border-warning bg-warning/10 p-3 text-sm">
                <Info className="mt-0.5 h-4 w-4 shrink-0 text-warning" />
                <div className="space-y-1">
                    <p className="font-medium">A row can create, link, or enrich — not always a new row.</p>
                    <p className="text-muted-foreground">
                        A phone matching an existing account with no driver yet gets a NEW driver
                        linked to that account (no duplicate account). A phone matching an
                        existing driver gets that driver&apos;s history enriched instead — no
                        competing row is created, and none of that driver&apos;s live fields
                        (name, phone, status, vehicle, rating) are ever touched.
                    </p>
                </div>
            </div>

            <Card>
                <CardHeader>
                    <CardTitle>1. Prepare your CSV</CardTitle>
                    <CardDescription>
                        Start from the template, or use the raw <span className="font-mono">drivers.csv</span>{" "}
                        from the Mongo export directly — only <span className="font-mono">_id</span>,{" "}
                        <span className="font-mono">name</span>, and <span className="font-mono">phone</span>{" "}
                        are required.
                    </CardDescription>
                </CardHeader>
                <CardContent>
                    <Button variant="outline" onClick={downloadTemplate}>
                        <FileDown className="mr-2 h-4 w-4" />
                        Download CSV template
                    </Button>
                </CardContent>
            </Card>

            <Card>
                <CardHeader>
                    <CardTitle>2. Upload &amp; validate</CardTitle>
                    <CardDescription>
                        Validation is a dry run — nothing is written until you commit.
                    </CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                    <div className="grid gap-4 sm:grid-cols-2">
                        <div className="space-y-1">
                            <label htmlFor="legacy-drivers-csv" className="text-sm font-medium">
                                Drivers CSV (raw Mongo export)
                            </label>
                            <Input
                                id="legacy-drivers-csv"
                                ref={fileInputRef}
                                type="file"
                                accept=".csv,text/csv"
                                onChange={(e) => onPickFile(e.target.files?.[0] ?? null)}
                            />
                        </div>
                        <div className="space-y-1">
                            <label htmlFor="legacy-service-area" className="text-sm font-medium">
                                Service area (name)
                            </label>
                            <Input
                                id="legacy-service-area"
                                value={serviceAreaName}
                                onChange={(e) => {
                                    setServiceAreaName(e.target.value);
                                    resetReport();
                                }}
                                placeholder="Saskatoon"
                            />
                        </div>
                        <div className="space-y-1">
                            <label htmlFor="legacy-service-area-id" className="text-sm font-medium">
                                Service area ID (optional — required if the name matches more than one area)
                            </label>
                            <Input
                                id="legacy-service-area-id"
                                value={serviceAreaId}
                                onChange={(e) => {
                                    setServiceAreaId(e.target.value);
                                    resetReport();
                                }}
                                placeholder="e.g. 361d17bb-ec55-4561-943f-e3bbee5d7a55"
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
                                ? "No errors — you can commit this import."
                                : "Fix the errors below, re-export your CSV, and validate again."}
                        </CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-5">
                        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                            <Stat label="Rows" value={counts?.rows ?? 0} />
                            <Stat label="New drivers" value={counts?.new_drivers ?? 0} />
                            <Stat label="Linked to existing account" value={counts?.linked_accounts ?? 0} />
                            <Stat label="Enriched existing driver" value={counts?.enriched_drivers ?? 0} />
                            <Stat label="Skipped (already imported)" value={counts?.skipped_resume ?? 0} />
                            <Stat label="Warnings" value={report.warnings.length} tone="warn" />
                            <Stat label="Errors" value={report.errors.length} tone="error" />
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
                                            exportToCsv("legacy-driver-import-errors", report.errors, REPORT_COLUMNS)
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
                                Commit import
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
