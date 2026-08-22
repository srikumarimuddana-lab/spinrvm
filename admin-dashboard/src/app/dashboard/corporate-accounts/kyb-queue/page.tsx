"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import {
    CorporateAccount,
    fetchKybDocumentBlob,
    listCorporateAccounts,
    reviewKyb,
} from "@/lib/api";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from "@/components/ui/table";
import {
    AlertDialog,
    AlertDialogAction,
    AlertDialogCancel,
    AlertDialogContent,
    AlertDialogDescription,
    AlertDialogFooter,
    AlertDialogHeader,
    AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { useTableSort, SortableHead } from "@/components/ui/sortable-table";
import {
    ArrowLeft,
    Building2,
    CheckCircle2,
    FileText,
    RefreshCw,
    XCircle,
} from "lucide-react";
import { useToast } from "@/components/ui/use-toast";

export default function KybQueuePage() {
    const { toast } = useToast();
    const [rows, setRows] = useState<CorporateAccount[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [busyId, setBusyId] = useState<string | null>(null);

    const [rejectTarget, setRejectTarget] = useState<CorporateAccount | null>(null);
    const [rejectNote, setRejectNote] = useState("");

    const { sorted, sort, toggle } = useTableSort(rows);

    const load = async () => {
        setLoading(true);
        try {
            const data = await listCorporateAccounts({
                status: "pending_verification",
                limit: 100,
            });
            setRows(Array.isArray(data) ? data : []);
            setError(null);
        } catch (e: any) {
            setError(e?.message ?? "Failed to load KYB queue");
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        load();
    }, []);

    const approve = async (id: string) => {
        setBusyId(id);
        try {
            await reviewKyb(id, { approve: true });
            await load();
        } catch (e: any) {
            toast({ title: "Approval failed", description: e?.message, variant: "destructive" });
        } finally {
            setBusyId(null);
        }
    };

    const confirmReject = async () => {
        if (!rejectTarget) return;
        const id = rejectTarget.id;
        setBusyId(id);
        try {
            await reviewKyb(id, {
                approve: false,
                note: rejectNote.trim() || undefined,
            });
            setRejectTarget(null);
            setRejectNote("");
            await load();
        } catch (e: any) {
            toast({ title: "Rejection failed", description: e?.message, variant: "destructive" });
        } finally {
            setBusyId(null);
        }
    };

    // kyb_document_url is a raw PRIVATE-bucket key — stream it through the
    // backend and open the blob in a new tab (M2.7).
    const preview = async (c: CorporateAccount) => {
        setBusyId(c.id);
        try {
            const blob = await fetchKybDocumentBlob(c.id);
            const url = URL.createObjectURL(blob);
            window.open(url, "_blank", "noopener");
            setTimeout(() => URL.revokeObjectURL(url), 60_000);
        } catch (e: any) {
            toast({ title: "Could not load document", description: e?.message, variant: "destructive" });
        } finally {
            setBusyId(null);
        }
    };

    return (
        <div className="space-y-6">
            <div className="flex items-center justify-between">
                <div>
                    <div className="flex items-center gap-2">
                        <Link
                            href="/dashboard/corporate-accounts"
                            className="text-muted-foreground hover:text-foreground"
                            aria-label="Back to corporate accounts"
                        >
                            <ArrowLeft className="h-4 w-4" />
                        </Link>
                        <h1 className="text-3xl font-bold tracking-tight">
                            KYB Verification Queue
                        </h1>
                    </div>
                    <p className="text-muted-foreground mt-1">
                        Review pending corporate signups and approve or reject their KYB
                        documents.
                    </p>
                </div>
                <Button variant="outline" size="icon" onClick={load} aria-label="Refresh">
                    <RefreshCw className="h-4 w-4" />
                </Button>
            </div>

            {error && (
                <div className="rounded-md border border-destructive/40 bg-destructive/5 px-4 py-2 text-sm text-destructive">
                    {error}
                </div>
            )}

            <Card className="border-border/50">
                <CardContent className="p-0">
                    <Table>
                        <TableHeader>
                            <TableRow>
                                <SortableHead column="legal_name" sort={sort} onSort={toggle}>Company</SortableHead>
                                <SortableHead column="business_number" sort={sort} onSort={toggle}>Business Number</SortableHead>
                                <SortableHead column="tax_region" sort={sort} onSort={toggle}>Region</SortableHead>
                                <SortableHead column="size_tier" sort={sort} onSort={toggle}>Tier</SortableHead>
                                <SortableHead column="billing_email" sort={sort} onSort={toggle}>Billing Email</SortableHead>
                                <SortableHead column="kyb_submitted_at" sort={sort} onSort={toggle}>Submitted</SortableHead>
                                <SortableHead column="kyb_document_url" sort={sort} onSort={toggle}>Document</SortableHead>
                                <TableHead className="text-right">Actions</TableHead>
                            </TableRow>
                        </TableHeader>
                        <TableBody>
                            {loading ? (
                                <TableRow>
                                    <TableCell colSpan={8} className="py-10 text-center">
                                        <div className="flex justify-center">
                                            <div className="h-6 w-6 animate-spin rounded-full border-2 border-primary border-t-transparent" />
                                        </div>
                                    </TableCell>
                                </TableRow>
                            ) : rows.length === 0 ? (
                                <TableRow>
                                    <TableCell
                                        colSpan={8}
                                        className="py-10 text-center text-muted-foreground"
                                    >
                                        Queue is empty. No companies are waiting for KYB review.
                                    </TableCell>
                                </TableRow>
                            ) : (
                                sorted.map((c) => (
                                    <TableRow key={c.id}>
                                        <TableCell className="font-medium">
                                            <div className="flex items-center gap-2">
                                                <Building2 className="h-4 w-4 text-muted-foreground" />
                                                <span>{c.legal_name ?? c.name}</span>
                                            </div>
                                            {c.legal_name && c.legal_name !== c.name && (
                                                <div className="text-xs text-muted-foreground ml-6">
                                                    {c.name}
                                                </div>
                                            )}
                                        </TableCell>
                                        <TableCell className="font-mono text-xs">
                                            {c.business_number ?? "—"}
                                        </TableCell>
                                        <TableCell>{c.tax_region ?? "—"}</TableCell>
                                        <TableCell className="capitalize">
                                            {c.size_tier.replace("_", " ")}
                                        </TableCell>
                                        <TableCell className="text-sm text-muted-foreground">
                                            {c.billing_email ?? "—"}
                                        </TableCell>
                                        <TableCell className="text-xs text-muted-foreground">
                                            {c.kyb_submitted_at
                                                ? new Date(c.kyb_submitted_at).toLocaleDateString()
                                                : "—"}
                                        </TableCell>
                                        <TableCell>
                                            {c.kyb_document_url ? (
                                                <button
                                                    type="button"
                                                    className="inline-flex items-center gap-1 text-sm text-blue-600 dark:text-blue-400 hover:underline disabled:opacity-50"
                                                    onClick={() => preview(c)}
                                                    disabled={busyId === c.id}
                                                >
                                                    <FileText className="h-3 w-3" /> Preview
                                                </button>
                                            ) : (
                                                <span className="text-xs text-muted-foreground">
                                                    None
                                                </span>
                                            )}
                                        </TableCell>
                                        <TableCell className="text-right">
                                            <div className="flex justify-end gap-2">
                                                <Button
                                                    size="sm"
                                                    onClick={() => approve(c.id)}
                                                    disabled={busyId === c.id}
                                                    // eslint-disable-next-line no-restricted-syntax -- solid-fill white-text button; dark-mode --success (2.02:1) fails WCAG AA against white text (#2816)
                                                    className="bg-emerald-600 hover:bg-emerald-700"
                                                >
                                                    <CheckCircle2 className="mr-1 h-4 w-4" />
                                                    Approve
                                                </Button>
                                                <Button
                                                    size="sm"
                                                    variant="outline"
                                                    onClick={() => {
                                                        setRejectTarget(c);
                                                        setRejectNote("");
                                                    }}
                                                    disabled={busyId === c.id}
                                                    className="text-destructive hover:text-destructive/80"
                                                >
                                                    <XCircle className="mr-1 h-4 w-4" />
                                                    Reject
                                                </Button>
                                            </div>
                                        </TableCell>
                                    </TableRow>
                                ))
                            )}
                        </TableBody>
                    </Table>
                </CardContent>
            </Card>

            <AlertDialog
                open={rejectTarget !== null}
                onOpenChange={(open) => {
                    if (!open) {
                        setRejectTarget(null);
                        setRejectNote("");
                    }
                }}
            >
                <AlertDialogContent>
                    <AlertDialogHeader>
                        <AlertDialogTitle>
                            Reject KYB for {rejectTarget?.legal_name ?? rejectTarget?.name}?
                        </AlertDialogTitle>
                        <AlertDialogDescription>
                            The company will be moved to <b>suspended</b> so it can re-upload
                            and be re-reviewed. Provide an optional reason that will be stored
                            with the review.
                        </AlertDialogDescription>
                    </AlertDialogHeader>
                    <div className="space-y-2">
                        <Label htmlFor="reject-note">Reason (optional)</Label>
                        <Textarea
                            id="reject-note"
                            value={rejectNote}
                            onChange={(e) => setRejectNote(e.target.value)}
                            placeholder="e.g. document is unreadable"
                            maxLength={500}
                            rows={3}
                        />
                    </div>
                    <AlertDialogFooter>
                        <AlertDialogCancel>Cancel</AlertDialogCancel>
                        <AlertDialogAction
                            onClick={confirmReject}
                            className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
                        >
                            {busyId && busyId === rejectTarget?.id ? "Rejecting…" : "Reject"}
                        </AlertDialogAction>
                    </AlertDialogFooter>
                </AlertDialogContent>
            </AlertDialog>
        </div>
    );
}
