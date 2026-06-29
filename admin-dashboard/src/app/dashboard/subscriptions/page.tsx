"use client";

import { useCallback, useEffect, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import {
    Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";
import {
    Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import {
    Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import { Pagination } from "@/components/ui/pagination";
import { useTableSort, SortableHead } from "@/components/ui/sortable-table";
import {
    CreditCard, Plus, Pencil, Trash2, RefreshCw, Users,
    DollarSign, TrendingUp, XCircle, CheckCircle, Clock,
    Receipt, Settings2, ChevronLeft, ChevronRight, Download, Send,
} from "lucide-react";
import { useToast } from "@/components/ui/use-toast";
import { formatCurrency, formatDate } from "@/lib/utils";
import {
    getSubscriptionPlans,
    createSubscriptionPlan,
    updateSubscriptionPlan,
    deleteSubscriptionPlan,
    getDriverSubscriptions,
    getSubscriptionStats,
    getAdminSubscriptionPayments,
    downloadSubscriptionInvoice,
    resendAdminSubscriptionInvoice,
    updateSubscriptionTaxConfig,
    getServiceAreas,
} from "@/lib/api";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface SubscriptionPlan {
    id: string;
    name: string;
    price: number;
    duration_days: number;
    rides_per_day: number;
    description?: string;
    features?: string[];
    is_active: boolean;
    subscriber_count?: number;
    created_at?: string;
    // Recurring Stripe Price (price_...). When set, the plan auto-renews via
    // Stripe Billing; blank keeps it on one-off Checkout-per-period.
    stripe_price_id?: string;
}

interface DriverSubscription {
    id: string;
    driver_id: string;
    driver_name?: string;
    plan_id?: string;
    plan_name?: string;
    price: number;
    status: "active" | "expired" | "cancelled";
    started_at?: string;
    expires_at?: string;
    created_at?: string;
}

interface SubStats {
    total_subscribers: number;
    active: number;
    expired: number;
    cancelled: number;
    total_revenue: number;
    active_mrr: number;
}

interface SubscriptionPayment {
    id: string;
    driver_id: string;
    driver_name: string;
    plan_id?: string;
    plan_name?: string;
    amount: number;
    subtotal: number;
    gst_amount: number;
    pst_amount: number;
    hst_amount: number;
    province: string;
    currency: string;
    billing_reason?: string;
    stripe_invoice_id?: string;
    created_at?: string;
}

interface TaxConfig {
    enabled: boolean;
    province: string;
    gst_rate: number;
    pst_rate: number;
    hst_rate: number;
}

interface ServiceArea {
    id: string;
    name: string;
    subscription_tax_config?: TaxConfig;
}

const EMPTY_PLAN: Omit<SubscriptionPlan, "id" | "created_at"> = {
    name: "",
    price: 0,
    duration_days: 30,
    rides_per_day: -1,
    description: "",
    features: [],
    is_active: true,
    stripe_price_id: "",
};

const STATUS_CONFIG: Record<string, { label: string; variant: "default" | "secondary" | "destructive" }> = {
    active: { label: "Active", variant: "default" },
    expired: { label: "Expired", variant: "secondary" },
    cancelled: { label: "Cancelled", variant: "destructive" },
};

const PAGE_SIZE = 20;

// ---------------------------------------------------------------------------
// Plan Form Modal
// ---------------------------------------------------------------------------

interface PlanModalProps {
    open: boolean;
    plan: Partial<SubscriptionPlan> | null;
    onClose: () => void;
    onSave: (data: Partial<SubscriptionPlan>) => Promise<void>;
}

function PlanModal({ open, plan, onClose, onSave }: PlanModalProps) {
    const isNew = !plan?.id;
    const [form, setForm] = useState({ ...EMPTY_PLAN });
    const [featuresText, setFeaturesText] = useState("");
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState("");

    useEffect(() => {
        if (plan) {
            setForm({
                name: plan.name ?? "",
                price: plan.price ?? 0,
                duration_days: plan.duration_days ?? 30,
                rides_per_day: plan.rides_per_day ?? -1,
                description: plan.description ?? "",
                features: plan.features ?? [],
                is_active: plan.is_active ?? true,
                stripe_price_id: plan.stripe_price_id ?? "",
            });
            setFeaturesText((plan.features ?? []).join("\n"));
        } else {
            setForm({ ...EMPTY_PLAN });
            setFeaturesText("");
        }
        setError("");
    }, [plan, open]);

    const handleSubmit = async () => {
        if (!form.name.trim()) { setError("Plan name is required."); return; }
        if (form.price < 0) { setError("Price must be ≥ 0."); return; }
        setSaving(true);
        try {
            await onSave({
                ...form,
                features: featuresText.split("\n").map((f) => f.trim()).filter(Boolean),
            });
            onClose();
        } catch {
            setError("Failed to save plan. Please try again.");
        } finally {
            setSaving(false);
        }
    };

    return (
        <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
            <DialogContent>
                <DialogHeader>
                    <DialogTitle>{isNew ? "Create Subscription Plan" : "Edit Subscription Plan"}</DialogTitle>
                </DialogHeader>
                <div className="space-y-4 py-2">
                    <div className="grid grid-cols-2 gap-4">
                        <div className="col-span-2 space-y-1">
                            <Label htmlFor="plan-name">Plan Name</Label>
                            <Input
                                id="plan-name"
                                value={form.name}
                                onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
                                placeholder="e.g. Pro 30-Day Pass"
                            />
                        </div>
                        <div className="space-y-1">
                            <Label htmlFor="plan-price">Price (CAD)</Label>
                            <Input
                                id="plan-price"
                                type="number"
                                min={0}
                                step={0.01}
                                value={form.price}
                                onChange={(e) => setForm((f) => ({ ...f, price: parseFloat(e.target.value) || 0 }))}
                            />
                        </div>
                        <div className="space-y-1">
                            <Label htmlFor="plan-duration">Duration (days)</Label>
                            <Select
                                value={String(form.duration_days)}
                                onValueChange={(v) => setForm((f) => ({ ...f, duration_days: parseInt(v) }))}
                            >
                                <SelectTrigger id="plan-duration">
                                    <SelectValue />
                                </SelectTrigger>
                                <SelectContent>
                                    <SelectItem value="1">1-day pass (24 hrs)</SelectItem>
                                    <SelectItem value="7">7-day pass</SelectItem>
                                    <SelectItem value="30">30-day pass</SelectItem>
                                    <SelectItem value="90">90-day pass</SelectItem>
                                    <SelectItem value="365">365-day pass</SelectItem>
                                </SelectContent>
                            </Select>
                        </div>
                        <div className="space-y-1">
                            <Label htmlFor="plan-rides">Rides per Day</Label>
                            <Select
                                value={String(form.rides_per_day)}
                                onValueChange={(v) => setForm((f) => ({ ...f, rides_per_day: parseInt(v) }))}
                            >
                                <SelectTrigger id="plan-rides">
                                    <SelectValue />
                                </SelectTrigger>
                                <SelectContent>
                                    <SelectItem value="-1">Unlimited</SelectItem>
                                    <SelectItem value="4">4 rides/day</SelectItem>
                                    <SelectItem value="8">8 rides/day</SelectItem>
                                    <SelectItem value="12">12 rides/day</SelectItem>
                                </SelectContent>
                            </Select>
                        </div>
                        <div className="flex items-center gap-2 pt-5">
                            <Switch
                                checked={form.is_active}
                                onCheckedChange={(v) => setForm((f) => ({ ...f, is_active: v }))}
                            />
                            <Label>Active</Label>
                        </div>
                        <div className="col-span-2 space-y-1">
                            <Label htmlFor="plan-desc">Description</Label>
                            <Input
                                id="plan-desc"
                                value={form.description}
                                onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))}
                                placeholder="Short description shown to drivers"
                            />
                        </div>
                        <div className="col-span-2 space-y-1">
                            <Label htmlFor="plan-features">Features (one per line)</Label>
                            <Textarea
                                id="plan-features"
                                rows={4}
                                value={featuresText}
                                onChange={(e) => setFeaturesText(e.target.value)}
                                placeholder={"Priority support\nSurge protection\nUnlimited rides"}
                            />
                        </div>
                        <div className="col-span-2 space-y-1">
                            <Label htmlFor="plan-price-id">Stripe Price ID (recurring)</Label>
                            <Input
                                id="plan-price-id"
                                value={form.stripe_price_id || ""}
                                onChange={(e) => setForm((f) => ({ ...f, stripe_price_id: e.target.value }))}
                                placeholder="price_... (leave blank for one-off per period)"
                            />
                            <p className="text-xs text-muted-foreground">
                                Set a recurring Stripe Price to auto-renew this plan via Stripe
                                Billing. Its amount/currency must match the price above. Blank =
                                one-off Checkout each period.
                            </p>
                        </div>
                    </div>
                    {error && <p className="text-sm text-red-500">{error}</p>}
                </div>
                <DialogFooter>
                    <Button variant="outline" onClick={onClose} disabled={saving}>
                        Cancel
                    </Button>
                    <Button onClick={handleSubmit} disabled={saving}>
                        {saving ? "Saving…" : isNew ? "Create Plan" : "Save Changes"}
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}

// ---------------------------------------------------------------------------
// Delete Confirm Modal
// ---------------------------------------------------------------------------

interface DeleteModalProps {
    open: boolean;
    planName: string;
    onClose: () => void;
    onConfirm: () => Promise<void>;
}

function DeleteModal({ open, planName, onClose, onConfirm }: DeleteModalProps) {
    const [deleting, setDeleting] = useState(false);
    const handleConfirm = async () => {
        setDeleting(true);
        try { await onConfirm(); onClose(); }
        finally { setDeleting(false); }
    };
    return (
        <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
            <DialogContent>
                <DialogHeader>
                    <DialogTitle>Delete Subscription Plan</DialogTitle>
                </DialogHeader>
                <p className="text-sm text-muted-foreground py-2">
                    Are you sure you want to delete <strong>{planName}</strong>? Existing driver subscriptions will
                    retain their plan reference but no new subscriptions can be created.
                </p>
                <DialogFooter>
                    <Button variant="outline" onClick={onClose} disabled={deleting}>Cancel</Button>
                    <Button variant="destructive" onClick={handleConfirm} disabled={deleting}>
                        {deleting ? "Deleting…" : "Delete Plan"}
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}

// ---------------------------------------------------------------------------
// Tax Config Modal
// ---------------------------------------------------------------------------

interface TaxConfigModalProps {
    open: boolean;
    area: ServiceArea | null;
    onClose: () => void;
    onSave: (areaId: string, config: TaxConfig) => Promise<void>;
}

function TaxConfigModal({ open, area, onClose, onSave }: TaxConfigModalProps) {
    const defaultCfg: TaxConfig = { enabled: true, province: "SK", gst_rate: 5.0, pst_rate: 6.0, hst_rate: 0.0 };
    const [form, setForm] = useState<TaxConfig>(defaultCfg);
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState("");

    useEffect(() => {
        if (area?.subscription_tax_config) {
            setForm({ ...defaultCfg, ...area.subscription_tax_config });
        } else {
            setForm(defaultCfg);
        }
        setError("");
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [area, open]);

    const handleSave = async () => {
        if (!area) return;
        setSaving(true);
        try {
            await onSave(area.id, form);
            onClose();
        } catch {
            setError("Failed to save tax config. Please try again.");
        } finally {
            setSaving(false);
        }
    };

    return (
        <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
            <DialogContent>
                <DialogHeader>
                    <DialogTitle>Tax Configuration — {area?.name}</DialogTitle>
                </DialogHeader>
                <div className="space-y-4 py-2">
                    <div className="flex items-center gap-3">
                        <Switch
                            checked={form.enabled}
                            onCheckedChange={(v) => setForm((f) => ({ ...f, enabled: v }))}
                        />
                        <Label>Tax collection enabled</Label>
                    </div>
                    <div className="space-y-1">
                        <Label>Province code</Label>
                        <Input
                            value={form.province}
                            maxLength={3}
                            placeholder="SK"
                            onChange={(e) => setForm((f) => ({ ...f, province: e.target.value.toUpperCase() }))}
                        />
                    </div>
                    <div className="grid grid-cols-3 gap-3">
                        <div className="space-y-1">
                            <Label>GST rate (%)</Label>
                            <Input
                                type="number"
                                min={0}
                                max={25}
                                step={0.01}
                                value={form.gst_rate}
                                onChange={(e) => setForm((f) => ({ ...f, gst_rate: parseFloat(e.target.value) || 0 }))}
                            />
                        </div>
                        <div className="space-y-1">
                            <Label>PST rate (%)</Label>
                            <Input
                                type="number"
                                min={0}
                                max={25}
                                step={0.01}
                                value={form.pst_rate}
                                onChange={(e) => setForm((f) => ({ ...f, pst_rate: parseFloat(e.target.value) || 0 }))}
                            />
                        </div>
                        <div className="space-y-1">
                            <Label>HST rate (%)</Label>
                            <Input
                                type="number"
                                min={0}
                                max={25}
                                step={0.01}
                                value={form.hst_rate}
                                onChange={(e) => setForm((f) => ({ ...f, hst_rate: parseFloat(e.target.value) || 0 }))}
                            />
                        </div>
                    </div>
                    <p className="text-xs text-muted-foreground">
                        SK: GST 5% + PST 6%. AB: GST 5%, PST 0%. ON: HST 13% (set HST, zero GST+PST).
                        Changes apply to the next checkout in this service area.
                    </p>
                    {error && <p className="text-sm text-red-500">{error}</p>}
                </div>
                <DialogFooter>
                    <Button variant="outline" onClick={onClose} disabled={saving}>Cancel</Button>
                    <Button onClick={handleSave} disabled={saving}>
                        {saving ? "Saving…" : "Save Tax Config"}
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}

// ---------------------------------------------------------------------------
// Main Page
// ---------------------------------------------------------------------------

type TabId = "plans" | "drivers" | "transactions" | "tax";

export default function SubscriptionsPage() {
    const [activeTab, setActiveTab] = useState<TabId>("plans");
    const [plans, setPlans] = useState<SubscriptionPlan[]>([]);
    const [driverSubs, setDriverSubs] = useState<DriverSubscription[]>([]);
    const [stats, setStats] = useState<SubStats | null>(null);
    const [loading, setLoading] = useState(true);
    const { toast } = useToast();
    const [statusFilter, setStatusFilter] = useState<string>("all");
    const [subPage, setSubPage] = useState(1);

    // Transactions tab state
    const [transactions, setTransactions] = useState<SubscriptionPayment[]>([]);
    const [txTotal, setTxTotal] = useState(0);
    const [txPage, setTxPage] = useState(1);
    const [txLoading, setTxLoading] = useState(false);
    const [downloadingId, setDownloadingId] = useState<string | null>(null);
    const [sendingId, setSendingId] = useState<string | null>(null);
    const TX_PAGE_SIZE = 50;

    // Tax config tab state
    const [serviceAreas, setServiceAreas] = useState<ServiceArea[]>([]);
    const [taxModalOpen, setTaxModalOpen] = useState(false);
    const [editingArea, setEditingArea] = useState<ServiceArea | null>(null);

    // Plan modal state
    const [planModalOpen, setPlanModalOpen] = useState(false);
    const [editingPlan, setEditingPlan] = useState<SubscriptionPlan | null>(null);

    // Delete modal state
    const [deleteModalOpen, setDeleteModalOpen] = useState(false);
    const [deletingPlan, setDeletingPlan] = useState<SubscriptionPlan | null>(null);

    const load = useCallback(async () => {
        setLoading(true);
        try {
            const [plansData, subsData, statsData, areasData] = await Promise.all([
                getSubscriptionPlans(),
                getDriverSubscriptions(statusFilter === "all" ? undefined : statusFilter),
                getSubscriptionStats(),
                getServiceAreas().catch(() => []),
            ]);
            setPlans(plansData ?? []);
            setDriverSubs(subsData ?? []);
            setStats(statsData?.stats ?? null);
            setServiceAreas((Array.isArray(areasData) ? areasData : []).filter(
                (a: ServiceArea) => !("parent_service_area_id" in a && a.parent_service_area_id),
            ));
        } catch {
            // non-fatal — page still renders with empty state
        } finally {
            setLoading(false);
        }
    }, [statusFilter]);

    const loadTransactions = useCallback(async (page: number) => {
        setTxLoading(true);
        try {
            const data = await getAdminSubscriptionPayments({
                limit: TX_PAGE_SIZE,
                offset: (page - 1) * TX_PAGE_SIZE,
            });
            setTransactions(data?.payments ?? []);
            setTxTotal(data?.total ?? 0);
        } catch {
            // non-fatal
        } finally {
            setTxLoading(false);
        }
    }, []);

    useEffect(() => { load(); }, [load]);
    useEffect(() => { setSubPage(1); }, [statusFilter]);
    useEffect(() => {
        if (activeTab === "transactions") loadTransactions(txPage);
    }, [activeTab, txPage, loadTransactions]);

    // Plan CRUD handlers
    const handleSavePlan = async (data: Partial<SubscriptionPlan>) => {
        if (editingPlan?.id) {
            await updateSubscriptionPlan(editingPlan.id, data);
        } else {
            await createSubscriptionPlan(data);
        }
        await load();
    };

    const handleDeletePlan = async () => {
        if (!deletingPlan) return;
        await deleteSubscriptionPlan(deletingPlan.id);
        await load();
    };

    const openCreate = () => { setEditingPlan(null); setPlanModalOpen(true); };
    const openEdit = (p: SubscriptionPlan) => { setEditingPlan(p); setPlanModalOpen(true); };
    const openDelete = (p: SubscriptionPlan) => { setDeletingPlan(p); setDeleteModalOpen(true); };

    const handleSaveTaxConfig = async (areaId: string, config: TaxConfig) => {
        await updateSubscriptionTaxConfig(areaId, config);
        await load();
    };

    const handleDownloadInvoice = async (paymentId: string) => {
        setDownloadingId(paymentId);
        try {
            const ok = await downloadSubscriptionInvoice(paymentId);
            if (!ok) {
                toast({ title: "Download failed", description: "Could not generate the invoice PDF.", variant: "destructive" });
            }
        } finally {
            setDownloadingId(null);
        }
    };

    const handleResendInvoice = async (paymentId: string) => {
        setSendingId(paymentId);
        try {
            await resendAdminSubscriptionInvoice(paymentId);
            toast({ title: "Invoice sent", description: "Emailed to the driver's address on file." });
        } catch (e: unknown) {
            const detail = e instanceof Error ? e.message : "Could not send the invoice email.";
            toast({ title: "Send failed", description: detail, variant: "destructive" });
        } finally {
            setSendingId(null);
        }
    };

    // Filtered + paginated driver subs
    const filteredSubs = statusFilter === "all"
        ? driverSubs
        : driverSubs.filter((s) => s.status === statusFilter);
    const pagedSubs = filteredSubs.slice((subPage - 1) * PAGE_SIZE, subPage * PAGE_SIZE);

    // Client-side sort state (one per table)
    const { sorted: sortedPlans, sort: plansSort, toggle: plansToggle } = useTableSort(plans);
    const { sorted: sortedSubs, sort: subsSort, toggle: subsToggle } = useTableSort(pagedSubs);
    const { sorted: sortedTransactions, sort: txSort, toggle: txToggle } = useTableSort(transactions);
    const { sorted: sortedAreas, sort: areasSort, toggle: areasToggle } = useTableSort(serviceAreas);

    const TABS: { id: TabId; label: string; icon: React.ReactNode }[] = [
        { id: "plans", label: "Plans", icon: <Users className="h-4 w-4" /> },
        { id: "drivers", label: "Driver Subscriptions", icon: <CreditCard className="h-4 w-4" /> },
        { id: "transactions", label: "Transactions", icon: <Receipt className="h-4 w-4" /> },
        { id: "tax", label: "Tax Config", icon: <Settings2 className="h-4 w-4" /> },
    ];

    return (
        <div className="space-y-6 p-6">
            {/* Header */}
            <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                    <CreditCard className="h-6 w-6 text-primary" />
                    <div>
                        <h1 className="text-2xl font-bold">Spinr Pass Subscriptions</h1>
                        <p className="text-sm text-muted-foreground">Manage plans, subscriptions, transactions, and tax rates</p>
                    </div>
                </div>
                <Button onClick={load} variant="outline" disabled={loading}>
                    <RefreshCw className={`h-4 w-4 mr-2 ${loading ? "animate-spin" : ""}`} />
                    Refresh
                </Button>
            </div>

            {/* Stats */}
            {stats && (
                <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-6">
                    <Card>
                        <CardContent className="pt-4 pb-3">
                            <p className="text-xs text-muted-foreground">Total Subscribers</p>
                            <p className="text-2xl font-bold">{stats.total_subscribers}</p>
                        </CardContent>
                    </Card>
                    <Card>
                        <CardContent className="pt-4 pb-3">
                            <div className="flex items-center gap-1">
                                <CheckCircle className="h-3 w-3 text-green-500" />
                                <p className="text-xs text-muted-foreground">Active</p>
                            </div>
                            <p className="text-2xl font-bold text-green-600">{stats.active}</p>
                        </CardContent>
                    </Card>
                    <Card>
                        <CardContent className="pt-4 pb-3">
                            <div className="flex items-center gap-1">
                                <Clock className="h-3 w-3 text-yellow-500" />
                                <p className="text-xs text-muted-foreground">Expired</p>
                            </div>
                            <p className="text-2xl font-bold text-yellow-600">{stats.expired}</p>
                        </CardContent>
                    </Card>
                    <Card>
                        <CardContent className="pt-4 pb-3">
                            <div className="flex items-center gap-1">
                                <XCircle className="h-3 w-3 text-red-500" />
                                <p className="text-xs text-muted-foreground">Cancelled</p>
                            </div>
                            <p className="text-2xl font-bold text-red-600">{stats.cancelled}</p>
                        </CardContent>
                    </Card>
                    <Card>
                        <CardContent className="pt-4 pb-3">
                            <div className="flex items-center gap-1">
                                <DollarSign className="h-3 w-3 text-primary" />
                                <p className="text-xs text-muted-foreground">Total Revenue</p>
                            </div>
                            <p className="text-2xl font-bold">{formatCurrency(stats.total_revenue)}</p>
                        </CardContent>
                    </Card>
                    <Card>
                        <CardContent className="pt-4 pb-3">
                            <div className="flex items-center gap-1">
                                <TrendingUp className="h-3 w-3 text-primary" />
                                <p className="text-xs text-muted-foreground">Active MRR</p>
                            </div>
                            <p className="text-2xl font-bold">{formatCurrency(stats.active_mrr)}</p>
                        </CardContent>
                    </Card>
                </div>
            )}

            {/* Tab bar */}
            <div className="flex gap-1 border-b">
                {TABS.map((t) => (
                    <button
                        key={t.id}
                        onClick={() => setActiveTab(t.id)}
                        className={`flex items-center gap-2 px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
                            activeTab === t.id
                                ? "border-primary text-primary"
                                : "border-transparent text-muted-foreground hover:text-foreground"
                        }`}
                    >
                        {t.icon}
                        {t.label}
                    </button>
                ))}
            </div>

            {/* Plans Tab */}
            {activeTab === "plans" && (
                <Card>
                    <CardHeader className="flex flex-row items-center justify-between pb-3">
                        <CardTitle className="flex items-center gap-2">
                            <Users className="h-4 w-4" />
                            Subscription Plans
                        </CardTitle>
                        <Button size="sm" onClick={openCreate}>
                            <Plus className="h-4 w-4 mr-2" />
                            New Plan
                        </Button>
                    </CardHeader>
                    <CardContent>
                        {plans.length === 0 && !loading ? (
                            <p className="text-sm text-muted-foreground py-4 text-center">No subscription plans yet.</p>
                        ) : (
                            <Table>
                                <TableHeader>
                                    <TableRow>
                                        <SortableHead column="name" sort={plansSort} onSort={plansToggle}>Name</SortableHead>
                                        <SortableHead column="price" sort={plansSort} onSort={plansToggle}>Price (pre-tax)</SortableHead>
                                        <SortableHead column="duration_days" sort={plansSort} onSort={plansToggle}>Duration</SortableHead>
                                        <SortableHead column="rides_per_day" sort={plansSort} onSort={plansToggle}>Rides/Day</SortableHead>
                                        <SortableHead column="subscriber_count" sort={plansSort} onSort={plansToggle}>Subscribers</SortableHead>
                                        <SortableHead column="is_active" sort={plansSort} onSort={plansToggle}>Status</SortableHead>
                                        <TableHead className="text-right">Actions</TableHead>
                                    </TableRow>
                                </TableHeader>
                                <TableBody>
                                    {sortedPlans.map((plan) => (
                                        <TableRow key={plan.id}>
                                            <TableCell className="font-medium">
                                                <div>{plan.name}</div>
                                                {plan.description && (
                                                    <div className="text-xs text-muted-foreground">{plan.description}</div>
                                                )}
                                            </TableCell>
                                            <TableCell>{formatCurrency(plan.price)}</TableCell>
                                            <TableCell>{plan.duration_days}d</TableCell>
                                            <TableCell>
                                                {plan.rides_per_day === -1 ? "Unlimited" : plan.rides_per_day}
                                            </TableCell>
                                            <TableCell>{plan.subscriber_count ?? 0}</TableCell>
                                            <TableCell>
                                                <Badge variant={plan.is_active ? "default" : "secondary"}>
                                                    {plan.is_active ? "Active" : "Inactive"}
                                                </Badge>
                                            </TableCell>
                                            <TableCell className="text-right">
                                                <div className="flex justify-end gap-2">
                                                    <Button size="sm" variant="outline" onClick={() => openEdit(plan)}>
                                                        <Pencil className="h-3 w-3" />
                                                    </Button>
                                                    <Button size="sm" variant="outline" onClick={() => openDelete(plan)}>
                                                        <Trash2 className="h-3 w-3 text-red-500" />
                                                    </Button>
                                                </div>
                                            </TableCell>
                                        </TableRow>
                                    ))}
                                </TableBody>
                            </Table>
                        )}
                    </CardContent>
                </Card>
            )}

            {/* Driver Subscriptions Tab */}
            {activeTab === "drivers" && (
                <Card>
                    <CardHeader className="flex flex-row items-center justify-between pb-3">
                        <CardTitle className="flex items-center gap-2">
                            <CreditCard className="h-4 w-4" />
                            Driver Subscriptions
                        </CardTitle>
                        <Select value={statusFilter} onValueChange={setStatusFilter}>
                            <SelectTrigger className="w-40">
                                <SelectValue placeholder="Filter status" />
                            </SelectTrigger>
                            <SelectContent>
                                <SelectItem value="all">All Statuses</SelectItem>
                                <SelectItem value="active">Active</SelectItem>
                                <SelectItem value="expired">Expired</SelectItem>
                                <SelectItem value="cancelled">Cancelled</SelectItem>
                            </SelectContent>
                        </Select>
                    </CardHeader>
                    <CardContent>
                        {filteredSubs.length === 0 && !loading ? (
                            <p className="text-sm text-muted-foreground py-4 text-center">No subscriptions found.</p>
                        ) : (
                            <>
                                <Table>
                                    <TableHeader>
                                        <TableRow>
                                            <SortableHead column="driver_name" sort={subsSort} onSort={subsToggle}>Driver</SortableHead>
                                            <SortableHead column="plan_name" sort={subsSort} onSort={subsToggle}>Plan</SortableHead>
                                            <SortableHead column="price" sort={subsSort} onSort={subsToggle}>Price</SortableHead>
                                            <SortableHead column="status" sort={subsSort} onSort={subsToggle}>Status</SortableHead>
                                            <SortableHead column="started_at" sort={subsSort} onSort={subsToggle}>Started</SortableHead>
                                            <SortableHead column="expires_at" sort={subsSort} onSort={subsToggle}>Expires</SortableHead>
                                        </TableRow>
                                    </TableHeader>
                                    <TableBody>
                                        {sortedSubs.map((sub) => {
                                            const sc = STATUS_CONFIG[sub.status] ?? { label: sub.status, variant: "secondary" as const };
                                            return (
                                                <TableRow key={sub.id}>
                                                    <TableCell className="font-mono text-xs">
                                                        {sub.driver_name || sub.driver_id?.slice(0, 8)}
                                                    </TableCell>
                                                    <TableCell>{sub.plan_name ?? "—"}</TableCell>
                                                    <TableCell>{formatCurrency(sub.price)}</TableCell>
                                                    <TableCell>
                                                        <Badge variant={sc.variant}>{sc.label}</Badge>
                                                    </TableCell>
                                                    <TableCell className="text-xs">
                                                        {sub.started_at ? formatDate(sub.started_at) : "—"}
                                                    </TableCell>
                                                    <TableCell className="text-xs">
                                                        {sub.expires_at ? formatDate(sub.expires_at) : "—"}
                                                    </TableCell>
                                                </TableRow>
                                            );
                                        })}
                                    </TableBody>
                                </Table>
                                {filteredSubs.length > PAGE_SIZE && (
                                    <div className="mt-4">
                                        <Pagination
                                            page={subPage}
                                            totalCount={filteredSubs.length}
                                            pageSize={PAGE_SIZE}
                                            hasNextPage={subPage * PAGE_SIZE < filteredSubs.length}
                                            onPageChange={setSubPage}
                                        />
                                    </div>
                                )}
                            </>
                        )}
                    </CardContent>
                </Card>
            )}

            {/* Transactions Tab */}
            {activeTab === "transactions" && (
                <Card>
                    <CardHeader className="pb-3">
                        <CardTitle className="flex items-center gap-2">
                            <Receipt className="h-4 w-4" />
                            Payment Transactions
                            <span className="ml-auto text-sm font-normal text-muted-foreground">
                                {txTotal} total
                            </span>
                        </CardTitle>
                    </CardHeader>
                    <CardContent>
                        {txLoading ? (
                            <p className="text-sm text-muted-foreground py-8 text-center">Loading…</p>
                        ) : transactions.length === 0 ? (
                            <p className="text-sm text-muted-foreground py-8 text-center">No transactions yet.</p>
                        ) : (
                            <>
                                <Table>
                                    <TableHeader>
                                        <TableRow>
                                            <SortableHead column="created_at" sort={txSort} onSort={txToggle}>Date</SortableHead>
                                            <SortableHead column="driver_name" sort={txSort} onSort={txToggle}>Driver</SortableHead>
                                            <SortableHead column="plan_name" sort={txSort} onSort={txToggle}>Plan</SortableHead>
                                            <SortableHead column="billing_reason" sort={txSort} onSort={txToggle}>Type</SortableHead>
                                            <SortableHead column="subtotal" sort={txSort} onSort={txToggle} align="right">Subtotal</SortableHead>
                                            <SortableHead column="gst_amount" sort={txSort} onSort={txToggle} align="right">GST</SortableHead>
                                            <SortableHead column="pst_amount" sort={txSort} onSort={txToggle} align="right">PST</SortableHead>
                                            <SortableHead column="hst_amount" sort={txSort} onSort={txToggle} align="right">HST</SortableHead>
                                            <SortableHead column="amount" sort={txSort} onSort={txToggle} align="right">Total</SortableHead>
                                            <TableHead className="text-right">Invoice</TableHead>
                                        </TableRow>
                                    </TableHeader>
                                    <TableBody>
                                        {sortedTransactions.map((tx) => (
                                            <TableRow key={tx.id}>
                                                <TableCell className="text-xs whitespace-nowrap">
                                                    {tx.created_at ? formatDate(tx.created_at) : "—"}
                                                </TableCell>
                                                <TableCell className="text-xs">
                                                    {tx.driver_name || tx.driver_id?.slice(0, 8)}
                                                </TableCell>
                                                <TableCell className="text-xs">{tx.plan_name ?? "—"}</TableCell>
                                                <TableCell>
                                                    <Badge variant="secondary" className="text-xs">
                                                        {tx.billing_reason === "subscription_cycle"
                                                            ? "Renewal"
                                                            : tx.billing_reason === "one_off"
                                                            ? "One-off"
                                                            : tx.billing_reason ?? "—"}
                                                    </Badge>
                                                </TableCell>
                                                <TableCell className="text-right text-xs tabular-nums">
                                                    {formatCurrency(tx.subtotal)}
                                                </TableCell>
                                                <TableCell className="text-right text-xs tabular-nums text-muted-foreground">
                                                    {tx.gst_amount > 0 ? formatCurrency(tx.gst_amount) : "—"}
                                                </TableCell>
                                                <TableCell className="text-right text-xs tabular-nums text-muted-foreground">
                                                    {tx.pst_amount > 0 ? formatCurrency(tx.pst_amount) : "—"}
                                                </TableCell>
                                                <TableCell className="text-right text-xs tabular-nums text-muted-foreground">
                                                    {tx.hst_amount > 0 ? formatCurrency(tx.hst_amount) : "—"}
                                                </TableCell>
                                                <TableCell className="text-right text-xs font-semibold tabular-nums">
                                                    {formatCurrency(tx.amount)}
                                                </TableCell>
                                                <TableCell className="text-right">
                                                    <div className="flex items-center justify-end gap-1">
                                                        <Button
                                                            variant="ghost"
                                                            size="sm"
                                                            className="h-7 px-2"
                                                            title="Download invoice PDF"
                                                            disabled={downloadingId === tx.id}
                                                            onClick={() => handleDownloadInvoice(tx.id)}
                                                        >
                                                            {downloadingId === tx.id ? (
                                                                <RefreshCw className="h-3.5 w-3.5 animate-spin" />
                                                            ) : (
                                                                <Download className="h-3.5 w-3.5" />
                                                            )}
                                                        </Button>
                                                        <Button
                                                            variant="ghost"
                                                            size="sm"
                                                            className="h-7 px-2"
                                                            title="Email invoice to driver"
                                                            disabled={sendingId === tx.id}
                                                            onClick={() => handleResendInvoice(tx.id)}
                                                        >
                                                            {sendingId === tx.id ? (
                                                                <RefreshCw className="h-3.5 w-3.5 animate-spin" />
                                                            ) : (
                                                                <Send className="h-3.5 w-3.5" />
                                                            )}
                                                        </Button>
                                                    </div>
                                                </TableCell>
                                            </TableRow>
                                        ))}
                                    </TableBody>
                                </Table>
                                {txTotal > TX_PAGE_SIZE && (
                                    <div className="mt-4 flex items-center justify-between text-sm text-muted-foreground">
                                        <span>
                                            Showing {(txPage - 1) * TX_PAGE_SIZE + 1}–
                                            {Math.min(txPage * TX_PAGE_SIZE, txTotal)} of {txTotal}
                                        </span>
                                        <div className="flex gap-2">
                                            <Button
                                                size="sm"
                                                variant="outline"
                                                disabled={txPage <= 1}
                                                onClick={() => setTxPage((p) => p - 1)}
                                            >
                                                <ChevronLeft className="h-4 w-4" />
                                            </Button>
                                            <Button
                                                size="sm"
                                                variant="outline"
                                                disabled={txPage * TX_PAGE_SIZE >= txTotal}
                                                onClick={() => setTxPage((p) => p + 1)}
                                            >
                                                <ChevronRight className="h-4 w-4" />
                                            </Button>
                                        </div>
                                    </div>
                                )}
                            </>
                        )}
                    </CardContent>
                </Card>
            )}

            {/* Tax Config Tab */}
            {activeTab === "tax" && (
                <Card>
                    <CardHeader className="pb-3">
                        <CardTitle className="flex items-center gap-2">
                            <Settings2 className="h-4 w-4" />
                            Subscription Tax Configuration
                        </CardTitle>
                        <p className="text-sm text-muted-foreground">
                            Set GST/PST/HST rates per service area. The plan price ($19.99) is pre-tax;
                            tax is added on top at checkout based on the driver&apos;s area.
                        </p>
                    </CardHeader>
                    <CardContent>
                        {serviceAreas.length === 0 && !loading ? (
                            <p className="text-sm text-muted-foreground py-8 text-center">No service areas found.</p>
                        ) : (
                            <Table>
                                <TableHeader>
                                    <TableRow>
                                        <SortableHead column="name" sort={areasSort} onSort={areasToggle}>Service Area</SortableHead>
                                        <SortableHead column="subscription_tax_config.province" sort={areasSort} onSort={areasToggle}>Province</SortableHead>
                                        <SortableHead column="subscription_tax_config.enabled" sort={areasSort} onSort={areasToggle}>Tax enabled</SortableHead>
                                        <SortableHead column="subscription_tax_config.gst_rate" sort={areasSort} onSort={areasToggle} align="right">GST</SortableHead>
                                        <SortableHead column="subscription_tax_config.pst_rate" sort={areasSort} onSort={areasToggle} align="right">PST</SortableHead>
                                        <SortableHead column="subscription_tax_config.hst_rate" sort={areasSort} onSort={areasToggle} align="right">HST</SortableHead>
                                        <TableHead className="text-right">Actions</TableHead>
                                    </TableRow>
                                </TableHeader>
                                <TableBody>
                                    {sortedAreas.map((area) => {
                                        const cfg = area.subscription_tax_config ?? {
                                            enabled: true,
                                            province: "SK",
                                            gst_rate: 5.0,
                                            pst_rate: 6.0,
                                            hst_rate: 0.0,
                                        };
                                        return (
                                            <TableRow key={area.id}>
                                                <TableCell className="font-medium">{area.name}</TableCell>
                                                <TableCell>{cfg.province}</TableCell>
                                                <TableCell>
                                                    <Badge variant={cfg.enabled ? "default" : "secondary"}>
                                                        {cfg.enabled ? "On" : "Off"}
                                                    </Badge>
                                                </TableCell>
                                                <TableCell className="text-right text-xs">
                                                    {cfg.gst_rate > 0 ? `${cfg.gst_rate}%` : "—"}
                                                </TableCell>
                                                <TableCell className="text-right text-xs">
                                                    {cfg.pst_rate > 0 ? `${cfg.pst_rate}%` : "—"}
                                                </TableCell>
                                                <TableCell className="text-right text-xs">
                                                    {cfg.hst_rate > 0 ? `${cfg.hst_rate}%` : "—"}
                                                </TableCell>
                                                <TableCell className="text-right">
                                                    <Button
                                                        size="sm"
                                                        variant="outline"
                                                        onClick={() => { setEditingArea(area); setTaxModalOpen(true); }}
                                                    >
                                                        <Pencil className="h-3 w-3 mr-1" />
                                                        Edit
                                                    </Button>
                                                </TableCell>
                                            </TableRow>
                                        );
                                    })}
                                </TableBody>
                            </Table>
                        )}
                    </CardContent>
                </Card>
            )}

            {/* Modals */}
            <PlanModal
                open={planModalOpen}
                plan={editingPlan}
                onClose={() => setPlanModalOpen(false)}
                onSave={handleSavePlan}
            />
            <DeleteModal
                open={deleteModalOpen}
                planName={deletingPlan?.name ?? ""}
                onClose={() => setDeleteModalOpen(false)}
                onConfirm={handleDeletePlan}
            />
            <TaxConfigModal
                open={taxModalOpen}
                area={editingArea}
                onClose={() => setTaxModalOpen(false)}
                onSave={handleSaveTaxConfig}
            />
        </div>
    );
}
