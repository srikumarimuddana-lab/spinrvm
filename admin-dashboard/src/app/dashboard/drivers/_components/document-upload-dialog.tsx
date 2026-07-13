"use client";

import { useMemo, useState } from "react";
import { Loader2, Upload } from "lucide-react";
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

interface DocumentUploadDialogProps {
    open: boolean;
    onClose: () => void;
    driverId: string | null;
    driverName?: string | null;
    requirements: UploadRequirement[];
    onUploaded?: () => void;
}

export function DocumentUploadDialog({
    open,
    onClose,
    driverId,
    driverName,
    requirements,
    onUploaded,
}: DocumentUploadDialogProps) {
    const { toast } = useToast();
    const [requirementKey, setRequirementKey] = useState<string>("");
    const [side, setSide] = useState<"front" | "back">("front");
    const [expiryDate, setExpiryDate] = useState<string>("");
    const [status, setStatus] = useState<"pending" | "approved">("pending");
    const [file, setFile] = useState<File | null>(null);
    const [submitting, setSubmitting] = useState(false);

    const selectedReq = useMemo(
        () => requirements.find((r) => r.key === requirementKey),
        [requirements, requirementKey],
    );

    const reset = () => {
        setRequirementKey("");
        setSide("front");
        setExpiryDate("");
        setStatus("pending");
        setFile(null);
    };

    const close = () => {
        if (submitting) return;
        reset();
        onClose();
    };

    const handleSubmit = async () => {
        if (!driverId || !requirementKey || !file) return;
        setSubmitting(true);
        try {
            await adminUploadDriverDocument({
                driverId,
                requirementKey,
                file,
                side: selectedReq?.requires_back_side ? side : undefined,
                expiryDate: selectedReq?.has_expiry && expiryDate ? expiryDate : undefined,
                status,
            });
            toast({
                title: "Document uploaded",
                description: `${selectedReq?.label || requirementKey} uploaded as ${status}.`,
            });
            reset();
            onUploaded?.();
            onClose();
        } catch (e) {
            toast({
                title: "Upload failed",
                description: e instanceof Error ? e.message : "Could not upload the document",
                variant: "destructive",
            });
        } finally {
            setSubmitting(false);
        }
    };

    return (
        <Dialog open={open} onOpenChange={(o) => !o && close()}>
            <DialogContent className="sm:max-w-md">
                <DialogHeader>
                    <DialogTitle>Upload document</DialogTitle>
                    <DialogDescription>
                        Attach a document on behalf of {driverName || "this driver"}.
                    </DialogDescription>
                </DialogHeader>

                <div className="space-y-4">
                    <div className="space-y-1">
                        <label htmlFor="upload-req" className="text-sm font-medium">
                            Document type
                        </label>
                        <Select value={requirementKey} onValueChange={setRequirementKey}>
                            <SelectTrigger id="upload-req">
                                <SelectValue placeholder="Select a document type" />
                            </SelectTrigger>
                            <SelectContent>
                                {requirements.length === 0 ? (
                                    <SelectItem value="__none__" disabled>
                                        No document types configured for this service area
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
                    </div>

                    {selectedReq?.requires_back_side && (
                        <div className="space-y-1">
                            <label htmlFor="upload-side" className="text-sm font-medium">
                                Side
                            </label>
                            <Select value={side} onValueChange={(v) => setSide(v as "front" | "back")}>
                                <SelectTrigger id="upload-side">
                                    <SelectValue />
                                </SelectTrigger>
                                <SelectContent>
                                    <SelectItem value="front">Front</SelectItem>
                                    <SelectItem value="back">Back</SelectItem>
                                </SelectContent>
                            </Select>
                        </div>
                    )}

                    {selectedReq?.has_expiry && (
                        <div className="space-y-1">
                            <label htmlFor="upload-expiry" className="text-sm font-medium">
                                Expiry date
                            </label>
                            <Input
                                id="upload-expiry"
                                type="date"
                                value={expiryDate}
                                onChange={(e) => setExpiryDate(e.target.value)}
                            />
                        </div>
                    )}

                    <div className="space-y-1">
                        <label htmlFor="upload-file" className="text-sm font-medium">
                            File (PDF or image)
                        </label>
                        <Input
                            id="upload-file"
                            type="file"
                            accept="image/jpeg,image/png,image/gif,image/webp,application/pdf"
                            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
                        />
                    </div>

                    <div className="space-y-1">
                        <label htmlFor="upload-status" className="text-sm font-medium">
                            Status
                        </label>
                        <Select value={status} onValueChange={(v) => setStatus(v as "pending" | "approved")}>
                            <SelectTrigger id="upload-status">
                                <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                                <SelectItem value="pending">Pending (needs review)</SelectItem>
                                <SelectItem value="approved">Approved (verified now)</SelectItem>
                            </SelectContent>
                        </Select>
                        {status === "approved" && (
                            <p className="text-xs text-muted-foreground">
                                Marks the document approved and updates the driver&apos;s expiry on file.
                            </p>
                        )}
                    </div>
                </div>

                <DialogFooter>
                    <Button variant="outline" onClick={close} disabled={submitting}>
                        Cancel
                    </Button>
                    <Button onClick={handleSubmit} disabled={!requirementKey || !file || submitting}>
                        {submitting ? (
                            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                        ) : (
                            <Upload className="mr-2 h-4 w-4" />
                        )}
                        Upload
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}
