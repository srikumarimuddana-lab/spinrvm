"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import {
    CorporateAccount,
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
import {
    ArrowLeft,
    Building2,
    CheckCircle2,
    FileText,
    RefreshCw,
    XCircle,
} from "lucide-react";

export default function KybQueuePage() {
    const [rows, setRows] = useState<CorporateAccount[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [busyId, setBusyId] = useState<string | null>(null);

    const [rejectTarget, setRejectTarget] = useState<CorporateAccount | null>(null);
    const [rejectNote, setRejectNote] = useState("");

    const load = async () => {
        setLoading(true);
        try {
            const data = await listCorporateAccounts({
                status: "pending_verification",
                limit: 100,
            });
            setRows(data);
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
            alert(e?.message ?? "Approval failed");
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
            alert(e?.message ?? "Rejection failed");
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
                <div className="rounded-md border border-red-200 bg-red-50 px-4 py-2 text-sm text-red-700">
                    {error}
                </div>
            )}

            <Card className="border-border/50">
                <CardContent className="p-0">
                    <Table>
                        <TableHeader>
                            <TableRow>
                                <TableHead>Company</TableHead>
                                <TableHead>Business Number</TableHead>
                                <TableHead>Region</TableHead>
                                <TableHead>Tier</TableHead>
                                <TableHead>Billing Email</TableHead>
                                <TableHead>Document</TableHead>
                                <TableHead className="text-right">Actions</TableHead>
                            </TableRow>
                        </TableHeader>
                        <TableBody>
                            {loading ? (
                                <TableRow>
                                    <TableCell colSpan={7} className="py-10 text-center">
                                        <div className="flex justify-center">
                                            <div className="h-6 w-6 animate-spin rounded-full border-2 border-primary border-t-transparent" />
                                        </div>
                                    </TableCell>
                                </TableRow>
                            ) : rows.length === 0 ? (
                                <TableRow>
                                    <TableCell
                                        colSpan={7}
                                        className="py-10 text-center text-muted-foreground"
                                    >
                                        Queue is empty. No companies are waiting for KYB review.
                                    </TableCell>
                                </TableRow>
                            ) : (
                                rows.map((c) => (
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
                                        <TableCell>
                                            {c.kyb_document_url ? (
                                                <a
                                                    className="inline-flex items-center gap-1 text-sm text-blue-600 hover:underline"
                                                    href={c.kyb_document_url}
                                                    target="_blank"
                                                    rel="noreferrer"
                                                >
                                                    <FileText className="h-3 w-3" /> View
                                                </a>
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
                                                    className="text-red-600 hover:text-red-700"
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
                            className="bg-red-600 hover:bg-red-700"
                        >
                            {busyId && busyId === rejectTarget?.id ? "Rejecting…" : "Reject"}
                        </AlertDialogAction>
                    </AlertDialogFooter>
                </AlertDialogContent>
            </AlertDialog>
        </div>
    );
}
