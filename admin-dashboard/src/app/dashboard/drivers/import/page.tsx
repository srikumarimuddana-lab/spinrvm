"use client";

import { useEffect, useRef, useState } from "react";
import {
    Upload,
    FileDown,
    CheckCircle2,
    AlertTriangle,
    Loader2,
    Info,
} from "lucide-react";
import {
    adminValidateDriverImport,
    adminCommitDriverImport,
    getServiceAreas,
    type DriverImportReport,
    type DriverImportReportItem,
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
import { useRequireModule } from "@/hooks/useRequireModule";
import { exportToCsv } from "@/lib/export-csv";

// Header for the downloadable template. The first nine columns are required by
// the backend (REQUIRED_DRIVER_COLUMNS); the rest are optional but commonly
// used. Keep in sync with services/driver_import_service.py normalize_header.
const TEMPLATE_HEADER = [
    "old_driver_id",
    "full_name",
    "phone",
    "email",
    "vehicle_plate",
    "vehicle_type",
    "vehicle_year",
    "vehicle_make",
    "vehicle_model",
    "vehicle_color",
    "vin",
    "date_of_birth",
    "license_number",
    "license_class",
    "license_expiry",
    "insurance_expiry",
    "vehicle_inspection_expiry",
    "criminal_record_check_expiry",
    "work_authorization_expiry",
    "spinr_approved",
    "approved_from_sgi",
    "pr",
    "citizen",
];
const TEMPLATE_SAMPLE = [
    "LEGACY-001",
    "Jane Doe",
    "3065551234",
    "jane@example.com",
    "ABC123",
    "Sedan",
    "2020",
    "Toyota",
    "Corolla",
    "Black",
    "2T1BURHE0JC123456",
    "1990-05-14",
    "D1234567",
    "5",
    "2027-05-14",
    "2026-11-01",
    "2026-09-30",
    "2026-08-15",
    "indefinite",
    "yes",
    "yes",
    "no",
    "yes",
];

function downloadTemplate() {
    const csv = `${TEMPLATE_HEADER.join(",")}\n${TEMPLATE_SAMPLE.join(",")}\n`;
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "spinr-driver-import-template.csv";
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

function IssueTable({ items }: { items: DriverImportReportItem[] }) {
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

export default function BulkImportPage() {
    const { allowed } = useRequireModule("drivers");
    const { toast } = useToast();
    const fileInputRef = useRef<HTMLInputElement>(null);

    const [file, setFile] = useState<File | null>(null);
    const [serviceAreas, setServiceAreas] = useState<Array<{ id: string; name?: string }>>([]);
    const [serviceAreaId, setServiceAreaId] = useState<string>("");
    const [report, setReport] = useState<DriverImportReport | null>(null);
    const [validating, setValidating] = useState(false);
    const [committing, setCommitting] = useState(false);
    const [committedSummary, setCommittedSummary] = useState<string | null>(null);

    useEffect(() => {
        getServiceAreas()
            .then((rows) => setServiceAreas(Array.isArray(rows) ? rows : []))
            .catch(() => setServiceAreas([]));
    }, []);

    if (!allowed) return null;

    const importOpts = () =>
        serviceAreaId ? { serviceAreaId } : { serviceAreaName: "Saskatoon" };

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
            const rep = await adminValidateDriverImport(file, importOpts());
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
            const res = await adminCommitDriverImport(file, importOpts());
            if (res.committed) {
                setCommittedSummary(
                    `Imported ${res.imported_drivers ?? 0} drivers (${res.imported_users ?? 0} accounts).` +
                        (res.warnings && res.warnings.length ? ` ${res.warnings.length} warning(s).` : ""),
                );
                setReport(null);
                setFile(null);
                if (fileInputRef.current) fileInputRef.current.value = "";
                toast({ title: "Import complete", description: `${res.imported_drivers ?? 0} drivers imported.` });
            } else {
                // The CSV no longer validates (data changed since validate).
                setReport({
                    batch: res.batch,
                    can_commit: false,
                    counts: res.counts ?? { rows: 0, users: 0, drivers: 0, skipped_resume: 0 },
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
                <h1 className="text-2xl font-semibold">Bulk Driver Import</h1>
                <p className="text-sm text-muted-foreground">
                    Upload a CSV of drivers to create their accounts and profiles. Validate first, review
                    the report, then commit. Document files are uploaded per-driver afterwards from the
                    driver&apos;s page.
                </p>
            </div>

            <Card>
                <CardHeader>
                    <CardTitle>1. Prepare your CSV</CardTitle>
                    <CardDescription>
                        Start from the template. The first nine columns are required; the rest are optional.
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
                            <label htmlFor="drivers-csv" className="text-sm font-medium">
                                Drivers CSV
                            </label>
                            <Input
                                id="drivers-csv"
                                ref={fileInputRef}
                                type="file"
                                accept=".csv,text/csv"
                                onChange={(e) => onPickFile(e.target.files?.[0] ?? null)}
                            />
                        </div>
                        <div className="space-y-1">
                            <label htmlFor="service-area" className="text-sm font-medium">
                                Service area
                            </label>
                            <Select
                                value={serviceAreaId || "__default__"}
                                onValueChange={(v) => {
                                    setServiceAreaId(v === "__default__" ? "" : v);
                                    resetReport();
                                }}
                            >
                                <SelectTrigger id="service-area">
                                    <SelectValue placeholder="Saskatoon (default)" />
                                </SelectTrigger>
                                <SelectContent>
                                    <SelectItem value="__default__">Saskatoon (default)</SelectItem>
                                    {serviceAreas.map((sa) => (
                                        <SelectItem key={sa.id} value={sa.id}>
                                            {sa.name || sa.id}
                                        </SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
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
                                ? "No errors — you can commit this import."
                                : "Fix the errors below, re-export your CSV, and validate again."}
                        </CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-5">
                        <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
                            <Stat label="Rows" value={counts?.rows ?? 0} />
                            <Stat label="To create" value={counts?.drivers ?? 0} />
                            <Stat label="Skipped (already imported)" value={counts?.skipped_resume ?? 0} />
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
                                            exportToCsv("driver-import-errors", report.errors, REPORT_COLUMNS)
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
                            <Button
                                onClick={handleCommit}
                                disabled={!report.can_commit || committing}
                            >
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
