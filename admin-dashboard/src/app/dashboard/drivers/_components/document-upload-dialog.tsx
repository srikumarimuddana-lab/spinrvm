"use client";

import { useMemo, useRef, useState } from "react";
import { Loader2, Upload, X, FileText, CalendarCheck2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
    Dialog,
    DialogContent,
    DialogHeader,
    DialogTitle,
    DialogDescription,
    DialogFooter,
} from "@/components/ui/dialog";
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select";
import { useToast } from "@/components/ui/use-toast";
import { adminUploadDriverDocument } from "@/lib/api";

export interface UploadRequirement {
    key: string;
    label: string;
    has_expiry?: boolean;
    requires_back_side?: boolean;
}

/** Current on-file state for a document type, surfaced so the admin can see
 *  what already exists (and its expiry) before re-uploading. */
export interface ExistingDocInfo {
    expiry?: string;
    status: "approved" | "pending" | "rejected" | "missing";
}

interface DocumentUploadDialogProps {
    open: boolean;
    onClose: () => void;
    driverId: string | null;
    driverName?: string | null;
    requirements: UploadRequirement[];
    /** Keyed by requirement key — the date/status already recorded per type. */
    existing?: Record<string, ExistingDocInfo>;
    onUploaded?: () => void;
}

interface UploadRow {
    id: string;
    file: File;
    requirementKey: string;
    side: "front" | "back";
    expiryDate: string;
    status: "pending" | "approved";
}

let _rowSeq = 0;
const nextRowId = () => `row-${Date.now()}-${_rowSeq++}`;

function fmtDate(value?: string): string | null {
    if (!value) return null;
    const d = new Date(value);
    if (Number.isNaN(d.getTime())) return null;
    return d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

/** Normalize a stored expiry (date-only or full ISO) to the YYYY-MM-DD a native
 *  <input type="date"> expects, without a timezone shift on date-only values. */
function toDateInputValue(value?: string): string {
    if (!value) return "";
    const m = /^(\d{4}-\d{2}-\d{2})/.exec(value);
    if (m) return m[1];
    const d = new Date(value);
    if (Number.isNaN(d.getTime())) return "";
    return d.toISOString().slice(0, 10);
}

export function DocumentUploadDialog({
    open,
    onClose,
    driverId,
    driverName,
    requirements,
    existing,
    onUploaded,
}: DocumentUploadDialogProps) {
    const { toast } = useToast();
    const [rows, setRows] = useState<UploadRow[]>([]);
    const [submitting, setSubmitting] = useState(false);
    const fileInputRef = useRef<HTMLInputElement>(null);

    const reqByKey = useMemo(
        () => new Map(requirements.map((r) => [r.key, r])),
        [requirements],
    );

    // Document types that already have a date on file — the reference list the
    // admin sees at the top of the upload page.
    const onFile = useMemo(
        () =>
            requirements
                .map((r) => ({ req: r, info: existing?.[r.key] }))
                .filter((x): x is { req: UploadRequirement; info: ExistingDocInfo } =>
                    !!x.info && !!fmtDate(x.info.expiry),
                ),
        [requirements, existing],
    );

    const addFiles = (files: FileList | null) => {
        if (!files || files.length === 0) return;
        const added: UploadRow[] = Array.from(files).map((file) => ({
            id: nextRowId(),
            file,
            requirementKey: "",
            side: "front",
            expiryDate: "",
            status: "pending",
        }));
        setRows((prev) => [...prev, ...added]);
        // Allow re-selecting the same file after removing a row.
        if (fileInputRef.current) fileInputRef.current.value = "";
    };

    const updateRow = (id: string, patch: Partial<UploadRow>) =>
        setRows((prev) => prev.map((r) => (r.id === id ? { ...r, ...patch } : r)));

    // Selecting the document type seeds the expiry from what's already on file,
    // so the admin edits the recorded date instead of re-typing it into an empty
    // field (and immediately sees the current expiry, e.g. vehicle registration).
    const selectRequirement = (id: string, key: string) => {
        const req = reqByKey.get(key);
        updateRow(id, {
            requirementKey: key,
            expiryDate: req?.has_expiry ? toDateInputValue(existing?.[key]?.expiry) : "",
        });
    };

    const removeRow = (id: string) => setRows((prev) => prev.filter((r) => r.id !== id));

    const reset = () => setRows([]);

    const close = () => {
        if (submitting) return;
        reset();
        onClose();
    };

    const unmapped = rows.filter((r) => !r.requirementKey).length;
    const canSubmit = rows.length > 0 && unmapped === 0 && !submitting;

    const handleSubmit = async () => {
        if (!driverId || !canSubmit) return;
        setSubmitting(true);
        const failures: string[] = [];
        for (const row of rows) {
            const req = reqByKey.get(row.requirementKey);
            try {
                await adminUploadDriverDocument({
                    driverId,
                    requirementKey: row.requirementKey,
                    file: row.file,
                    side: req?.requires_back_side ? row.side : undefined,
                    expiryDate: req?.has_expiry && row.expiryDate ? row.expiryDate : undefined,
                    status: row.status,
                });
            } catch (e) {
                failures.push(`${row.file.name}: ${e instanceof Error ? e.message : "upload failed"}`);
            }
        }
        setSubmitting(false);

        const ok = rows.length - failures.length;
        if (failures.length === 0) {
            toast({
                title: "Documents uploaded",
                description: `${ok} document${ok === 1 ? "" : "s"} uploaded for ${driverName || "this driver"}.`,
            });
            reset();
            onUploaded?.();
            onClose();
        } else {
            toast({
                title: ok > 0 ? "Some documents failed" : "Upload failed",
                description: `${ok} succeeded, ${failures.length} failed. ${failures[0]}`,
                variant: "destructive",
            });
            // Keep only the failed rows so the admin can retry them.
            setRows((prev) => prev.filter((r) => failures.some((f) => f.startsWith(`${r.file.name}:`))));
            if (ok > 0) onUploaded?.();
        }
    };

    return (
        <Dialog open={open} onOpenChange={(o) => !o && close()}>
            <DialogContent className="sm:max-w-2xl">
                <DialogHeader>
                    <DialogTitle>Upload documents</DialogTitle>
                    <DialogDescription>
                        Add one or more files for {driverName || "this driver"} and map each to its
                        document type.
                    </DialogDescription>
                </DialogHeader>

                {onFile.length > 0 && (
                    <div className="rounded-md border bg-muted/40 p-3">
                        <p className="mb-2 flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
                            <CalendarCheck2 className="h-3.5 w-3.5" /> Already on file
                        </p>
                        <ul className="space-y-1">
                            {onFile.map(({ req, info }) => (
                                <li key={req.key} className="flex items-center justify-between text-xs">
                                    <span className="text-foreground">{req.label}</span>
                                    <span className="text-muted-foreground">
                                        expires {fmtDate(info.expiry)}
                                        {info.status !== "approved" ? ` · ${info.status}` : ""}
                                    </span>
                                </li>
                            ))}
                        </ul>
                    </div>
                )}

                <div className="space-y-1">
                    <label htmlFor="upload-files" className="text-sm font-medium">
                        Files (PDF or image)
                    </label>
                    <Input
                        id="upload-files"
                        ref={fileInputRef}
                        type="file"
                        multiple
                        accept="image/jpeg,image/png,image/gif,image/webp,application/pdf"
                        onChange={(e) => addFiles(e.target.files)}
                    />
                </div>

                {rows.length > 0 && (
                    <div className="max-h-[50vh] space-y-3 overflow-y-auto pr-1">
                        {rows.map((row) => {
                            const req = reqByKey.get(row.requirementKey);
                            const existingForType = row.requirementKey ? existing?.[row.requirementKey] : undefined;
                            const onFileDate = fmtDate(existingForType?.expiry);
                            return (
                                <div key={row.id} className="space-y-3 rounded-md border p-3">
                                    <div className="flex items-center justify-between gap-2">
                                        <span className="flex min-w-0 items-center gap-1.5 text-sm">
                                            <FileText className="h-4 w-4 shrink-0 text-muted-foreground" />
                                            <span className="truncate">{row.file.name}</span>
                                        </span>
                                        <button
                                            type="button"
                                            onClick={() => removeRow(row.id)}
                                            disabled={submitting}
                                            className="text-muted-foreground hover:text-foreground"
                                            aria-label={`Remove ${row.file.name}`}
                                        >
                                            <X className="h-4 w-4" />
                                        </button>
                                    </div>

                                    <div className="grid gap-3 sm:grid-cols-2">
                                        <div className="space-y-1">
                                            <span className="block text-xs font-medium">Document type</span>
                                            <Select
                                                value={row.requirementKey}
                                                onValueChange={(v) => selectRequirement(row.id, v)}
                                            >
                                                <SelectTrigger aria-label={`Document type for ${row.file.name}`}>
                                                    <SelectValue placeholder="Select a document type" />
                                                </SelectTrigger>
                                                <SelectContent>
                                                    {requirements.length === 0 ? (
                                                        <SelectItem value="__none__" disabled>
                                                            No document types configured
                                                        </SelectItem>
                                                    ) : (
                                                        requirements.map((r) => (
                                                            <SelectItem key={r.key} value={r.key}>
                                                                {r.label}
                                                            </SelectItem>
                                                        ))
                                                    )}
                                                </SelectContent>
                                            </Select>
                                            {onFileDate && (
                                                <p className="text-xs text-muted-foreground">
                                                    Currently on file: expires {onFileDate}
                                                </p>
                                            )}
                                        </div>

                                        <div className="space-y-1">
                                            <span className="block text-xs font-medium">Status</span>
                                            <Select
                                                value={row.status}
                                                onValueChange={(v) => updateRow(row.id, { status: v as "pending" | "approved" })}
                                            >
                                                <SelectTrigger aria-label={`Status for ${row.file.name}`}>
                                                    <SelectValue />
                                                </SelectTrigger>
                                                <SelectContent>
                                                    <SelectItem value="pending">Pending (needs review)</SelectItem>
                                                    <SelectItem value="approved">Approved (verified now)</SelectItem>
                                                </SelectContent>
                                            </Select>
                                        </div>

                                        {req?.has_expiry && (
                                            <div className="space-y-1">
                                                <span className="block text-xs font-medium">Expiry date</span>
                                                <Input
                                                    type="date"
                                                    value={row.expiryDate}
                                                    onChange={(e) => updateRow(row.id, { expiryDate: e.target.value })}
                                                    aria-label={`Expiry date for ${row.file.name}`}
                                                />
                                            </div>
                                        )}

                                        {req?.requires_back_side && (
                                            <div className="space-y-1">
                                                <span className="block text-xs font-medium">Side</span>
                                                <Select
                                                    value={row.side}
                                                    onValueChange={(v) => updateRow(row.id, { side: v as "front" | "back" })}
                                                >
                                                    <SelectTrigger aria-label={`Side for ${row.file.name}`}>
                                                        <SelectValue />
                                                    </SelectTrigger>
                                                    <SelectContent>
                                                        <SelectItem value="front">Front</SelectItem>
                                                        <SelectItem value="back">Back</SelectItem>
                                                    </SelectContent>
                                                </Select>
                                            </div>
                                        )}
                                    </div>
                                </div>
                            );
                        })}
                    </div>
                )}

                {rows.length > 0 && unmapped > 0 && (
                    <p className="text-xs text-warning">
                        Map every file to a document type before uploading ({unmapped} remaining).
                    </p>
                )}

                <DialogFooter>
                    <Button variant="outline" onClick={close} disabled={submitting}>
                        Cancel
                    </Button>
                    <Button onClick={handleSubmit} disabled={!canSubmit}>
                        {submitting ? (
                            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                        ) : (
                            <Upload className="mr-2 h-4 w-4" />
                        )}
                        Upload{rows.length > 0 ? ` ${rows.length}` : ""}
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}
