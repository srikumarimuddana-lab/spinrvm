"use client";

import { useEffect, useState, useMemo } from "react";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from "@/components/ui/table";
import {
    Dialog,
    DialogContent,
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
    Ticket,
    Plus,
    Trash2,
    ToggleLeft,
    ToggleRight,
    Pencil,
    Search,
    Download,
    RefreshCw,
    Tag,
    Users,
    Calendar,
} from "lucide-react";
import { formatDate } from "@/lib/utils";
import { getPromotions, createPromotion, updatePromotion, deletePromotion } from "@/lib/api";

interface PromoCode {
    id: string;
    code: string;
    discount_type: "flat" | "percentage";
    discount_value: number;
    max_discount?: number;
    max_uses: number;
    max_uses_per_user: number;
    uses: number;
    expiry_date?: string;
    is_active: boolean;
    description?: string;
    created_at: string;
}

const STATUS_CONFIG: Record<string, { label: string; color: string }> = {
    active: { label: "Active", color: "bg-emerald-500/15 text-emerald-600" },
    inactive: { label: "Inactive", color: "bg-zinc-500/15 text-zinc-600" },
    expired: { label: "Expired", color: "bg-red-500/15 text-red-600" },
};

function getPromoStatus(p: PromoCode): string {
    if (p.expiry_date && new Date(p.expiry_date) < new Date()) return "expired";
    return p.is_active ? "active" : "inactive";
}

export default function PromotionsPage() {
    const [promos, setPromos] = useState<PromoCode[]>([]);
    const [loading, setLoading] = useState(true);
    const [search, setSearch] = useState("");
    const [statusFilter, setStatusFilter] = useState("all");
    const [dialogOpen, setDialogOpen] = useState(false);
    const [editingPromo, setEditingPromo] = useState<PromoCode | null>(null);
    const [saving, setSaving] = useState(false);

    // Form state
    const [form, setForm] = useState({
        code: "",
        discount_type: "flat" as "flat" | "percentage",
        discount_value: "",
        max_discount: "",
        max_uses: "100",
        max_uses_per_user: "1",
        expiry_date: "",
        description: "",
    });

    const fetchPromos = async () => {
        setLoading(true);
        try {
            const data = await getPromotions();
            setPromos(Array.isArray(data) ? data : []);
        } catch {
            console.error("Failed to fetch promos");
            setPromos([]);
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        fetchPromos();
    }, []);

    const filtered = useMemo(() => {
        return promos.filter((p) => {
            const matchSearch =
                !search ||
                p.code?.toLowerCase().includes(search.toLowerCase()) ||
                p.description?.toLowerCase().includes(search.toLowerCase());
            const status = getPromoStatus(p);
            const matchStatus = statusFilter === "all" || status === statusFilter;
            return matchSearch && matchStatus;
        });
    }, [promos, search, statusFilter]);

    const stats = useMemo(() => {
        const active = promos.filter((p) => getPromoStatus(p) === "active").length;
        const expired = promos.filter((p) => getPromoStatus(p) === "expired").length;
        const totalRedemptions = promos.reduce((s, p) => s + (p.uses || 0), 0);
        return { total: promos.length, active, expired, totalRedemptions };
    }, [promos]);

    const resetForm = () => {
        setForm({
            code: "",
            discount_type: "flat",
            discount_value: "",
            max_discount: "",
            max_uses: "100",
            max_uses_per_user: "1",
            expiry_date: "",
            description: "",
        });
        setEditingPromo(null);
    };

    const openCreate = () => {
        resetForm();
        setDialogOpen(true);
    };

    const openEdit = (promo: PromoCode) => {
        setEditingPromo(promo);
        setForm({
            code: promo.code,
            discount_type: promo.discount_type,
            discount_value: String(promo.discount_value),
            max_discount: promo.max_discount ? String(promo.max_discount) : "",
            max_uses: String(promo.max_uses),
            max_uses_per_user: String(promo.max_uses_per_user),
            expiry_date: promo.expiry_date ? promo.expiry_date.split("T")[0] : "",
            description: promo.description || "",
        });
        setDialogOpen(true);
    };

    const handleSave = async () => {
        if (!form.code.trim() || !form.discount_value) {
            alert("Please fill in code and discount value.");
            return;
        }
        setSaving(true);
        try {
            const payload = {
                code: form.code.trim().toUpperCase(),
                discount_type: form.discount_type,
                discount_value: parseFloat(form.discount_value),
                max_discount: form.max_discount ? parseFloat(form.max_discount) : null,
                max_uses: parseInt(form.max_uses),
                max_uses_per_user: parseInt(form.max_uses_per_user),
                expiry_date: form.expiry_date || null,
                description: form.description || null,
            };
            if (editingPromo) {
                await updatePromotion(editingPromo.id, payload);
            } else {
                await createPromotion(payload);
            }
            setDialogOpen(false);
            resetForm();
            await fetchPromos();
        } catch (error: any) {
            alert(`Failed to save: ${error.message}`);
        } finally {
            setSaving(false);
        }
    };

    const toggleActive = async (promo: PromoCode) => {
        try {
            await updatePromotion(promo.id, { is_active: !promo.is_active });
            await fetchPromos();
        } catch (error: any) {
            alert(`Failed to toggle: ${error.message}`);
        }
    };

    const handleDelete = async (id: string) => {
        if (!confirm("Delete this promo code?")) return;
        try {
            await deletePromotion(id);
            await fetchPromos();
        } catch (error: any) {
            alert(`Failed to delete: ${error.message}`);
        }
    };

    const handleExport = () => {
        const headers = ["Code", "Type", "Value", "Max Discount", "Uses", "Max Uses", "Per User", "Expiry", "Status", "Description", "Created"];
        const rows = filtered.map((p) => [
            p.code,
            p.discount_type,
            p.discount_value,
            p.max_discount || "",
            p.uses,
            p.max_uses,
            p.max_uses_per_user,
            p.expiry_date || "",
            getPromoStatus(p),
            `"${(p.description || "").replace(/"/g, '""')}"`,
            formatDate(p.created_at),
        ]);
        const csv = [headers.join(","), ...rows.map((r) => r.join(","))].join("\n");
        const blob = new Blob([csv], { type: "text/csv" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `promotions-${new Date().toISOString().split("T")[0]}.csv`;
        a.click();
        URL.revokeObjectURL(url);
    };

    return (
        <div className="space-y-6">
            {/* Header */}
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-3xl font-bold tracking-tight flex items-center gap-2">
                        <Ticket className="h-8 w-8 text-violet-500" />
                        Promotions
                    </h1>
                    <p className="text-muted-foreground mt-1">
                        Manage promo codes and discounts.
                    </p>
                </div>
                <div className="flex gap-2">
                    <Button variant="outline" size="sm" onClick={fetchPromos} disabled={loading}>
                        <RefreshCw className={`mr-2 h-4 w-4 ${loading ? "animate-spin" : ""}`} /> Refresh
                    </Button>
                    <Button variant="outline" size="sm" onClick={handleExport} disabled={filtered.length === 0}>
                        <Download className="mr-2 h-4 w-4" /> Export
                    </Button>
                    <Button onClick={openCreate}>
                        <Plus className="mr-2 h-4 w-4" /> Create Promo Code
                    </Button>
                </div>
            </div>

            {/* Stats */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                <Card>
                    <CardContent className="pt-4 pb-3">
                        <div className="flex items-center gap-2">
                            <Tag className="h-5 w-5 text-violet-500" />
                            <div>
                                <p className="text-xs text-muted-foreground">Total Promos</p>
                                <p className="text-2xl font-bold">{stats.total}</p>
                            </div>
                        </div>
                    </CardContent>
                </Card>
                <Card>
                    <CardContent className="pt-4 pb-3">
                        <div className="flex items-center gap-2">
                            <ToggleRight className="h-5 w-5 text-emerald-500" />
                            <div>
                                <p className="text-xs text-muted-foreground">Active</p>
                                <p className="text-2xl font-bold">{stats.active}</p>
                            </div>
                        </div>
                    </CardContent>
                </Card>
                <Card>
                    <CardContent className="pt-4 pb-3">
                        <div className="flex items-center gap-2">
                            <Calendar className="h-5 w-5 text-red-500" />
                            <div>
                                <p className="text-xs text-muted-foreground">Expired</p>
                                <p className="text-2xl font-bold">{stats.expired}</p>
                            </div>
                        </div>
                    </CardContent>
                </Card>
                <Card>
                    <CardContent className="pt-4 pb-3">
                        <div className="flex items-center gap-2">
                            <Users className="h-5 w-5 text-amber-500" />
                            <div>
                                <p className="text-xs text-muted-foreground">Total Redemptions</p>
                                <p className="text-2xl font-bold">{stats.totalRedemptions}</p>
                            </div>
                        </div>
                    </CardContent>
                </Card>
            </div>

            {/* Filters */}
            <div className="flex flex-wrap items-center gap-3">
                <div className="relative flex-1 max-w-sm">
                    <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                    <Input
                        placeholder="Search by code or description..."
                        value={search}
                        onChange={(e) => setSearch(e.target.value)}
                        className="pl-9"
                    />
                </div>
                <Select value={statusFilter} onValueChange={setStatusFilter}>
                    <SelectTrigger className="w-40">
                        <SelectValue placeholder="Filter by status" />
                    </SelectTrigger>
                    <SelectContent>
                        <SelectItem value="all">All Status</SelectItem>
                        <SelectItem value="active">Active</SelectItem>
                        <SelectItem value="inactive">Inactive</SelectItem>
                        <SelectItem value="expired">Expired</SelectItem>
                    </SelectContent>
                </Select>
            </div>

            {/* Table */}
            <Card className="border-border/50">
                <CardContent className="p-0">
                    {loading ? (
                        <div className="flex items-center justify-center p-12">
                            <div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" />
                        </div>
                    ) : filtered.length === 0 ? (
                        <div className="text-center py-16">
                            <Ticket className="h-12 w-12 text-muted-foreground/30 mx-auto mb-4" />
                            <h3 className="text-lg font-semibold">No promo codes found</h3>
                            <p className="text-muted-foreground mt-1">Create a promo code to get started.</p>
                        </div>
                    ) : (
                        <Table>
                            <TableHeader>
                                <TableRow>
                                    <TableHead>Code</TableHead>
                                    <TableHead>Discount</TableHead>
                                    <TableHead>Uses</TableHead>
                                    <TableHead>Expiry</TableHead>
                                    <TableHead>Status</TableHead>
                                    <TableHead className="text-right">Actions</TableHead>
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                {filtered.map((p) => {
                                    const status = getPromoStatus(p);
                                    const statusCfg = STATUS_CONFIG[status] || STATUS_CONFIG.inactive;
                                    return (
                                        <TableRow key={p.id}>
                                            <TableCell>
                                                <div>
                                                    <span className="font-mono font-semibold text-violet-600 dark:text-violet-400 bg-violet-500/10 px-2 py-0.5 rounded">
                                                        {p.code}
                                                    </span>
                                                    {p.description && (
                                                        <p className="text-xs text-muted-foreground mt-1">{p.description}</p>
                                                    )}
                                                </div>
                                            </TableCell>
                                            <TableCell className="text-sm">
                                                {p.discount_type === "flat" ? `$${p.discount_value.toFixed(2)}` : `${p.discount_value}%`}
                                                {p.max_discount != null && (
                                                    <span className="text-xs text-muted-foreground ml-1">(max ${p.max_discount})</span>
                                                )}
                                            </TableCell>
                                            <TableCell className="text-sm">
                                                {p.uses}/{p.max_uses}
                                            </TableCell>
                                            <TableCell className="text-sm text-muted-foreground">
                                                {p.expiry_date ? formatDate(p.expiry_date) : "No expiry"}
                                            </TableCell>
                                            <TableCell>
                                                <Badge className={statusCfg.color}>
                                                    {statusCfg.label}
                                                </Badge>
                                            </TableCell>
                                            <TableCell className="text-right">
                                                <div className="flex gap-1 justify-end">
                                                    <Button
                                                        variant="ghost"
                                                        size="icon"
                                                        className="h-8 w-8"
                                                        onClick={() => openEdit(p)}
                                                        title="Edit"
                                                    >
                                                        <Pencil className="h-4 w-4" />
                                                    </Button>
                                                    <Button
                                                        variant="ghost"
                                                        size="icon"
                                                        className="h-8 w-8"
                                                        onClick={() => toggleActive(p)}
                                                        title={p.is_active ? "Deactivate" : "Activate"}
                                                    >
                                                        {p.is_active ? (
                                                            <ToggleRight className="h-4 w-4 text-emerald-500" />
                                                        ) : (
                                                            <ToggleLeft className="h-4 w-4 text-muted-foreground" />
                                                        )}
                                                    </Button>
                                                    <Button
                                                        variant="ghost"
                                                        size="icon"
                                                        className="h-8 w-8 text-destructive"
                                                        onClick={() => handleDelete(p.id)}
                                                        title="Delete"
                                                    >
                                                        <Trash2 className="h-4 w-4" />
                                                    </Button>
                                                </div>
                                            </TableCell>
                                        </TableRow>
                                    );
                                })}
                            </TableBody>
                        </Table>
                    )}
                </CardContent>
            </Card>

            {/* Create/Edit Dialog */}
            <Dialog open={dialogOpen} onOpenChange={(open) => { if (!open) { setDialogOpen(false); resetForm(); } }}>
                <DialogContent className="sm:max-w-lg">
                    <DialogHeader>
                        <DialogTitle className="flex items-center gap-2">
                            <Ticket className="h-5 w-5 text-violet-500" />
                            {editingPromo ? "Edit Promo Code" : "Create Promo Code"}
                        </DialogTitle>
                    </DialogHeader>
                    <div className="space-y-4">
                        <div className="grid grid-cols-2 gap-4">
                            <div className="space-y-2">
                                <Label>Code</Label>
                                <Input
                                    placeholder="e.g. SAVE10"
                                    value={form.code}
                                    onChange={(e) => setForm({ ...form, code: e.target.value })}
                                    className="uppercase tracking-widest font-mono"
                                />
                            </div>
                            <div className="space-y-2">
                                <Label>Type</Label>
                                <Select value={form.discount_type} onValueChange={(v) => setForm({ ...form, discount_type: v as "flat" | "percentage" })}>
                                    <SelectTrigger><SelectValue /></SelectTrigger>
                                    <SelectContent>
                                        <SelectItem value="flat">Flat ($)</SelectItem>
                                        <SelectItem value="percentage">Percentage (%)</SelectItem>
                                    </SelectContent>
                                </Select>
                            </div>
                        </div>
                        <div className="grid grid-cols-2 gap-4">
                            <div className="space-y-2">
                                <Label>Discount Value</Label>
                                <Input
                                    type="number"
                                    placeholder={form.discount_type === "flat" ? "5.00" : "10"}
                                    value={form.discount_value}
                                    onChange={(e) => setForm({ ...form, discount_value: e.target.value })}
                                />
                            </div>
                            {form.discount_type === "percentage" && (
                                <div className="space-y-2">
                                    <Label>Max Discount Cap ($)</Label>
                                    <Input
                                        type="number"
                                        placeholder="25.00"
                                        value={form.max_discount}
                                        onChange={(e) => setForm({ ...form, max_discount: e.target.value })}
                                    />
                                </div>
                            )}
                        </div>
                        <div className="grid grid-cols-3 gap-4">
                            <div className="space-y-2">
                                <Label>Max Uses</Label>
                                <Input
                                    type="number"
                                    value={form.max_uses}
                                    onChange={(e) => setForm({ ...form, max_uses: e.target.value })}
                                />
                            </div>
                            <div className="space-y-2">
                                <Label>Per User Limit</Label>
                                <Input
                                    type="number"
                                    value={form.max_uses_per_user}
                                    onChange={(e) => setForm({ ...form, max_uses_per_user: e.target.value })}
                                />
                            </div>
                            <div className="space-y-2">
                                <Label>Expiry Date</Label>
                                <Input
                                    type="date"
                                    value={form.expiry_date}
                                    onChange={(e) => setForm({ ...form, expiry_date: e.target.value })}
                                />
                            </div>
                        </div>
                        <div className="space-y-2">
                            <Label>Description</Label>
                            <Input
                                placeholder="Optional description"
                                value={form.description}
                                onChange={(e) => setForm({ ...form, description: e.target.value })}
                            />
                        </div>
                        <Button className="w-full" onClick={handleSave} disabled={saving}>
                            {saving ? "Saving..." : editingPromo ? "Update Promo Code" : "Create Promo Code"}
                        </Button>
                    </div>
                </DialogContent>
            </Dialog>
        </div>
    );
}
