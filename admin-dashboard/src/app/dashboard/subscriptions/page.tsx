"use client";

import { useCallback, useEffect, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Separator } from "@/components/ui/separator";
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
import {
    CreditCard, Plus, Pencil, Trash2, RefreshCw, Users,
    DollarSign, TrendingUp, XCircle, CheckCircle, Clock,
} from "lucide-react";
import { formatCurrency, formatDate } from "@/lib/utils";
import {
    getSubscriptionPlans,
    createSubscriptionPlan,
    updateSubscriptionPlan,
    deleteSubscriptionPlan,
    getDriverSubscriptions,
    getSubscriptionStats,
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

const EMPTY_PLAN: Omit<SubscriptionPlan, "id" | "created_at"> = {
    name: "",
    price: 0,
    duration_days: 30,
    rides_per_day: -1,
    description: "",
    features: [],
    is_active: true,
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
                                placeholder="e.g. Pro Monthly"
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
                                    <SelectItem value="1">Daily (1 day)</SelectItem>
                                    <SelectItem value="7">Weekly (7 days)</SelectItem>
                                    <SelectItem value="30">Monthly (30 days)</SelectItem>
                                    <SelectItem value="90">Quarterly (90 days)</SelectItem>
                                    <SelectItem value="365">Annual (365 days)</SelectItem>
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
// Main Page
// ---------------------------------------------------------------------------

export default function SubscriptionsPage() {
    const [plans, setPlans] = useState<SubscriptionPlan[]>([]);
    const [driverSubs, setDriverSubs] = useState<DriverSubscription[]>([]);
    const [stats, setStats] = useState<SubStats | null>(null);
    const [loading, setLoading] = useState(true);
    const [statusFilter, setStatusFilter] = useState<string>("all");
    const [subPage, setSubPage] = useState(1);

    // Plan modal state
    const [planModalOpen, setPlanModalOpen] = useState(false);
    const [editingPlan, setEditingPlan] = useState<SubscriptionPlan | null>(null);

    // Delete modal state
    const [deleteModalOpen, setDeleteModalOpen] = useState(false);
    const [deletingPlan, setDeletingPlan] = useState<SubscriptionPlan | null>(null);

    const load = useCallback(async () => {
        setLoading(true);
        try {
            const [plansData, subsData, statsData] = await Promise.all([
                getSubscriptionPlans(),
                getDriverSubscriptions(statusFilter === "all" ? undefined : statusFilter),
                getSubscriptionStats(),
            ]);
            setPlans(plansData ?? []);
            setDriverSubs(subsData ?? []);
            setStats(statsData?.stats ?? null);
        } catch {
            // non-fatal — page still renders with empty state
        } finally {
            setLoading(false);
        }
    }, [statusFilter]);

    useEffect(() => { load(); }, [load]);
    useEffect(() => { setSubPage(1); }, [statusFilter]);

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

    // Filtered + paginated driver subs
    const filteredSubs = statusFilter === "all"
        ? driverSubs
        : driverSubs.filter((s) => s.status === statusFilter);
    const pagedSubs = filteredSubs.slice((subPage - 1) * PAGE_SIZE, subPage * PAGE_SIZE);

    return (
        <div className="space-y-6 p-6">
            {/* Header */}
            <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                    <CreditCard className="h-6 w-6 text-primary" />
                    <div>
                        <h1 className="text-2xl font-bold">Spinr Pass Subscriptions</h1>
                        <p className="text-sm text-muted-foreground">Manage driver subscription plans and active subscriptions</p>
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

            {/* Plans Section */}
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
                                    <TableHead>Name</TableHead>
                                    <TableHead>Price</TableHead>
                                    <TableHead>Duration</TableHead>
                                    <TableHead>Rides/Day</TableHead>
                                    <TableHead>Subscribers</TableHead>
                                    <TableHead>Status</TableHead>
                                    <TableHead className="text-right">Actions</TableHead>
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                {plans.map((plan) => (
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
                                                <Button
                                                    size="sm"
                                                    variant="outline"
                                                    onClick={() => openEdit(plan)}
                                                >
                                                    <Pencil className="h-3 w-3" />
                                                </Button>
                                                <Button
                                                    size="sm"
                                                    variant="outline"
                                                    onClick={() => openDelete(plan)}
                                                >
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

            <Separator />

            {/* Driver Subscriptions Section */}
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
                                        <TableHead>Driver</TableHead>
                                        <TableHead>Plan</TableHead>
                                        <TableHead>Price</TableHead>
                                        <TableHead>Status</TableHead>
                                        <TableHead>Started</TableHead>
                                        <TableHead>Expires</TableHead>
                                    </TableRow>
                                </TableHeader>
                                <TableBody>
                                    {pagedSubs.map((sub) => {
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
        </div>
    );
}
