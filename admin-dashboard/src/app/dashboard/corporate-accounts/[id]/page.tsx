"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import {
    CorporateAccount,
    CompanyStatus,
    changeCompanyStatus,
    getCorporateAccount,
} from "@/lib/api";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
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
import {
    ArrowLeft,
    Building2,
    CheckCircle2,
    FileText,
    PauseCircle,
    PlayCircle,
    XCircle,
} from "lucide-react";

const STATUS_PILL_CLASSES: Record<CompanyStatus, string> = {
    pending_verification: "bg-yellow-100 text-yellow-800 hover:bg-yellow-100",
    active: "bg-emerald-100 text-emerald-800 hover:bg-emerald-100",
    suspended: "bg-orange-100 text-orange-800 hover:bg-orange-100",
    closed: "bg-gray-200 text-gray-700 hover:bg-gray-200",
};

type TransitionKind = "suspend" | "reactivate" | "close";

interface TransitionConfig {
    title: string;
    description: string;
    targetStatus: CompanyStatus;
    confirmLabel: string;
    captureReason: boolean;
    confirmClass: string;
}

const TRANSITIONS: Record<TransitionKind, TransitionConfig> = {
    suspend: {
        title: "Suspend this company?",
        description:
            "Wallet activity and new ride authorisations will be blocked until the company is reactivated.",
        targetStatus: "suspended",
        confirmLabel: "Suspend",
        captureReason: true,
        confirmClass: "bg-orange-600 hover:bg-orange-700",
    },
    reactivate: {
        title: "Reactivate this company?",
        description: "The company will return to active status and wallet activity resumes.",
        targetStatus: "active",
        confirmLabel: "Reactivate",
        captureReason: false,
        confirmClass: "bg-emerald-600 hover:bg-emerald-700",
    },
    close: {
        title: "Close this company permanently?",
        description:
            "A closed account cannot be reopened. All members lose access and billing stops.",
        targetStatus: "closed",
        confirmLabel: "Close",
        captureReason: true,
        confirmClass: "bg-red-600 hover:bg-red-700",
    },
};

function formatDate(iso?: string | null) {
    if (!iso) return "—";
    try {
        return new Date(iso).toLocaleString();
    } catch {
        return iso;
    }
}

export default function CompanyDetailPage() {
    const params = useParams<{ id: string }>();
    const id = params?.id;

    const [company, setCompany] = useState<CorporateAccount | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [pendingTransition, setPendingTransition] = useState<TransitionKind | null>(null);
    const [reason, setReason] = useState("");
    const [transitioning, setTransitioning] = useState(false);

    const load = useCallback(async () => {
        if (!id) return;
        setLoading(true);
        try {
            setCompany(await getCorporateAccount(id));
            setError(null);
        } catch (e: any) {
            setError(e?.message ?? "Failed to load company");
        } finally {
            setLoading(false);
        }
    }, [id]);

    useEffect(() => {
        load();
    }, [load]);

    const openTransition = (kind: TransitionKind) => {
        setPendingTransition(kind);
        setReason("");
    };

    const confirmTransition = async () => {
        if (!company || !pendingTransition) return;
        const cfg = TRANSITIONS[pendingTransition];
        setTransitioning(true);
        try {
            const updated = await changeCompanyStatus(company.id, {
                status: cfg.targetStatus,
                reason: cfg.captureReason && reason.trim() ? reason.trim() : undefined,
            });
            setCompany(updated);
            setPendingTransition(null);
            setReason("");
        } catch (e: any) {
            alert(e?.message ?? "Status change failed");
        } finally {
            setTransitioning(false);
        }
    };

    if (loading) {
        return (
            <div className="flex justify-center py-20">
                <div className="h-6 w-6 animate-spin rounded-full border-2 border-primary border-t-transparent" />
            </div>
        );
    }

    if (error) {
        return (
            <div className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
                {error}
            </div>
        );
    }

    if (!company) return null;

    const transitionCfg = pendingTransition ? TRANSITIONS[pendingTransition] : null;

    return (
        <div className="space-y-6">
            <div className="flex items-start justify-between gap-4">
                <div className="space-y-1">
                    <Link
                        href="/dashboard/corporate-accounts"
                        className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
                    >
                        <ArrowLeft className="h-3 w-3" /> All corporate accounts
                    </Link>
                    <h1 className="text-3xl font-bold tracking-tight flex items-center gap-3">
                        <Building2 className="h-7 w-7 text-muted-foreground" />
                        {company.legal_name ?? company.name}
                        <Badge
                            variant="secondary"
                            className={STATUS_PILL_CLASSES[company.status]}
                        >
                            {company.status.replace("_", " ")}
                        </Badge>
                    </h1>
                    {company.legal_name && company.legal_name !== company.name && (
                        <p className="text-sm text-muted-foreground">
                            Trading as <b>{company.name}</b>
                        </p>
                    )}
                </div>

                <div className="flex flex-wrap gap-2">
                    {company.status !== "suspended" && company.status !== "closed" && (
                        <Button
                            variant="outline"
                            onClick={() => openTransition("suspend")}
                            className="text-orange-700"
                        >
                            <PauseCircle className="mr-2 h-4 w-4" /> Suspend
                        </Button>
                    )}
                    {company.status === "suspended" && (
                        <Button
                            onClick={() => openTransition("reactivate")}
                            className="bg-emerald-600 hover:bg-emerald-700"
                        >
                            <PlayCircle className="mr-2 h-4 w-4" /> Reactivate
                        </Button>
                    )}
                    {company.status !== "closed" && (
                        <Button
                            variant="outline"
                            onClick={() => openTransition("close")}
                            className="text-red-700"
                        >
                            <XCircle className="mr-2 h-4 w-4" /> Close
                        </Button>
                    )}
                </div>
            </div>

            <Card>
                <CardContent className="grid grid-cols-1 gap-6 p-6 md:grid-cols-2">
                    <div className="space-y-1">
                        <Label className="text-xs uppercase text-muted-foreground">
                            Business Number
                        </Label>
                        <p className="font-mono text-sm">{company.business_number ?? "—"}</p>
                    </div>
                    <div className="space-y-1">
                        <Label className="text-xs uppercase text-muted-foreground">
                            Tax Region
                        </Label>
                        <p className="text-sm">{company.tax_region ?? "—"}</p>
                    </div>
                    <div className="space-y-1">
                        <Label className="text-xs uppercase text-muted-foreground">
                            Size Tier
                        </Label>
                        <p className="text-sm capitalize">
                            {company.size_tier.replace("_", " ")}
                        </p>
                    </div>
                    <div className="space-y-1">
                        <Label className="text-xs uppercase text-muted-foreground">
                            Billing Email
                        </Label>
                        <p className="text-sm">{company.billing_email ?? "—"}</p>
                    </div>
                    <div className="space-y-1">
                        <Label className="text-xs uppercase text-muted-foreground">
                            Created
                        </Label>
                        <p className="text-sm">{formatDate(company.created_at)}</p>
                    </div>
                    <div className="space-y-1">
                        <Label className="text-xs uppercase text-muted-foreground">
                            Last Updated
                        </Label>
                        <p className="text-sm">{formatDate(company.updated_at)}</p>
                    </div>
                </CardContent>
            </Card>

            <Card>
                <CardContent className="p-6 space-y-4">
                    <h2 className="font-semibold flex items-center gap-2">
                        <CheckCircle2 className="h-4 w-4 text-muted-foreground" />
                        KYB Verification
                    </h2>
                    <div className="grid grid-cols-1 gap-4 md:grid-cols-2 text-sm">
                        <div>
                            <Label className="text-xs uppercase text-muted-foreground">
                                Reviewed At
                            </Label>
                            <p>{formatDate(company.kyb_reviewed_at)}</p>
                        </div>
                        <div>
                            <Label className="text-xs uppercase text-muted-foreground">
                                Reviewed By
                            </Label>
                            <p>{company.kyb_reviewed_by ?? "—"}</p>
                        </div>
                        {company.kyb_document_url && (
                            <div className="md:col-span-2">
                                <a
                                    className="inline-flex items-center gap-1 text-blue-600 hover:underline"
                                    href={company.kyb_document_url}
                                    target="_blank"
                                    rel="noreferrer"
                                >
                                    <FileText className="h-3 w-3" /> View KYB document
                                </a>
                            </div>
                        )}
                    </div>
                </CardContent>
            </Card>

            <AlertDialog
                open={pendingTransition !== null}
                onOpenChange={(open) => {
                    if (!open) {
                        setPendingTransition(null);
                        setReason("");
                    }
                }}
            >
                <AlertDialogContent>
                    <AlertDialogHeader>
                        <AlertDialogTitle>{transitionCfg?.title}</AlertDialogTitle>
                        <AlertDialogDescription>
                            {transitionCfg?.description}
                        </AlertDialogDescription>
                    </AlertDialogHeader>
                    {transitionCfg?.captureReason && (
                        <div className="space-y-2">
                            <Label htmlFor="transition-reason">Reason (optional)</Label>
                            <Textarea
                                id="transition-reason"
                                value={reason}
                                onChange={(e) => setReason(e.target.value)}
                                maxLength={500}
                                rows={3}
                                placeholder="e.g. overdue balance, compliance issue"
                            />
                        </div>
                    )}
                    <AlertDialogFooter>
                        <AlertDialogCancel disabled={transitioning}>
                            Cancel
                        </AlertDialogCancel>
                        <AlertDialogAction
                            onClick={confirmTransition}
                            disabled={transitioning}
                            className={transitionCfg?.confirmClass}
                        >
                            {transitioning
                                ? "Working…"
                                : transitionCfg?.confirmLabel ?? "Confirm"}
                        </AlertDialogAction>
                    </AlertDialogFooter>
                </AlertDialogContent>
            </AlertDialog>
        </div>
    );
}
