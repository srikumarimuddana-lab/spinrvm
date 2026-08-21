"use client";

/**
 * Legacy Booking Import — previous-app ride history into `rides`.
 *
 * Four CSVs in one request (bookings, customers, drivers, driverearnings),
 * following the same validate-first / dry-run-report contract as the other
 * bulk tools on this page.
 *
 * Two guards this tool has that the others do not, because it writes to the
 * tables that feed driver payable balance:
 *   1. Commit is disabled until a validate for the CURRENT file set has come
 *      back clean. Changing any file clears the report, so you cannot review
 *      one set of files and commit another.
 *   2. Commit requires typing the confirmation phrase. It writes rides plus
 *      offsetting payouts and cannot be undone from the UI.
 */

import { useMemo, useState } from "react";
import { AlertTriangle, CheckCircle2, Info, Loader2, Upload } from "lucide-react";
import {
    adminCommitBookingImport,
    adminValidateBookingImport,
    type BookingImportCommitResult,
    type BookingImportFiles,
    type BookingImportReport,
    type BookingImportReportItem,
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

const CONFIRM_PHRASE = "IMPORT";

/** The four files, in the order an operator meets them in the export. */
const FILE_FIELDS = [
    {
        key: "bookings" as const,
        label: "Bookings",
        hint: "bookings.csv — one row per legacy booking",
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
    {
        key: "earnings" as const,
        label: "Driver earnings",
        hint: "driverearnings.csv — actual payout per booking",
    },
];

type FileState = Partial<Record<keyof BookingImportFiles, File | null>>;

function IssueTable({ items }: { items: BookingImportReportItem[] }) {
    return (
        <div className="overflow-x-auto rounded-md border">
            <Table>
                <TableHeader>
                    <TableRow>
                        <TableHead className="w-24">Row</TableHead>
                        <TableHead className="w-40">Booking</TableHead>
                        <TableHead className="w-40">Field</TableHead>
                        <TableHead>Message</TableHead>
                    </TableRow>
                </TableHeader>
                <TableBody>
                    {items.map((it, i) => (
                        <TableRow key={`${it.row_num}-${it.field}-${i}`}>
                            <TableCell className="font-mono text-xs">{it.row_num}</TableCell>
                            <TableCell className="font-mono text-xs">{it.booking_code}</TableCell>
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

export function LegacyBookingImport() {
    const { toast } = useToast();

    const [files, setFiles] = useState<FileState>({});
    const [serviceAreaName, setServiceAreaName] = useState("Saskatoon");
    const [vehicleTypeName, setVehicleTypeName] = useState("Economy");
    const [report, setReport] = useState<BookingImportReport | null>(null);
    const [committed, setCommitted] = useState<BookingImportCommitResult | null>(null);
    const [confirmText, setConfirmText] = useState("");
    const [validating, setValidating] = useState(false);
    const [committing, setCommitting] = useState(false);

    const allFiles = useMemo<BookingImportFiles | null>(() => {
        const { bookings, customers, drivers, earnings } = files;
        if (!bookings || !customers || !drivers || !earnings) return null;
        return { bookings, customers, drivers, earnings };
    }, [files]);

    // Changing any file invalidates the report: an operator must never be able
    // to review one set of files and then commit a different set.
    const setFile = (key: keyof BookingImportFiles, file: File | null) => {
        setFiles((prev) => ({ ...prev, [key]: file }));
        setReport(null);
        setCommitted(null);
        setConfirmText("");
    };

    const opts = () => ({
        serviceAreaName: serviceAreaName.trim() || "Saskatoon",
        vehicleTypeName: vehicleTypeName.trim() || "Economy",
        // Reuse the validated batch so offset payout IDs stay deterministic
        // across a validate -> commit pair and any later resume.
        ...(report?.batch ? { batch: report.batch } : {}),
    });

    const handleValidate = async () => {
        if (!allFiles) return;
        setValidating(true);
        setCommitted(null);
        try {
            setReport(await adminValidateBookingImport(allFiles, opts()));
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
            const res = await adminCommitBookingImport(allFiles, opts());
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
                        "Nothing was written. The report below shows why — most often the rows were already imported.",
                    variant: "destructive",
                });
            } else {
                setConfirmText("");
                toast({
                    title: "Import committed",
                    description: `${res.imported_rides ?? 0} ride(s) and ${res.offset_payouts ?? 0} offset payout(s) written.`,
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
                    Legacy booking import
                </CardTitle>
                <CardDescription>
                    Import completed rides from the previous app into ride history. Riders and
                    drivers are matched by phone number; a booking whose party is not in Spinr
                    imports unlinked and can be re-linked by a later run.
                </CardDescription>
            </CardHeader>
            <CardContent className="space-y-6">
                <div className="flex gap-2 rounded-md border border-amber-300 bg-amber-50 p-3 text-sm dark:border-amber-900 dark:bg-amber-950/40">
                    <Info className="mt-0.5 h-4 w-4 shrink-0 text-amber-600 dark:text-amber-400" />
                    <div className="space-y-1">
                        <p className="font-medium">Driver payouts were already settled off-platform.</p>
                        <p className="text-muted-foreground">
                            Each matched driver also gets one offsetting payout record so imported
                            earnings never become withdrawable a second time. Net payable change per
                            driver is $0.00. Always run the dry run first and check the offset total.
                        </p>
                    </div>
                </div>

                {/* 1. Files */}
                <div className="space-y-3">
                    <h3 className="text-sm font-medium">1. Select the four exported CSVs</h3>
                    <div className="grid gap-3 sm:grid-cols-2">
                        {FILE_FIELDS.map((f) => (
                            <div key={f.key} className="space-y-1">
                                <Label htmlFor={`booking-import-${f.key}`} className="text-xs">
                                    {f.label}
                                    {files[f.key] ? (
                                        <CheckCircle2 className="ml-1 inline h-3 w-3 text-success" />
                                    ) : null}
                                </Label>
                                <Input
                                    id={`booking-import-${f.key}`}
                                    type="file"
                                    accept=".csv,text/csv"
                                    onChange={(e) => setFile(f.key, e.target.files?.[0] ?? null)}
                                />
                                <p className="text-xs text-muted-foreground">{f.hint}</p>
                            </div>
                        ))}
                    </div>
                    <div className="grid gap-3 sm:grid-cols-2">
                        <div className="space-y-1">
                            <Label htmlFor="booking-import-area" className="text-xs">
                                Service area
                            </Label>
                            <Input
                                id="booking-import-area"
                                value={serviceAreaName}
                                onChange={(e) => setServiceAreaName(e.target.value)}
                            />
                        </div>
                        <div className="space-y-1">
                            <Label htmlFor="booking-import-vehicle" className="text-xs">
                                Vehicle type
                            </Label>
                            <Input
                                id="booking-import-vehicle"
                                value={vehicleTypeName}
                                onChange={(e) => setVehicleTypeName(e.target.value)}
                            />
                        </div>
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
                            All four files are required before validating.
                        </p>
                    ) : null}
                </div>

                {/* 3. Report + commit */}
                {report && c ? (
                    <div className="space-y-4">
                        <h3 className="text-sm font-medium">3. Review and commit</h3>

                        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
                            <Stat label="Rides to import" value={c.rides_planned} />
                            <Stat label="Offset payouts" value={c.payouts_planned} />
                            <Stat label="Unmatched riders" value={c.unmatched_riders} tone="warn" />
                            <Stat label="Unmatched drivers" value={c.unmatched_drivers} tone="warn" />
                            <Stat label="Rider paid (total)" value={c.sum_rider_paid} money />
                            <Stat label="Driver earnings" value={c.sum_driver_total} money />
                            <Stat label="Offset total" value={c.sum_offset_payouts} money />
                            <Stat label="Already imported" value={c.skipped_already_imported} />
                        </div>

                        <p className="text-xs text-muted-foreground">
                            Read {c.bookings_read} booking(s); {c.skipped_not_completed} not
                            completed, {c.skipped_test_account} test account(s),{" "}
                            {c.skipped_unmatched_both} with neither party matched. Batch{" "}
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
                                            exportToCsv("booking-import-errors", report.errors, [
                                                { key: "row_num", label: "Row" },
                                                { key: "booking_code", label: "Booking" },
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
                            <div className="flex items-center gap-2 rounded-md border border-green-300 bg-green-50 p-3 text-sm dark:border-green-900 dark:bg-green-950/40">
                                <CheckCircle2 className="h-4 w-4 text-success" />
                                <span>
                                    Imported {committed.imported_rides} ride(s) and{" "}
                                    {committed.offset_payouts} offset payout(s);{" "}
                                    {committed.drivers_recounted} driver counter(s) recomputed.
                                </span>
                            </div>
                        ) : report.can_commit ? (
                            <div className="space-y-2 rounded-md border p-3">
                                <Label htmlFor="booking-import-confirm" className="text-xs">
                                    This writes {c.rides_planned} ride(s) and {c.payouts_planned}{" "}
                                    payout record(s) to live data and cannot be undone from here.
                                    Type <span className="font-mono">{CONFIRM_PHRASE}</span> to
                                    enable.
                                </Label>
                                <div className="flex gap-2">
                                    <Input
                                        id="booking-import-confirm"
                                        value={confirmText}
                                        onChange={(e) => setConfirmText(e.target.value)}
                                        placeholder={CONFIRM_PHRASE}
                                        className="max-w-40 font-mono"
                                    />
                                    <Button onClick={handleCommit} disabled={!canCommit}>
                                        {committing ? (
                                            <>
                                                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                                Committing…
                                            </>
                                        ) : (
                                            "Commit import"
                                        )}
                                    </Button>
                                </div>
                            </div>
                        ) : (
                            <p className="text-sm text-muted-foreground">
                                Nothing to commit — fix the errors above, or every booking in this
                                export has already been imported.
                            </p>
                        )}
                    </div>
                ) : null}
            </CardContent>
        </Card>
    );
}
