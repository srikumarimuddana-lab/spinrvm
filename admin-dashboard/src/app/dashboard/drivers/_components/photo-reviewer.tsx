"use client";

import { useCallback, useState } from "react";
import { X, Check, XCircle, Loader2, AlertCircle, Camera } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { useToast } from "@/components/ui/use-toast";
import { reviewDriverPhoto } from "@/lib/api";

interface PhotoReviewerProps {
    open: boolean;
    driverId: string | null;
    driverName?: string | null;
    photoUrl?: string | null;
    onClose: () => void;
    onAfterAction?: () => void;
}

export function PhotoReviewer({
    open,
    driverId,
    driverName,
    photoUrl,
    onClose,
    onAfterAction,
}: PhotoReviewerProps) {
    const { toast } = useToast();
    const [mode, setMode] = useState<"idle" | "reject">("idle");
    const [reason, setReason] = useState("");
    const [busy, setBusy] = useState(false);

    const reset = useCallback(() => {
        setMode("idle");
        setReason("");
    }, []);

    const submit = useCallback(
        async (action: "approved" | "rejected") => {
            if (!driverId) return;
            if (action === "rejected" && !reason.trim()) {
                toast({ title: "Reason required", description: "Provide a rejection reason so the driver knows what to fix.", variant: "destructive" });
                return;
            }
            setBusy(true);
            try {
                await reviewDriverPhoto(driverId, action, action === "rejected" ? reason.trim() : undefined);
                toast({
                    title: action === "approved" ? "Photo approved" : "Photo rejected",
                    description: action === "approved"
                        ? "Driver was notified their photo is live."
                        : "Driver was notified to re-upload.",
                });
                reset();
                onAfterAction?.();
                onClose();
            } catch (e) {
                toast({ title: "Action failed", description: String((e as Error).message || e), variant: "destructive" });
            } finally {
                setBusy(false);
            }
        },
        [driverId, reason, toast, reset, onAfterAction, onClose],
    );

    if (!open) return null;

    return (
        <div
            className="fixed inset-0 z-[110] bg-background/80 backdrop-blur-sm flex items-center justify-center p-4"
            role="dialog"
            aria-modal="true"
            aria-label="Profile photo reviewer"
        >
            <div className="bg-card border border-border rounded-2xl shadow-2xl w-full max-w-md flex flex-col overflow-hidden">
                {/* Header */}
                <div className="flex items-center justify-between gap-3 px-5 py-4 border-b border-border">
                    <div className="flex items-center gap-2">
                        <Camera className="h-4 w-4 text-muted-foreground" />
                        <div>
                            <p className="text-sm font-semibold">{driverName || "Profile Photo Review"}</p>
                            <p className="text-xs text-muted-foreground">Photo pending admin review</p>
                        </div>
                    </div>
                    <Button variant="ghost" size="sm" onClick={onClose} disabled={busy}>
                        <X className="h-4 w-4" />
                    </Button>
                </div>

                {/* Photo */}
                <div className="bg-muted/30 flex items-center justify-center p-6 min-h-[240px]">
                    {photoUrl ? (
                        // eslint-disable-next-line @next/next/no-img-element
                        <img
                            src={photoUrl}
                            alt="Driver profile photo"
                            className="max-h-56 max-w-full rounded-xl object-contain shadow-md"
                        />
                    ) : (
                        <div className="text-center text-muted-foreground">
                            <AlertCircle className="h-10 w-10 mx-auto opacity-30 mb-2" />
                            <p className="text-sm">No photo available</p>
                        </div>
                    )}
                </div>

                {/* Actions */}
                <div className="px-5 py-4 space-y-3">
                    <div className="flex gap-2">
                        <Button
                            onClick={() => { setMode("idle"); submit("approved"); }}
                            className="flex-1 bg-emerald-600 hover:bg-emerald-700 text-white"
                            disabled={busy || mode === "reject"}
                        >
                            {busy && mode !== "reject" ? (
                                <Loader2 className="h-4 w-4 animate-spin mr-1.5" />
                            ) : (
                                <Check className="h-4 w-4 mr-1.5" />
                            )}
                            Approve
                        </Button>
                        <Button
                            onClick={() => setMode(mode === "reject" ? "idle" : "reject")}
                            variant={mode === "reject" ? "default" : "outline"}
                            className={mode === "reject" ? "flex-1 bg-red-600 hover:bg-red-700 text-white" : "flex-1"}
                            disabled={busy}
                        >
                            <XCircle className="h-4 w-4 mr-1.5" />
                            Reject
                        </Button>
                    </div>

                    {mode === "reject" && (
                        <div className="space-y-2 rounded-lg border border-red-200 dark:border-red-800 bg-red-50/40 dark:bg-red-900/10 p-3">
                            <label htmlFor="photo-reject-reason" className="text-xs font-medium text-red-700 dark:text-red-300">
                                Rejection reason <span className="text-red-500">*</span>
                            </label>
                            <Textarea
                                id="photo-reject-reason"
                                value={reason}
                                onChange={(e) => setReason(e.target.value)}
                                placeholder="e.g. Not a clear face photo, sunglasses visible, image too dark…"
                                className="text-sm min-h-[72px]"
                                autoFocus
                            />
                            <Button
                                onClick={() => submit("rejected")}
                                disabled={busy || !reason.trim()}
                                className="w-full bg-red-600 hover:bg-red-700 text-white"
                            >
                                {busy ? (
                                    <Loader2 className="h-4 w-4 animate-spin mr-1.5" />
                                ) : (
                                    <XCircle className="h-4 w-4 mr-1.5" />
                                )}
                                Confirm rejection
                            </Button>
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}
