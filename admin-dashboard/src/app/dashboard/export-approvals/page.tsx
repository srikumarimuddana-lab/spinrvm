"use client";

/**
 * Export Approvals — ACTION_ITEMS.md B10 dual-approval gate queue.
 *
 * Strictly role === "super_admin" (same pattern as Bulk Operations /
 * AI Console): the sidebar entry uses superAdminOnly, this page renders a
 * denial card for anyone else, and the backend independently re-checks the
 * role via require_super_admin on every export-approvals endpoint (403).
 *
 * A pending request here means a different admin ran a large export
 * (Compliance GST/PST or insurance-period audit, or a Data Transfer
 * export) while settings.dual_approval_exports_enabled was on, and it's
 * waiting for someone other than the requester to approve or deny it
 * before the file is generated. The backend enforces the self-approval
 * block independently (403 if you try), so this page doesn't need to.
 */

import { useCallback, useEffect, useState } from "react";
import { ShieldAlert, Check, X, Loader2, Inbox } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { useToast } from "@/components/ui/use-toast";
import { useAuthStore } from "@/store/authStore";
import { approveExportRequest, denyExportRequest, getPendingExportApprovals, type ExportApprovalRequest } from "@/lib/api";

const ROUTE_LABELS: Record<string, string> = {
    "compliance.gst_pst_remittance": "Compliance — GST/PST Remittance",
    "compliance.insurance_billing_sgi": "Compliance — SGI Insurance Billing",
    "compliance.insurance_billing_knight_archer": "Compliance — Knight Archer Insurance Billing",
    "compliance.airport_trips": "Compliance — Airport Trips",
    "data_transfer.export": "Data Transfer — Export",
};

export default function ExportApprovalsPage() {
    const user = useAuthStore((s) => s.user);
    const { toast } = useToast();
    const [requests, setRequests] = useState<ExportApprovalRequest[]>([]);
    const [loading, setLoading] = useState(true);
    const [notes, setNotes] = useState<Record<string, string>>({});
    const [busyId, setBusyId] = useState<string | null>(null);

    const load = useCallback(() => {
        setLoading(true);
        getPendingExportApprovals()
            .then((rows) => setRequests(Array.isArray(rows) ? rows : []))
            .catch((e) => toast({ title: "Could not load queue", description: String(e?.message || e), variant: "destructive" }))
            .finally(() => setLoading(false));
    }, [toast]);

    useEffect(() => { load(); }, [load]);

    const isSuperAdmin = user?.role === "super_admin";
    if (!isSuperAdmin) {
        return (
            <div className="mx-auto flex max-w-md flex-col items-center gap-3 p-12 text-center">
                <ShieldAlert className="h-10 w-10 text-muted-foreground" />
                <h2 className="text-lg font-semibold">Super admin only</h2>
                <p className="text-sm text-muted-foreground">
                    Approving or denying a bulk PII export requires the super admin role.
                </p>
            </div>
        );
    }

    const decide = async (request: ExportApprovalRequest, action: "approve" | "deny") => {
        setBusyId(request.id);
        try {
            const fn = action === "approve" ? approveExportRequest : denyExportRequest;
            await fn(request.id, notes[request.id] || "");
            toast({ title: action === "approve" ? "Approved" : "Denied", description: ROUTE_LABELS[request.route_key] || request.route_key });
            setRequests((prev) => prev.filter((r) => r.id !== request.id));
        } catch (e: any) {
            // The backend's error messages here (RequestNotFound /
            // RequestAlreadyDecided / SelfApprovalError) are already
            // written as clear, direct sentences meant to be shown as-is.
            toast({ title: "Action failed", description: String(e?.message || e), variant: "destructive" });
        } finally {
            setBusyId(null);
        }
    };

    return (
        <div className="p-6 space-y-6">
            <div>
                <h1 className="text-2xl font-semibold">Export Approvals</h1>
                <p className="text-muted-foreground">
                    Large PII exports (Compliance reports, Data Transfer bundles) generated while the
                    dual-approval gate is on wait here for a different admin to approve or deny before the
                    file is produced.
                </p>
            </div>

            <Card>
                <CardHeader>
                    <CardTitle>Pending ({requests.length})</CardTitle>
                    <CardDescription>You cannot approve or deny your own export request.</CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                    {loading ? (
                        <div className="flex items-center gap-2 text-muted-foreground py-8 justify-center">
                            <Loader2 className="h-4 w-4 animate-spin" /> Loading…
                        </div>
                    ) : requests.length === 0 ? (
                        <div className="flex flex-col items-center gap-2 text-muted-foreground py-8">
                            <Inbox className="h-8 w-8" />
                            <p>No pending export requests.</p>
                        </div>
                    ) : (
                        requests.map((r) => (
                            <div key={r.id} className="rounded-lg border p-4 space-y-3">
                                <div className="flex items-start justify-between gap-4">
                                    <div>
                                        <div className="font-medium">{ROUTE_LABELS[r.route_key] || r.route_key}</div>
                                        <div className="text-xs text-muted-foreground">
                                            Requested by {r.requested_by} · {new Date(r.created_at).toLocaleString()}
                                        </div>
                                    </div>
                                    {r.row_count != null && <Badge variant="secondary">{r.row_count.toLocaleString()} rows</Badge>}
                                </div>
                                {r.reason && <p className="text-sm">Reason: {r.reason}</p>}
                                <pre className="text-xs bg-muted rounded p-2 overflow-x-auto">{JSON.stringify(r.params, null, 2)}</pre>
                                <Textarea
                                    placeholder="Optional note (visible in the audit log)"
                                    value={notes[r.id] || ""}
                                    onChange={(e) => setNotes((prev) => ({ ...prev, [r.id]: e.target.value }))}
                                    className="h-16"
                                />
                                <div className="flex gap-2">
                                    <Button size="sm" onClick={() => decide(r, "approve")} disabled={busyId === r.id}>
                                        {busyId === r.id ? <Loader2 className="h-3.5 w-3.5 animate-spin mr-1.5" /> : <Check className="h-3.5 w-3.5 mr-1.5" />}
                                        Approve
                                    </Button>
                                    <Button size="sm" variant="outline" onClick={() => decide(r, "deny")} disabled={busyId === r.id}>
                                        <X className="h-3.5 w-3.5 mr-1.5" /> Deny
                                    </Button>
                                </div>
                            </div>
                        ))
                    )}
                </CardContent>
            </Card>
        </div>
    );
}
