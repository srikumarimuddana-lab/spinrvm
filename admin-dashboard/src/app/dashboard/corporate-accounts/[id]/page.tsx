"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import {
    fetchKybDocumentBlob,
    CorporateAccount,
    CompanyStatus,
    CorporateWallet,
    SizeTier,
    changeCompanyStatus,
    getCorporateAccount,
    getCorporateWallet,
    updateCorporateAccount,
    updateWalletConfig,
    walletAdjust,
} from "@/lib/api";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
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
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog";
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select";
import {
    ArrowLeft,
    Building2,
    CheckCircle2,
    FileText,
    PauseCircle,
    Pencil,
    PlayCircle,
    ShieldCheck,
    Users,
    Wallet,
    XCircle,
} from "lucide-react";
import { useToast } from "@/components/ui/use-toast";

const SIZE_TIER_LABELS: Record<SizeTier, string> = {
    smb: "SMB",
    mid_market: "Mid-Market",
    enterprise: "Enterprise",
};

interface ProfileFormData {
    legal_name: string;
    business_number: string;
    tax_region: string;
    billing_email: string;
    size_tier: SizeTier;
}

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
    const { toast } = useToast();

    const [company, setCompany] = useState<CorporateAccount | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [pendingTransition, setPendingTransition] = useState<TransitionKind | null>(null);
    const [reason, setReason] = useState("");
    const [transitioning, setTransitioning] = useState(false);
    const [wallet, setWallet] = useState<CorporateWallet | null>(null);
    const [walletBusy, setWalletBusy] = useState(false);
    const [isEditProfileOpen, setIsEditProfileOpen] = useState(false);
    const [profileForm, setProfileForm] = useState<ProfileFormData>({
        legal_name: "",
        business_number: "",
        tax_region: "",
        billing_email: "",
        size_tier: "smb",
    });
    const [profileSaving, setProfileSaving] = useState(false);

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

    const loadWallet = useCallback(async () => {
        if (!id) return;
        try {
            setWallet(await getCorporateWallet(id));
        } catch {
            setWallet(null);
        }
    }, [id]);

    useEffect(() => {
        load();
    }, [load]);

    useEffect(() => {
        if (company?.status === "active" || company?.status === "suspended") {
            loadWallet();
        }
    }, [company?.status, loadWallet]);

    const MAX_SINGLE_ADJUSTMENT = 10000; // $10,000 CAD safety cap

    const handleAdjust = async () => {
        if (!id) return;
        const raw = window.prompt("Adjustment amount (signed CAD):");
        if (!raw) return;
        const adjustmentAmount = parseFloat(raw);
        if (isNaN(adjustmentAmount) || adjustmentAmount === 0) {
            toast({ title: "Invalid amount", description: "Adjustment amount cannot be zero", variant: "destructive" });
            return;
        }
        if (Math.abs(adjustmentAmount) > MAX_SINGLE_ADJUSTMENT) {
            toast({
                title: "Amount exceeds limit",
                description: `Single adjustment cannot exceed $${MAX_SINGLE_ADJUSTMENT.toFixed(2)}`,
                variant: "destructive",
            });
            return;
        }
        const notes = window.prompt("Reason (required):") ?? "";
        if (!notes.trim()) return;
        setWalletBusy(true);
        try {
            await walletAdjust(id, { amount: adjustmentAmount, notes: notes.trim() });
            await loadWallet();
        } catch (e: any) {
            toast({ title: "Adjustment failed", description: e?.message, variant: "destructive" });
        } finally {
            setWalletBusy(false);
        }
    };

    const handleToggleAutotopup = async () => {
        if (!id || !wallet) return;
        setWalletBusy(true);
        try {
            await updateWalletConfig(id, {
                auto_topup_enabled: !wallet.auto_topup_enabled,
            });
            await loadWallet();
        } catch (e: any) {
            toast({ title: "Failed to toggle auto top-up", description: e?.message, variant: "destructive" });
        } finally {
            setWalletBusy(false);
        }
    };

    const handleOpenEditProfile = () => {
        if (!company) return;
        setProfileForm({
            legal_name: company.legal_name ?? "",
            business_number: company.business_number ?? "",
            tax_region: company.tax_region ?? "",
            billing_email: company.billing_email ?? "",
            size_tier: company.size_tier,
        });
        setIsEditProfileOpen(true);
    };

    const handleSaveProfile = async (e: React.FormEvent) => {
        e.preventDefault();
        if (!id) return;
        setProfileSaving(true);
        try {
            const updated = await updateCorporateAccount(id, {
                legal_name: profileForm.legal_name.trim() || null,
                business_number: profileForm.business_number.trim() || null,
                tax_region: profileForm.tax_region.trim() || null,
                billing_email: profileForm.billing_email.trim() || null,
                size_tier: profileForm.size_tier,
            });
            setCompany(updated);
            setIsEditProfileOpen(false);
        } catch (e: any) {
            toast({ title: "Failed to save profile", description: e?.message, variant: "destructive" });
        } finally {
            setProfileSaving(false);
        }
    };

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
            toast({ title: "Status change failed", description: e?.message, variant: "destructive" });
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
                    <Link href={`/dashboard/corporate-accounts/${id}/members`}>
                        <Button variant="outline">
                            <Users className="mr-2 h-4 w-4" /> Members
                        </Button>
                    </Link>
                    <Link href={`/dashboard/corporate-accounts/${id}/policy`}>
                        <Button variant="outline">
                            <ShieldCheck className="mr-2 h-4 w-4" /> Policy
                        </Button>
                    </Link>
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
                <CardContent className="p-6 space-y-4">
                    <div className="flex items-center justify-between">
                        <h2 className="font-semibold flex items-center gap-2">
                            <Building2 className="h-4 w-4 text-muted-foreground" />
                            Company Profile
                        </h2>
                        <Button variant="outline" size="sm" onClick={handleOpenEditProfile}>
                            <Pencil className="mr-2 h-3 w-3" /> Edit Profile
                        </Button>
                    </div>
                    <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
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
                        <div>
                            <Label className="text-xs uppercase text-muted-foreground">
                                Submitted At
                            </Label>
                            <p>{formatDate(company.kyb_submitted_at)}</p>
                        </div>
                        {company.kyb_review_note && (
                            <div className="md:col-span-2">
                                <Label className="text-xs uppercase text-muted-foreground">
                                    Review Note
                                </Label>
                                <p className="rounded bg-muted p-2">{company.kyb_review_note}</p>
                            </div>
                        )}
                        {company.kyb_document_url && (
                            <div className="md:col-span-2">
                                <button
                                    type="button"
                                    className="inline-flex items-center gap-1 text-blue-600 hover:underline"
                                    onClick={async () => {
                                        try {
                                            const blob = await fetchKybDocumentBlob(company.id);
                                            const url = URL.createObjectURL(blob);
                                            window.open(url, "_blank", "noopener");
                                            setTimeout(() => URL.revokeObjectURL(url), 60_000);
                                        } catch (e: any) {
                                            toast({
                                                title: "Could not load document",
                                                description: e?.message,
                                                variant: "destructive",
                                            });
                                        }
                                    }}
                                >
                                    <FileText className="h-3 w-3" /> Preview KYB document
                                </button>
                            </div>
                        )}
                    </div>
                </CardContent>
            </Card>

            {wallet && (
                <Card>
                    <CardContent className="p-6 space-y-4">
                        <header className="flex items-center justify-between gap-4">
                            <h2 className="font-semibold flex items-center gap-2">
                                <Wallet className="h-4 w-4 text-muted-foreground" />
                                Master wallet
                            </h2>
                            <span className="text-2xl font-bold tabular-nums">
                                ${parseFloat(wallet.balance).toFixed(2)}{" "}
                                <span className="text-sm text-muted-foreground">
                                    {wallet.currency}
                                </span>
                            </span>
                        </header>

                        <div className="grid grid-cols-1 gap-3 text-sm md:grid-cols-3">
                            <div>
                                <Label className="text-xs uppercase text-muted-foreground">
                                    Auto top-up
                                </Label>
                                <p>
                                    <b>
                                        {wallet.auto_topup_enabled ? "On" : "Off"}
                                    </b>
                                </p>
                            </div>
                            <div>
                                <Label className="text-xs uppercase text-muted-foreground">
                                    Threshold / Amount
                                </Label>
                                <p>
                                    {wallet.auto_topup_threshold ?? "—"} /{" "}
                                    {wallet.auto_topup_amount ?? "—"}
                                </p>
                            </div>
                            <div>
                                <Label className="text-xs uppercase text-muted-foreground">
                                    Daily cap / Floor
                                </Label>
                                <p>
                                    {wallet.auto_topup_daily_cap} /{" "}
                                    {wallet.soft_negative_floor}
                                </p>
                            </div>
                        </div>

                        <div className="flex flex-wrap gap-2">
                            <Button
                                variant="outline"
                                onClick={handleAdjust}
                                disabled={walletBusy}
                            >
                                Adjust balance
                            </Button>
                            <Button
                                variant="outline"
                                onClick={handleToggleAutotopup}
                                disabled={
                                    walletBusy ||
                                    (!wallet.auto_topup_enabled &&
                                        (wallet.auto_topup_threshold == null ||
                                            wallet.auto_topup_amount == null))
                                }
                                title={
                                    !wallet.auto_topup_enabled &&
                                    (wallet.auto_topup_threshold == null ||
                                        wallet.auto_topup_amount == null)
                                        ? "Set threshold and amount before enabling"
                                        : undefined
                                }
                            >
                                {wallet.auto_topup_enabled
                                    ? "Disable auto top-up"
                                    : "Enable auto top-up"}
                            </Button>
                        </div>

                        <div>
                            <h3 className="mt-2 font-semibold text-sm">
                                Recent transactions
                            </h3>
                            {wallet.transactions.length === 0 ? (
                                <p className="mt-2 text-sm text-muted-foreground">
                                    No transactions yet.
                                </p>
                            ) : (
                                <ul className="mt-2 divide-y text-sm">
                                    {wallet.transactions.map((t) => (
                                        <li
                                            key={t.id}
                                            className="flex items-center justify-between py-2"
                                        >
                                            <span className="flex items-center gap-3">
                                                <span className="inline-block w-28 capitalize">
                                                    {t.type}
                                                </span>
                                                <span className="text-muted-foreground">
                                                    {formatDate(t.created_at)}
                                                </span>
                                            </span>
                                            <span className="tabular-nums">
                                                {parseFloat(t.amount) >= 0 ? "+" : ""}
                                                ${parseFloat(t.amount).toFixed(2)}
                                                <span className="text-muted-foreground ml-2">
                                                    bal $
                                                    {parseFloat(
                                                        t.balance_after
                                                    ).toFixed(2)}
                                                </span>
                                            </span>
                                        </li>
                                    ))}
                                </ul>
                            )}
                        </div>
                    </CardContent>
                </Card>
            )}

            <Dialog open={isEditProfileOpen} onOpenChange={setIsEditProfileOpen}>
                <DialogContent>
                    <DialogHeader>
                        <DialogTitle>Edit Company Profile</DialogTitle>
                        <DialogDescription>
                            Legal and billing details for {company.name}.
                        </DialogDescription>
                    </DialogHeader>
                    <form onSubmit={handleSaveProfile} className="space-y-4">
                        <div className="space-y-2">
                            <Label htmlFor="legal_name">Legal Name</Label>
                            <Input
                                id="legal_name"
                                value={profileForm.legal_name}
                                onChange={(e) =>
                                    setProfileForm({ ...profileForm, legal_name: e.target.value })
                                }
                                placeholder="e.g. Acme Corporation Inc."
                            />
                        </div>
                        <div className="grid grid-cols-2 gap-4">
                            <div className="space-y-2">
                                <Label htmlFor="business_number">Business Number</Label>
                                <Input
                                    id="business_number"
                                    value={profileForm.business_number}
                                    onChange={(e) =>
                                        setProfileForm({ ...profileForm, business_number: e.target.value })
                                    }
                                    placeholder="123456789RT0001"
                                />
                            </div>
                            <div className="space-y-2">
                                <Label htmlFor="tax_region">Tax Region</Label>
                                <Input
                                    id="tax_region"
                                    value={profileForm.tax_region}
                                    onChange={(e) =>
                                        setProfileForm({ ...profileForm, tax_region: e.target.value })
                                    }
                                    placeholder="e.g. SK"
                                />
                            </div>
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="billing_email">Billing Email</Label>
                            <Input
                                id="billing_email"
                                type="email"
                                value={profileForm.billing_email}
                                onChange={(e) =>
                                    setProfileForm({ ...profileForm, billing_email: e.target.value })
                                }
                                placeholder="billing@acme.com"
                            />
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="size_tier">Size Tier</Label>
                            <Select
                                value={profileForm.size_tier}
                                onValueChange={(v) =>
                                    setProfileForm({ ...profileForm, size_tier: v as SizeTier })
                                }
                            >
                                <SelectTrigger id="size_tier">
                                    <SelectValue />
                                </SelectTrigger>
                                <SelectContent>
                                    {(Object.keys(SIZE_TIER_LABELS) as SizeTier[]).map((tier) => (
                                        <SelectItem key={tier} value={tier}>
                                            {SIZE_TIER_LABELS[tier]}
                                        </SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                        </div>
                        <DialogFooter className="pt-4">
                            <Button
                                type="button"
                                variant="outline"
                                onClick={() => setIsEditProfileOpen(false)}
                            >
                                Cancel
                            </Button>
                            <Button type="submit" disabled={profileSaving}>
                                {profileSaving ? "Saving..." : "Save Profile"}
                            </Button>
                        </DialogFooter>
                    </form>
                </DialogContent>
            </Dialog>

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
