"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import {
    Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import {
    ArrowLeft, RefreshCw, RotateCcw, Copy, ExternalLink, CircleAlert,
    CircleCheck, Clock, Ban,
} from "lucide-react";
import Link from "next/link";
import { getPayoutById, retryPayout } from "@/lib/api";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface Payout {
    id: string;
    driver_id?: string;
    driver_name?: string;
    driver_email?: string;
    driver_phone?: string;
    amount: number | string;
    status: string;
    failure_reason?: string;
    bank_name?: string;
    account_last4?: string;
    stripe_transfer_id?: string;
    stripe_payout_id?: string;
    retry_requested_by?: string;
    retry_requested_at?: string;
    created_at?: string;
    updated_at?: string;
    processed_at?: string;
    ride_id?: string;
    period_start?: string;
    period_end?: string;
    notes?: string;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const STATUS_CONFIG: Record<string, { label: string; cls: string; Icon: React.ElementType }> = {
    completed: { label: "Completed", cls: "bg-green-500/15 text-green-700", Icon: CircleCheck },
    paid:      { label: "Paid",      cls: "bg-green-500/15 text-green-700", Icon: CircleCheck },
    pending:   { label: "Pending",   cls: "bg-yellow-500/15 text-yellow-700", Icon: Clock },
    processing:{ label: "Processing",cls: "bg-blue-500/15 text-blue-700", Icon: Clock },
    failed:    { label: "Failed",    cls: "bg-red-500/15 text-red-700", Icon: CircleAlert },
    cancelled: { label: "Cancelled", cls: "bg-zinc-400/20 text-zinc-600", Icon: Ban },
};

function formatCurrency(v: number | string | undefined) {
    const n = parseFloat(String(v ?? 0));
    return isNaN(n) ? "—" : `$${n.toFixed(2)}`;
}

function formatDate(iso?: string) {
    if (!iso) return "—";
    return new Date(iso).toLocaleString("en-CA", {
        year: "numeric", month: "short", day: "numeric",
        hour: "2-digit", minute: "2-digit",
    });
}

function Field({ label, value, mono }: { label: string; value: React.ReactNode; mono?: boolean }) {
    return (
        <div className="space-y-0.5">
            <p className="text-xs text-muted-foreground">{label}</p>
            <p className={`text-sm font-medium break-all ${mono ? "font-mono" : ""}`}>{value ?? "—"}</p>
        </div>
    );
}

function CopyableField({ label, value }: { label: string; value?: string }) {
    const [copied, setCopied] = useState(false);
    if (!value) return <Field label={label} value="—" />;
    const copy = () => {
        navigator.clipboard.writeText(value).then(() => {
            setCopied(true);
            setTimeout(() => setCopied(false), 1500);
        });
    };
    return (
        <div className="space-y-0.5">
            <p className="text-xs text-muted-foreground">{label}</p>
            <div className="flex items-center gap-1.5">
                <p className="text-sm font-medium font-mono break-all">{value}</p>
                <button
                    onClick={copy}
                    className="text-muted-foreground hover:text-foreground transition-colors"
                    title="Copy"
                >
                    {copied ? <CircleCheck className="h-3.5 w-3.5 text-green-600" /> : <Copy className="h-3.5 w-3.5" />}
                </button>
            </div>
        </div>
    );
}

// ---------------------------------------------------------------------------
// Retry Confirm Modal
// ---------------------------------------------------------------------------

function RetryModal({
    open, payout, onClose, onConfirm,
}: {
    open: boolean;
    payout: Payout | null;
    onClose: () => void;
    onConfirm: () => Promise<void>;
}) {
    const [busy, setBusy] = useState(false);
    const handle = async () => {
        setBusy(true);
        try { await onConfirm(); onClose(); }
        finally { setBusy(false); }
    };
    return (
        <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
            <DialogContent>
                <DialogHeader>
                    <DialogTitle>Retry Payout</DialogTitle>
                </DialogHeader>
                <div className="space-y-3 py-2 text-sm">
                    <p>Re-queue this payout for processing?</p>
                    <div className="rounded-md border p-3 space-y-1 bg-muted/30">
                        <p><span className="text-muted-foreground">Driver: </span>{payout?.driver_name ?? "Unknown"}</p>
                        <p><span className="text-muted-foreground">Amount: </span>{formatCurrency(payout?.amount)}</p>
                        {payout?.failure_reason && (
                            <p className="text-red-600 dark:text-red-400"><span className="text-muted-foreground">Failure: </span>{payout.failure_reason}</p>
                        )}
                    </div>
                    <p className="text-muted-foreground text-xs">
                        The payment-retry loop will pick this up within its next tick. Ensure the driver's bank details are correct before retrying.
                    </p>
                </div>
                <DialogFooter>
                    <Button variant="outline" onClick={onClose} disabled={busy}>Cancel</Button>
                    <Button onClick={handle} disabled={busy}>
                        {busy ? "Queuing…" : "Retry Payout"}
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}

// ---------------------------------------------------------------------------
// Main Page
// ---------------------------------------------------------------------------

export default function PayoutDetailPage() {
    const params = useParams<{ id: string }>();
    const id = params.id;

    const [payout, setPayout] = useState<Payout | null>(null);
    const [loading, setLoading] = useState(true);
    const [notFound, setNotFound] = useState(false);
    const [retryOpen, setRetryOpen] = useState(false);
    const [toast, setToast] = useState("");

    const load = useCallback(async () => {
        setLoading(true);
        try {
            const data = await getPayoutById(id);
            setPayout(data);
        } catch (err: any) {
            if (err?.status === 404 || String(err).includes("404")) setNotFound(true);
        } finally {
            setLoading(false);
        }
    }, [id]);

    useEffect(() => { load(); }, [load]);

    const handleRetry = async () => {
        await retryPayout(id);
        setToast("Payout queued for retry.");
        setTimeout(() => setToast(""), 4000);
        await load();
    };

    if (notFound) {
        return (
            <div className="p-8 text-center space-y-3">
                <p className="text-lg font-semibold">Payout not found</p>
                <Link href="/dashboard/earnings/payouts">
                    <Button variant="outline"><ArrowLeft className="h-4 w-4 mr-2" />Back to Payouts</Button>
                </Link>
            </div>
        );
    }

    const sc = payout ? (STATUS_CONFIG[payout.status] ?? { label: payout.status, cls: "bg-zinc-500/15 text-zinc-600", Icon: Clock }) : null;
    const canRetry = payout?.status === "failed" || payout?.status === "cancelled";

    return (
        <div className="space-y-6 p-6 max-w-4xl">
            {/* Header */}
            <div className="flex items-center justify-between flex-wrap gap-3">
                <div className="flex items-center gap-3">
                    <Link href="/dashboard/earnings/payouts" className="text-muted-foreground hover:text-foreground">
                        <ArrowLeft className="h-5 w-5" />
                    </Link>
                    <div>
                        <h1 className="text-2xl font-bold">Payout Detail</h1>
                        {payout && (
                            <p className="text-sm text-muted-foreground font-mono">{payout.id}</p>
                        )}
                    </div>
                </div>
                <div className="flex gap-2 items-center">
                    {toast && (
                        <span className="text-sm text-green-700 bg-green-100 px-3 py-1 rounded-full">{toast}</span>
                    )}
                    <Button variant="outline" onClick={load} disabled={loading}>
                        <RefreshCw className={`h-4 w-4 mr-2 ${loading ? "animate-spin" : ""}`} />
                        Refresh
                    </Button>
                    {canRetry && (
                        <Button onClick={() => setRetryOpen(true)}>
                            <RotateCcw className="h-4 w-4 mr-2" />
                            Retry Payout
                        </Button>
                    )}
                </div>
            </div>

            {loading && !payout ? (
                <div className="py-20 text-center text-muted-foreground">Loading…</div>
            ) : payout ? (
                <>
                    {/* Status + amount hero */}
                    <Card>
                        <CardContent className="pt-6 pb-5 flex items-center justify-between flex-wrap gap-4">
                            <div className="space-y-1">
                                <p className="text-xs text-muted-foreground">Amount</p>
                                <p className="text-3xl font-bold">{formatCurrency(payout.amount)}</p>
                            </div>
                            <div className="flex items-center gap-3">
                                {sc && (
                                    <Badge className={`${sc.cls} text-sm px-3 py-1 gap-1.5`}>
                                        <sc.Icon className="h-4 w-4" />
                                        {sc.label}
                                    </Badge>
                                )}
                            </div>
                        </CardContent>
                        {payout.failure_reason && (
                            <div className="px-6 pb-4">
                                <div className="rounded-md bg-red-50 dark:bg-red-900/10 border border-red-200 dark:border-red-800 p-3">
                                    <p className="text-xs font-semibold text-red-700 dark:text-red-300 mb-0.5">Failure Reason</p>
                                    <p className="text-sm text-red-700 dark:text-red-300">{payout.failure_reason}</p>
                                </div>
                            </div>
                        )}
                    </Card>

                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                        {/* Driver info */}
                        <Card>
                            <CardHeader className="pb-3">
                                <CardTitle className="text-sm">Driver</CardTitle>
                            </CardHeader>
                            <CardContent className="space-y-3">
                                <Field label="Name" value={payout.driver_name ?? "Unknown"} />
                                {payout.driver_id && (
                                    <div className="space-y-0.5">
                                        <p className="text-xs text-muted-foreground">Driver ID</p>
                                        <div className="flex items-center gap-2">
                                            <p className="text-sm font-mono">{payout.driver_id}</p>
                                            <Link
                                                href={`/dashboard/drivers/${payout.driver_id}`}
                                                className="text-muted-foreground hover:text-foreground"
                                                title="View driver"
                                            >
                                                <ExternalLink className="h-3.5 w-3.5" />
                                            </Link>
                                        </div>
                                    </div>
                                )}
                                {payout.driver_email && <Field label="Email" value={payout.driver_email} />}
                                {payout.driver_phone && <Field label="Phone" value={payout.driver_phone} />}
                            </CardContent>
                        </Card>

                        {/* Bank info */}
                        <Card>
                            <CardHeader className="pb-3">
                                <CardTitle className="text-sm">Bank Account</CardTitle>
                            </CardHeader>
                            <CardContent className="space-y-3">
                                <Field label="Bank" value={payout.bank_name} />
                                <Field
                                    label="Account"
                                    value={payout.account_last4 ? `•••• •••• ${payout.account_last4}` : undefined}
                                />
                            </CardContent>
                        </Card>

                        {/* Stripe */}
                        <Card>
                            <CardHeader className="pb-3">
                                <CardTitle className="text-sm">Stripe</CardTitle>
                            </CardHeader>
                            <CardContent className="space-y-3">
                                <CopyableField label="Transfer ID" value={payout.stripe_transfer_id} />
                                <CopyableField label="Payout ID" value={payout.stripe_payout_id} />
                            </CardContent>
                        </Card>

                        {/* Timestamps */}
                        <Card>
                            <CardHeader className="pb-3">
                                <CardTitle className="text-sm">Timeline</CardTitle>
                            </CardHeader>
                            <CardContent className="space-y-3">
                                <Field label="Requested" value={formatDate(payout.created_at)} />
                                <Field label="Processed" value={formatDate(payout.processed_at)} />
                                <Field label="Last Updated" value={formatDate(payout.updated_at)} />
                                {payout.period_start && (
                                    <Field
                                        label="Earnings Period"
                                        value={`${formatDate(payout.period_start)} → ${formatDate(payout.period_end)}`}
                                    />
                                )}
                            </CardContent>
                        </Card>
                    </div>

                    {/* Retry audit */}
                    {(payout.retry_requested_by || payout.retry_requested_at) && (
                        <>
                            <Separator />
                            <Card>
                                <CardHeader className="pb-3">
                                    <CardTitle className="text-sm">Retry Audit</CardTitle>
                                </CardHeader>
                                <CardContent className="grid grid-cols-2 gap-4">
                                    <Field label="Retry Requested By" value={payout.retry_requested_by} mono />
                                    <Field label="Retry Requested At" value={formatDate(payout.retry_requested_at)} />
                                </CardContent>
                            </Card>
                        </>
                    )}

                    {/* Related ride */}
                    {payout.ride_id && (
                        <>
                            <Separator />
                            <Card>
                                <CardHeader className="pb-3">
                                    <CardTitle className="text-sm">Related Ride</CardTitle>
                                </CardHeader>
                                <CardContent>
                                    <div className="flex items-center gap-2">
                                        <p className="text-sm font-mono">{payout.ride_id}</p>
                                        <Link
                                            href={`/dashboard/rides/${payout.ride_id}`}
                                            className="text-muted-foreground hover:text-foreground"
                                            title="View ride"
                                        >
                                            <ExternalLink className="h-3.5 w-3.5" />
                                        </Link>
                                    </div>
                                </CardContent>
                            </Card>
                        </>
                    )}

                    {/* Notes */}
                    {payout.notes && (
                        <>
                            <Separator />
                            <Card>
                                <CardHeader className="pb-3">
                                    <CardTitle className="text-sm">Notes</CardTitle>
                                </CardHeader>
                                <CardContent>
                                    <p className="text-sm text-muted-foreground whitespace-pre-wrap">{payout.notes}</p>
                                </CardContent>
                            </Card>
                        </>
                    )}
                </>
            ) : null}

            <RetryModal
                open={retryOpen}
                payout={payout}
                onClose={() => setRetryOpen(false)}
                onConfirm={handleRetry}
            />
        </div>
    );
}
