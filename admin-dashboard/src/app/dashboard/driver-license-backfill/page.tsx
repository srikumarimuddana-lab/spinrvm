"use client";

import { useCallback, useEffect, useState } from "react";
import { FileText, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Table, TableHeader, TableBody, TableHead, TableRow, TableCell } from "@/components/ui/table";
import { useToast } from "@/components/ui/use-toast";
import { useRequireModule } from "@/hooks/useRequireModule";
import { getDrivers, updateDriver } from "@/lib/api";
import { DocumentReviewer } from "../drivers/_components/document-reviewer";

interface GapDriver {
    id: string;
    name?: string;
    first_name?: string;
    last_name?: string;
    email?: string;
    status?: string;
    license_number?: string | null;
    license_class?: string | null;
}

/**
 * ACTION_ITEMS.md B14 immediate-remediation tool: one-time backfill queue
 * for the 22 (as of 2026-07-29) drivers with NULL license_number/
 * license_class -- see docs/proposals/2026-07-29-driver-document-ocr-onboarding-automation.md.
 *
 * Deliberately manual, not automated: an admin views each driver's already-
 * uploaded license document (reusing the existing DocumentReviewer) and
 * transcribes the number/class themselves. No OCR here -- the proposal doc
 * explicitly recommends never auto-trusting an unverified OCR read onto a
 * regulator-facing SGI form, and this backend has no OCR pipeline yet
 * anyway. This page exists to make the existing safe update path
 * (PUT /admin/drivers/{id}, which now correctly Vault-encrypts
 * license_number) easy to reach for exactly this queue, not to guess.
 */
export default function DriverLicenseBackfillPage() {
    const { allowed } = useRequireModule("drivers");
    const { toast } = useToast();
    const [drivers, setDrivers] = useState<GapDriver[]>([]);
    const [loading, setLoading] = useState(true);
    const [edits, setEdits] = useState<Record<string, { license_number: string; license_class: string }>>({});
    const [savingId, setSavingId] = useState<string | null>(null);
    const [reviewerDriver, setReviewerDriver] = useState<GapDriver | null>(null);

    const load = useCallback(() => {
        setLoading(true);
        getDrivers({ missing_license: true, limit: 200 })
            .then((rows) => {
                const list: GapDriver[] = Array.isArray(rows) ? rows : [];
                setDrivers(list);
                setEdits((prev) => {
                    const next = { ...prev };
                    for (const d of list) {
                        if (!next[d.id]) {
                            next[d.id] = {
                                license_number: d.license_number || "",
                                license_class: d.license_class || "",
                            };
                        }
                    }
                    return next;
                });
            })
            .catch((e) => toast({ title: "Could not load drivers", description: String(e?.message || e), variant: "destructive" }))
            .finally(() => setLoading(false));
    }, [toast]);

    useEffect(() => { load(); }, [load]);

    if (!allowed) return null;

    const setEdit = (id: string, field: "license_number" | "license_class", value: string) => {
        setEdits((prev) => ({ ...prev, [id]: { ...prev[id], [field]: value } }));
    };

    const save = async (driver: GapDriver) => {
        const edit = edits[driver.id];
        if (!edit?.license_number.trim() || !edit?.license_class.trim()) {
            toast({ title: "Both fields required", description: "Enter the license number and class before saving.", variant: "destructive" });
            return;
        }
        setSavingId(driver.id);
        try {
            await updateDriver(driver.id, {
                license_number: edit.license_number.trim(),
                license_class: edit.license_class.trim(),
            });
            toast({ title: "Saved", description: `${driver.name || driver.first_name || driver.id} updated — removed from this queue.` });
            setDrivers((prev) => prev.filter((d) => d.id !== driver.id));
        } catch (e: any) {
            toast({ title: "Save failed", description: String(e?.message || e), variant: "destructive" });
        } finally {
            setSavingId(null);
        }
    };

    return (
        <div className="p-6 space-y-6">
            <div>
                <h1 className="text-2xl font-semibold">Driver Licence Backfill</h1>
                <p className="text-muted-foreground">
                    Drivers missing a licence number or class — SGI D00032 renders these fields blank until they&apos;re
                    filled in here. View each driver&apos;s uploaded licence document, then transcribe the number and
                    class exactly as printed. See ACTION_ITEMS.md B14 for background.
                </p>
            </div>

            <Card>
                <CardHeader>
                    <CardTitle>Pending ({drivers.length})</CardTitle>
                    <CardDescription>
                        Both fields are required before saving. Saving removes the driver from this list.
                    </CardDescription>
                </CardHeader>
                <CardContent>
                    {loading ? (
                        <div className="flex items-center gap-2 text-muted-foreground py-8 justify-center">
                            <Loader2 className="h-4 w-4 animate-spin" /> Loading…
                        </div>
                    ) : drivers.length === 0 ? (
                        <p className="text-muted-foreground py-8 text-center">
                            No drivers are missing a licence number or class.
                        </p>
                    ) : (
                        <Table>
                            <TableHeader>
                                <TableRow>
                                    <TableHead>Driver</TableHead>
                                    <TableHead>Status</TableHead>
                                    <TableHead>Document</TableHead>
                                    <TableHead>Licence number</TableHead>
                                    <TableHead>Licence class</TableHead>
                                    <TableHead />
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                {drivers.map((d) => {
                                    const edit = edits[d.id] || { license_number: "", license_class: "" };
                                    return (
                                        <TableRow key={d.id}>
                                            <TableCell>
                                                <div className="font-medium">{d.name || `${d.first_name || ""} ${d.last_name || ""}`.trim() || d.id}</div>
                                                <div className="text-xs text-muted-foreground">{d.email}</div>
                                            </TableCell>
                                            <TableCell className="text-sm text-muted-foreground">{d.status || "—"}</TableCell>
                                            <TableCell>
                                                <Button variant="outline" size="sm" onClick={() => setReviewerDriver(d)}>
                                                    <FileText className="h-3.5 w-3.5 mr-1.5" /> View documents
                                                </Button>
                                            </TableCell>
                                            <TableCell>
                                                <Input
                                                    value={edit.license_number}
                                                    onChange={(e) => setEdit(d.id, "license_number", e.target.value)}
                                                    placeholder="e.g. S1234567"
                                                    className="w-[160px]"
                                                />
                                            </TableCell>
                                            <TableCell>
                                                <Input
                                                    value={edit.license_class}
                                                    onChange={(e) => setEdit(d.id, "license_class", e.target.value)}
                                                    placeholder="e.g. 5"
                                                    className="w-[100px]"
                                                />
                                            </TableCell>
                                            <TableCell>
                                                <Button size="sm" onClick={() => save(d)} disabled={savingId === d.id}>
                                                    {savingId === d.id ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : "Save"}
                                                </Button>
                                            </TableCell>
                                        </TableRow>
                                    );
                                })}
                            </TableBody>
                        </Table>
                    )}
                </CardContent>
            </Card>

            <DocumentReviewer
                open={!!reviewerDriver}
                driverId={reviewerDriver?.id || null}
                driverName={reviewerDriver?.name}
                onClose={() => setReviewerDriver(null)}
            />
        </div>
    );
}
