"use client";

import { useState } from "react";
import { CheckCircle2, AlertTriangle, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { Label } from "@/components/ui/label";
import { Table, TableHeader, TableBody, TableHead, TableRow, TableCell } from "@/components/ui/table";
import { useToast } from "@/components/ui/use-toast";
import {
    adminValidateDataTransferImport,
    adminCommitDataTransferImport,
    type DataTransferImportReport,
    type DataTransferImportCommitResult,
} from "@/lib/api";
import { BundleDropzone } from "@/components/data-transfer/BundleDropzone";

export function ImportTab() {
    const { toast } = useToast();
    const [file, setFile] = useState<File | null>(null);
    const [validating, setValidating] = useState(false);
    const [committing, setCommitting] = useState(false);
    const [report, setReport] = useState<DataTransferImportReport | DataTransferImportCommitResult | null>(null);
    const [updateExisting, setUpdateExisting] = useState(false);

    const onFileSelected = (f: File | null) => {
        setFile(f);
        setReport(null);
    };

    const onValidate = async () => {
        if (!file) return;
        setValidating(true);
        try {
            const result = await adminValidateDataTransferImport(file);
            setReport(result);
        } catch (e: any) {
            toast({ title: "Validation failed", description: e?.message ?? "Unknown error", variant: "destructive" });
        } finally {
            setValidating(false);
        }
    };

    const onCommit = async () => {
        if (!file) return;
        setCommitting(true);
        try {
            const result = await adminCommitDataTransferImport(file, undefined, updateExisting);
            setReport(result);
            if (result.committed) {
                const updateSummary =
                    updateExisting && ((result.updated_users ?? 0) > 0 || (result.updated_drivers ?? 0) > 0)
                        ? `, ${result.updated_users ?? 0} user(s) and ${result.updated_drivers ?? 0} driver(s) updated`
                        : "";
                toast({
                    title: "Import committed",
                    description: `${result.created_users ?? 0} user(s), ${result.created_drivers ?? 0} driver(s), ${result.documents_replayed ?? 0} document(s) created${updateSummary}.`,
                });
            } else {
                toast({
                    title: "Commit refused",
                    description: "The bundle no longer validates cleanly — see the errors below.",
                    variant: "destructive",
                });
            }
        } catch (e: any) {
            toast({ title: "Commit failed", description: e?.message ?? "Unknown error", variant: "destructive" });
        } finally {
            setCommitting(false);
        }
    };

    const committed = report && "committed" in report && report.committed;

    return (
        <div className="space-y-4">
            <BundleDropzone file={file} onFileSelected={onFileSelected} />

            <div className="flex items-center gap-2">
                <Switch
                    id="update-existing"
                    checked={updateExisting}
                    onCheckedChange={setUpdateExisting}
                    disabled={committing}
                />
                <Label htmlFor="update-existing" className="text-sm font-normal">
                    Update already-imported entities
                </Label>
                <span className="text-sm text-muted-foreground">
                    — sync profile fields and replay any new documents/insurance periods instead of skipping them
                </span>
            </div>

            <div className="flex gap-2">
                <Button onClick={onValidate} disabled={!file || validating || committing} variant="outline">
                    {validating && <Loader2 className="h-4 w-4 animate-spin" />}
                    Validate (dry run)
                </Button>
                <Button onClick={onCommit} disabled={!file || !report?.can_commit || committing || Boolean(committed)}>
                    {committing && <Loader2 className="h-4 w-4 animate-spin" />}
                    Commit import
                </Button>
            </div>

            {report && (
                <div className="space-y-3">
                    <div className="flex items-center gap-2 text-sm">
                        {report.can_commit ? (
                            <CheckCircle2 className="h-4 w-4 text-success" />
                        ) : (
                            <AlertTriangle className="h-4 w-4 text-warning" />
                        )}
                        <span>
                            {report.counts.entities} entities — {report.counts.new} new,{" "}
                            {report.counts.existing_match} already imported (
                            {updateExisting ? "will be updated" : "will be skipped"}), {report.counts.conflict}{" "}
                            conflicts
                        </span>
                    </div>

                    {report.errors.length > 0 && (
                        <Table>
                            <TableHeader>
                                <TableRow>
                                    <TableHead>Entity</TableHead>
                                    <TableHead>Field</TableHead>
                                    <TableHead>Error</TableHead>
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                {report.errors.map((err, i) => (
                                    <TableRow key={i}>
                                        <TableCell>{err.entity_id}</TableCell>
                                        <TableCell>{err.field}</TableCell>
                                        <TableCell>{err.message}</TableCell>
                                    </TableRow>
                                ))}
                            </TableBody>
                        </Table>
                    )}

                    {report.warnings.length > 0 && (
                        <div className="text-sm text-muted-foreground">
                            {report.warnings.map((w, i) => (
                                <div key={i}>
                                    {w.entity_id}: {w.message}
                                </div>
                            ))}
                        </div>
                    )}
                </div>
            )}
        </div>
    );
}
