"use client";

import { useEffect, useState, useMemo, useCallback } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import {
    Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";
import {
    Dialog, DialogContent, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import {
    Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import {
    Ticket, Plus, Trash2, ToggleLeft, ToggleRight, Pencil, Search, Download,
    RefreshCw, Tag, Users, Calendar, Lock, Globe, ChevronLeft, ChevronRight,
    DollarSign, TrendingUp, BarChart3, X, Check, User,
} from "lucide-react";
import { formatDate, formatCurrency } from "@/lib/utils";
import { getPromotions, createPromotion, updatePromotion, deletePromotion, getPromoUsage, getPromoStats, getUsers } from "@/lib/api";
import { BarChart, Bar, LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from "recharts";

// --- Types ---

interface PromoCode {
    id: string;
    code: string;
    promo_type?: string;
    discount_type: "flat" | "percentage";
    discount_value: number;
    max_discount?: number;
    max_uses: number;
    max_uses_per_user: number;
    uses: number;
    expiry_date?: string;
    is_active: boolean;
    description?: string;
    min_ride_fare?: number;
    first_ride_only?: boolean;
    assigned_user_ids?: string[];
    created_at: string;
}

interface PromoUsageRecord {
    id: string;
    user_id: string;
    promo_id: string;
    code: string;
    discount_applied: number;
    created_at: string;
}

interface PromoStatsData {
    total_codes: number;
    active_codes: number;
    expired_codes: number;
    total_private: number;
    active_private: number;
    total_redemptions: number;
    total_discount_given: number;
    daily_usage: { date: string; count: number; amount: number }[];
}

interface UserOption {
    id: string;
    label: string;
    email?: string;
    phone?: string;
}

// --- Constants ---

const STATUS_CONFIG: Record<string, { label: string; color: string }> = {
    active: { label: "Active", color: "bg-emerald-500/15 text-emerald-600" },
    inactive: { label: "Inactive", color: "bg-zinc-500/15 text-zinc-600" },
    expired: { label: "Expired", color: "bg-red-500/15 text-red-600" },
};

const DATE_RANGES = [
    { key: "today", label: "Today" },
    { key: "yesterday", label: "Yesterday" },
    { key: "week", label: "This Week" },
    { key: "last_week", label: "Last Week" },
    { key: "month", label: "This Month" },
];

const PER_PAGE = 25;

function getPromoStatus(p: PromoCode): string {
    if (p.expiry_date && new Date(p.expiry_date) < new Date()) return "expired";
    return p.is_active ? "active" : "inactive";
}

// --- Component ---

export default function PromotionsPage() {
    const [promos, setPromos] = useState<PromoCode[]>([]);
    const [usage, setUsage] = useState<PromoUsageRecord[]>([]);
    const [stats, setStats] = useState<PromoStatsData | null>(null);
    const [loading, setLoading] = useState(true);
    const [usageLoading, setUsageLoading] = useState(false);
    const [search, setSearch] = useState("");
    const [statusFilter, setStatusFilter] = useState("all");
    const [dateRange, setDateRange] = useState("month");
    const [dateFrom, setDateFrom] = useState("");
    const [dateTo, setDateTo] = useState("");
    const [promoTab, setPromoTab] = useState<"public" | "private">("public");
    const [dialogOpen, setDialogOpen] = useState(false);
    const [editingPromo, setEditingPromo] = useState<PromoCode | null>(null);
    const [saving, setSaving] = useState(false);
    const [usagePage, setUsagePage] = useState(1);
    const [usageSearch, setUsageSearch] = useState("");

    // Multi-select for private coupon users
    const [userOptions, setUserOptions] = useState<UserOption[]>([]);
    const [selectedUsers, setSelectedUsers] = useState<UserOption[]>([]);
    const [userSearchText, setUserSearchText] = useState("");
    const [userSearchLoading, setUserSearchLoading] = useState(false);

    // Form
    const [form, setForm] = useState({
        code: "",
        discount_type: "flat" as "flat" | "percentage",
        discount_value: "",
        max_discount: "",
        max_uses: "100",
        max_uses_per_user: "1",
        expiry_date: "",
        description: "",
        min_ride_fare: "",
        first_ride_only: false,
        assigned_user_ids: [] as string[],
    });

    // --- Data fetching ---

    const fetchAll = async () => {
        setLoading(true);
        try {
            const [promosData, statsData] = await Promise.all([
                getPromotions().catch(() => []),
                getPromoStats(dateRange).catch(() => null),
            ]);
            setPromos(Array.isArray(promosData) ? promosData : []);
            setStats(statsData);
        } catch {
            setPromos([]);
        } finally {
            setLoading(false);
        }
    };

    const fetchUsage = async () => {
        setUsageLoading(true);
        try {
            const data = await getPromoUsage({ date_from: dateFrom || undefined, date_to: dateTo || undefined, limit: 500 });
            setUsage(Array.isArray(data) ? data : []);
        } catch {
            setUsage([]);
        } finally {
            setUsageLoading(false);
        }
    };

    useEffect(() => { fetchAll(); }, [dateRange]);
    useEffect(() => { fetchUsage(); }, [dateFrom, dateTo]);

    // User search for private coupons
    const fetchUserOptions = useCallback(async (query: string) => {
        setUserSearchLoading(true);
        try {
            const data = await getUsers();
            const opts: UserOption[] = (data || []).map((u: any) => ({
                id: u.id,
                label: `${u.first_name || ""} ${u.last_name || ""}`.trim() || u.email || u.phone || u.id,
                email: u.email,
                phone: u.phone,
            }));
            const q = query.toLowerCase();
            setUserOptions(q ? opts.filter((o) => o.label.toLowerCase().includes(q) || o.email?.toLowerCase().includes(q) || o.phone?.includes(q)) : opts.slice(0, 30));
        } catch {
            setUserOptions([]);
        } finally {
            setUserSearchLoading(false);
        }
    }, []);

    useEffect(() => {
        if (promoTab !== "private" || !dialogOpen) return;
        const timer = setTimeout(() => fetchUserOptions(userSearchText), 300);
        return () => clearTimeout(timer);
    }, [userSearchText, promoTab, dialogOpen, fetchUserOptions]);

    // --- Filtering ---

    const publicPromos = useMemo(() => promos.filter((p) => p.promo_type !== "private"), [promos]);
    const privatePromos = useMemo(() => promos.filter((p) => p.promo_type === "private"), [promos]);
    const activeList = promoTab === "public" ? publicPromos : privatePromos;

    const filtered = useMemo(() => {
        return activeList.filter((p) => {
            const matchSearch = !search || p.code?.toLowerCase().includes(search.toLowerCase()) || p.description?.toLowerCase().includes(search.toLowerCase());
            const status = getPromoStatus(p);
            const matchStatus = statusFilter === "all" || status === statusFilter;
            return matchSearch && matchStatus;
        });
    }, [activeList, search, statusFilter]);

    const filteredUsage = useMemo(() => {
        return usage.filter((u) => {
            if (!usageSearch) return true;
            const q = usageSearch.toLowerCase();
            return u.code?.toLowerCase().includes(q) || u.user_id?.toLowerCase().includes(q);
        });
    }, [usage, usageSearch]);

    const totalUsagePages = Math.max(1, Math.ceil(filteredUsage.length / PER_PAGE));
    const paginatedUsage = filteredUsage.slice((usagePage - 1) * PER_PAGE, usagePage * PER_PAGE);
    useEffect(() => { setUsagePage(1); }, [usageSearch]);

    // --- CRUD ---

    const resetForm = () => {
        setForm({ code: "", discount_type: "flat", discount_value: "", max_discount: "", max_uses: "100", max_uses_per_user: "1", expiry_date: "", description: "", min_ride_fare: "", first_ride_only: false, assigned_user_ids: [] });
        setEditingPromo(null);
        setSelectedUsers([]);
        setUserSearchText("");
    };

    const openCreate = () => { resetForm(); setDialogOpen(true); };

    const openEdit = (p: PromoCode) => {
        setEditingPromo(p);
        setForm({
            code: p.code, discount_type: p.discount_type, discount_value: String(p.discount_value),
            max_discount: p.max_discount ? String(p.max_discount) : "", max_uses: String(p.max_uses),
            max_uses_per_user: String(p.max_uses_per_user), expiry_date: p.expiry_date ? p.expiry_date.split("T")[0] : "",
            description: p.description || "", min_ride_fare: p.min_ride_fare ? String(p.min_ride_fare) : "",
            first_ride_only: p.first_ride_only || false, assigned_user_ids: p.assigned_user_ids || [],
        });
        setSelectedUsers((p.assigned_user_ids || []).map((id) => ({ id, label: id.slice(0, 8) + "..." })));
        setDialogOpen(true);
    };

    const handleSave = async () => {
        if (!form.code.trim() || !form.discount_value) { alert("Please fill in code and discount value."); return; }
        setSaving(true);
        try {
            const payload: any = {
                code: form.code.trim().toUpperCase(),
                promo_type: promoTab === "private" ? "private" : "discount",
                discount_type: form.discount_type,
                discount_value: parseFloat(form.discount_value),
                max_discount: form.max_discount ? parseFloat(form.max_discount) : null,
                max_uses: parseInt(form.max_uses),
                max_uses_per_user: parseInt(form.max_uses_per_user),
                expiry_date: form.expiry_date || null,
                description: form.description || null,
                min_ride_fare: form.min_ride_fare ? parseFloat(form.min_ride_fare) : 0,
                first_ride_only: form.first_ride_only,
            };
            if (promoTab === "private") payload.assigned_user_ids = form.assigned_user_ids;
            if (editingPromo) { await updatePromotion(editingPromo.id, payload); }
            else { await createPromotion(payload); }
            setDialogOpen(false);
            resetForm();
            await fetchAll();
        } catch (error: any) {
            alert(`Failed to save: ${error.message}`);
        } finally {
            setSaving(false);
        }
    };

    const toggleActive = async (p: PromoCode) => {
        try { await updatePromotion(p.id, { is_active: !p.is_active }); await fetchAll(); }
        catch (e: any) { alert(`Failed: ${e.message}`); }
    };

    const handleDelete = async (id: string) => {
        if (!confirm("Delete this promo code?")) return;
        try { await deletePromotion(id); await fetchAll(); }
        catch (e: any) { alert(`Failed: ${e.message}`); }
    };

    const toggleUserSelection = (opt: UserOption) => {
        if (form.assigned_user_ids.includes(opt.id)) {
            setForm((f) => ({ ...f, assigned_user_ids: f.assigned_user_ids.filter((id) => id !== opt.id) }));
            setSelectedUsers((prev) => prev.filter((u) => u.id !== opt.id));
        } else {
            setForm((f) => ({ ...f, assigned_user_ids: [...f.assigned_user_ids, opt.id] }));
            setSelectedUsers((prev) => [...prev, opt]);
        }
    };

    // --- Export ---

    const handleExportPromos = () => {
        const headers = ["Code", "Type", "Promo Type", "Value", "Max Discount", "Uses", "Max Uses", "Per User", "Expiry", "Status", "Description", "Created"];
        const rows = filtered.map((p) => [p.code, p.discount_type, p.promo_type || "discount", p.discount_value, p.max_discount || "", p.uses, p.max_uses, p.max_uses_per_user, p.expiry_date || "", getPromoStatus(p), `"${(p.description || "").replace(/"/g, '""')}"`, formatDate(p.created_at)]);
        const csv = [headers.join(","), ...rows.map((r) => r.join(","))].join("\n");
        const blob = new Blob([csv], { type: "text/csv" }); const url = URL.createObjectURL(blob);
        const a = document.createElement("a"); a.href = url; a.download = `promotions-${promoTab}-${new Date().toISOString().split("T")[0]}.csv`; a.click(); URL.revokeObjectURL(url);
    };

    const handleExportUsage = () => {
        const headers = ["Date", "User ID", "Code", "Discount Applied"];
        const rows = filteredUsage.map((u) => [formatDate(u.created_at), u.user_id, u.code, u.discount_applied]);
        const csv = [headers.join(","), ...rows.map((r) => r.join(","))].join("\n");
        const blob = new Blob([csv], { type: "text/csv" }); const url = URL.createObjectURL(blob);
        const a = document.createElement("a"); a.href = url; a.download = `promo-usage-${new Date().toISOString().split("T")[0]}.csv`; a.click(); URL.revokeObjectURL(url);
    };

    return (
        <div className="space-y-6">
            {/* Header */}
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-3xl font-bold tracking-tight flex items-center gap-2"><Ticket className="h-8 w-8 text-violet-500" /> Promotions</h1>
                    <p className="text-muted-foreground mt-1">Manage promo codes, private coupons, and track usage.</p>
                </div>
                <div className="flex gap-2">
                    <Button variant="outline" size="sm" onClick={() => { fetchAll(); fetchUsage(); }} disabled={loading}><RefreshCw className={`mr-2 h-4 w-4 ${loading ? "animate-spin" : ""}`} /> Refresh</Button>
                </div>
            </div>

            {/* Quick Date Selectors */}
            <div className="flex flex-wrap items-center gap-2">
                {DATE_RANGES.map((r) => (
                    <Button key={r.key} variant={dateRange === r.key ? "default" : "outline"} size="sm" onClick={() => setDateRange(r.key)} className={dateRange === r.key ? "bg-red-500 hover:bg-red-600 text-white" : ""}>
                        {r.label}
                    </Button>
                ))}
                <Separator orientation="vertical" className="h-6 mx-1" />
                <div className="flex items-center gap-2">
                    <Label className="text-xs text-muted-foreground">From</Label>
                    <Input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} className="w-36 h-8 text-xs" />
                </div>
                <div className="flex items-center gap-2">
                    <Label className="text-xs text-muted-foreground">To</Label>
                    <Input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} className="w-36 h-8 text-xs" />
                </div>
                {(dateFrom || dateTo) && <Button variant="ghost" size="sm" onClick={() => { setDateFrom(""); setDateTo(""); }}>Clear</Button>}
            </div>

            {/* Summary Stats */}
            {stats && (
                <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4">
                    {[
                        { label: "Total Codes", value: stats.total_codes, icon: Tag, color: "text-violet-500" },
                        { label: "Active", value: stats.active_codes, icon: ToggleRight, color: "text-emerald-500" },
                        { label: "Private Coupons", value: stats.total_private, icon: Lock, color: "text-blue-500" },
                        { label: "Redemptions", value: stats.total_redemptions, icon: Users, color: "text-amber-500" },
                        { label: "Discount Given", value: formatCurrency(stats.total_discount_given), icon: DollarSign, color: "text-red-500" },
                        { label: "Active Private", value: stats.active_private, icon: Lock, color: "text-emerald-500" },
                    ].map((s, i) => (
                        <Card key={i}><CardContent className="pt-4 pb-3"><div className="flex items-center gap-2"><s.icon className={`h-5 w-5 ${s.color}`} /><div><p className="text-xs text-muted-foreground">{s.label}</p><p className="text-2xl font-bold">{s.value}</p></div></div></CardContent></Card>
                    ))}
                </div>
            )}

            {/* Promo Tabs: Public vs Private */}
            <div className="flex gap-1 border-b">
                <button onClick={() => setPromoTab("public")} className={`flex items-center gap-2 px-4 py-2.5 text-sm font-medium border-b-2 -mb-px ${promoTab === "public" ? "border-red-500 text-red-600 dark:text-red-400" : "border-transparent text-muted-foreground hover:text-foreground"}`}>
                    <Globe className="h-4 w-4" /> Public Promo Codes ({publicPromos.length})
                </button>
                <button onClick={() => setPromoTab("private")} className={`flex items-center gap-2 px-4 py-2.5 text-sm font-medium border-b-2 -mb-px ${promoTab === "private" ? "border-red-500 text-red-600 dark:text-red-400" : "border-transparent text-muted-foreground hover:text-foreground"}`}>
                    <Lock className="h-4 w-4" /> Private Coupons ({privatePromos.length})
                </button>
            </div>

            {/* Promo Table */}
            <Card>
                <CardHeader className="pb-3">
                    <div className="flex items-center justify-between">
                        <CardTitle className="text-lg">{promoTab === "public" ? "Public Promo Codes" : "Private Coupons"}</CardTitle>
                        <div className="flex gap-2">
                            <Button variant="outline" size="sm" onClick={handleExportPromos} disabled={filtered.length === 0}><Download className="mr-2 h-4 w-4" /> Export</Button>
                            <Button size="sm" onClick={openCreate}><Plus className="mr-2 h-4 w-4" /> {promoTab === "public" ? "Create Code" : "Create Coupon"}</Button>
                        </div>
                    </div>
                </CardHeader>
                <CardContent className="space-y-4">
                    <div className="flex flex-wrap items-center gap-3">
                        <div className="relative flex-1 max-w-sm"><Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" /><Input placeholder="Search by code or description..." value={search} onChange={(e) => setSearch(e.target.value)} className="pl-9" /></div>
                        <Select value={statusFilter} onValueChange={setStatusFilter}><SelectTrigger className="w-36"><SelectValue placeholder="Status" /></SelectTrigger><SelectContent><SelectItem value="all">All Status</SelectItem><SelectItem value="active">Active</SelectItem><SelectItem value="inactive">Inactive</SelectItem><SelectItem value="expired">Expired</SelectItem></SelectContent></Select>
                    </div>

                    {loading ? (
                        <div className="flex items-center justify-center p-12"><div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" /></div>
                    ) : filtered.length === 0 ? (
                        <div className="text-center py-12"><Ticket className="h-12 w-12 text-muted-foreground/30 mx-auto mb-4" /><h3 className="text-lg font-semibold">No {promoTab === "public" ? "promo codes" : "coupons"} found</h3><p className="text-muted-foreground mt-1">Create one to get started.</p></div>
                    ) : (
                        <div className="border rounded-lg">
                            <Table>
                                <TableHeader><TableRow>
                                    <TableHead>Code</TableHead><TableHead>Discount</TableHead><TableHead>Uses</TableHead>
                                    {promoTab === "private" && <TableHead>Assigned To</TableHead>}
                                    <TableHead>Expiry</TableHead><TableHead>Status</TableHead><TableHead className="text-right">Actions</TableHead>
                                </TableRow></TableHeader>
                                <TableBody>
                                    {filtered.map((p) => {
                                        const status = getPromoStatus(p);
                                        const sc = STATUS_CONFIG[status] || STATUS_CONFIG.inactive;
                                        return (
                                            <TableRow key={p.id}>
                                                <TableCell><span className="font-mono font-semibold text-violet-600 dark:text-violet-400 bg-violet-500/10 px-2 py-0.5 rounded">{p.code}</span>{p.description && <p className="text-xs text-muted-foreground mt-1">{p.description}</p>}</TableCell>
                                                <TableCell className="text-sm">{p.discount_type === "flat" ? formatCurrency(p.discount_value) : `${p.discount_value}%`}{p.max_discount != null && <span className="text-xs text-muted-foreground ml-1">(max {formatCurrency(p.max_discount)})</span>}</TableCell>
                                                <TableCell className="text-sm">{p.uses}/{p.max_uses || "∞"}</TableCell>
                                                {promoTab === "private" && <TableCell className="text-sm">{(p.assigned_user_ids || []).length} user{(p.assigned_user_ids || []).length !== 1 ? "s" : ""}</TableCell>}
                                                <TableCell className="text-sm text-muted-foreground">{p.expiry_date ? formatDate(p.expiry_date) : "No expiry"}</TableCell>
                                                <TableCell><Badge className={sc.color}>{sc.label}</Badge></TableCell>
                                                <TableCell className="text-right"><div className="flex gap-1 justify-end">
                                                    <Button variant="ghost" size="icon" className="h-8 w-8" onClick={() => openEdit(p)}><Pencil className="h-4 w-4" /></Button>
                                                    <Button variant="ghost" size="icon" className="h-8 w-8" onClick={() => toggleActive(p)}>{p.is_active ? <ToggleRight className="h-4 w-4 text-emerald-500" /> : <ToggleLeft className="h-4 w-4 text-muted-foreground" />}</Button>
                                                    <Button variant="ghost" size="icon" className="h-8 w-8 text-destructive" onClick={() => handleDelete(p.id)}><Trash2 className="h-4 w-4" /></Button>
                                                </div></TableCell>
                                            </TableRow>
                                        );
                                    })}
                                </TableBody>
                            </Table>
                        </div>
                    )}
                </CardContent>
            </Card>

            {/* Charts */}
            {stats && stats.daily_usage && stats.daily_usage.length > 0 && (
                <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                    <Card>
                        <CardHeader className="pb-2"><CardTitle className="text-sm flex items-center gap-2"><BarChart3 className="h-4 w-4 text-blue-500" /> Daily Redemptions</CardTitle></CardHeader>
                        <CardContent>
                            <ResponsiveContainer width="100%" height={220}>
                                <BarChart data={stats.daily_usage}>
                                    <CartesianGrid strokeDasharray="3 3" className="stroke-border" />
                                    <XAxis dataKey="date" tick={{ fontSize: 10 }} tickFormatter={(d) => d.slice(5)} className="text-muted-foreground" />
                                    <YAxis tick={{ fontSize: 10 }} className="text-muted-foreground" />
                                    <Tooltip contentStyle={{ fontSize: 12, borderRadius: 8 }} />
                                    <Bar dataKey="count" fill="var(--chart-3)" radius={[3, 3, 0, 0]} name="Redemptions" />
                                </BarChart>
                            </ResponsiveContainer>
                        </CardContent>
                    </Card>
                    <Card>
                        <CardHeader className="pb-2"><CardTitle className="text-sm flex items-center gap-2"><TrendingUp className="h-4 w-4 text-emerald-500" /> Daily Discount Amount</CardTitle></CardHeader>
                        <CardContent>
                            <ResponsiveContainer width="100%" height={220}>
                                <LineChart data={stats.daily_usage}>
                                    <CartesianGrid strokeDasharray="3 3" className="stroke-border" />
                                    <XAxis dataKey="date" tick={{ fontSize: 10 }} tickFormatter={(d) => d.slice(5)} className="text-muted-foreground" />
                                    <YAxis tick={{ fontSize: 10 }} className="text-muted-foreground" />
                                    <Tooltip contentStyle={{ fontSize: 12, borderRadius: 8 }} formatter={(v) => [`$${Number(v).toFixed(2)}`, "Amount"]} />
                                    <Line dataKey="amount" stroke="var(--chart-2)" strokeWidth={2} dot={false} name="Amount ($)" />
                                </LineChart>
                            </ResponsiveContainer>
                        </CardContent>
                    </Card>
                </div>
            )}

            {/* Usage History */}
            <Card>
                <CardHeader className="pb-3">
                    <div className="flex items-center justify-between">
                        <CardTitle className="text-lg flex items-center gap-2"><Users className="h-5 w-5 text-amber-500" /> Usage History</CardTitle>
                        <Button variant="outline" size="sm" onClick={handleExportUsage} disabled={filteredUsage.length === 0}><Download className="mr-2 h-4 w-4" /> Export</Button>
                    </div>
                </CardHeader>
                <CardContent className="space-y-4">
                    <div className="relative max-w-sm"><Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" /><Input placeholder="Search by code or user ID..." value={usageSearch} onChange={(e) => setUsageSearch(e.target.value)} className="pl-9" /></div>

                    {usageLoading ? (
                        <div className="flex items-center justify-center p-12"><div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" /></div>
                    ) : filteredUsage.length === 0 ? (
                        <div className="text-center py-8 text-muted-foreground">No usage records found.</div>
                    ) : (
                        <div className="border rounded-lg">
                            <Table>
                                <TableHeader><TableRow>
                                    <TableHead>Date</TableHead><TableHead>User ID</TableHead><TableHead>Code</TableHead><TableHead>Discount Applied</TableHead>
                                </TableRow></TableHeader>
                                <TableBody>
                                    {paginatedUsage.map((u) => (
                                        <TableRow key={u.id}>
                                            <TableCell className="text-sm text-muted-foreground">{formatDate(u.created_at)}</TableCell>
                                            <TableCell className="text-sm font-mono">{u.user_id?.slice(0, 12)}...</TableCell>
                                            <TableCell><span className="font-mono font-semibold text-violet-600 dark:text-violet-400 bg-violet-500/10 px-2 py-0.5 rounded text-xs">{u.code}</span></TableCell>
                                            <TableCell className="text-sm font-medium text-emerald-600">{formatCurrency(u.discount_applied)}</TableCell>
                                        </TableRow>
                                    ))}
                                </TableBody>
                            </Table>
                            {filteredUsage.length > PER_PAGE && (
                                <div className="flex items-center justify-between px-4 py-3 border-t">
                                    <p className="text-sm text-muted-foreground">Showing {(usagePage - 1) * PER_PAGE + 1}–{Math.min(usagePage * PER_PAGE, filteredUsage.length)} of {filteredUsage.length}</p>
                                    <div className="flex items-center gap-2">
                                        <Button variant="outline" size="sm" onClick={() => setUsagePage((p) => Math.max(1, p - 1))} disabled={usagePage === 1}><ChevronLeft className="h-4 w-4" /></Button>
                                        <span className="text-sm text-muted-foreground">Page {usagePage} of {totalUsagePages}</span>
                                        <Button variant="outline" size="sm" onClick={() => setUsagePage((p) => Math.min(totalUsagePages, p + 1))} disabled={usagePage === totalUsagePages}><ChevronRight className="h-4 w-4" /></Button>
                                    </div>
                                </div>
                            )}
                        </div>
                    )}
                </CardContent>
            </Card>

            {/* Create/Edit Dialog */}
            <Dialog open={dialogOpen} onOpenChange={(open) => { if (!open) { setDialogOpen(false); resetForm(); } }}>
                <DialogContent className="sm:max-w-lg max-h-[90vh] overflow-y-auto">
                    <DialogHeader><DialogTitle className="flex items-center gap-2"><Ticket className="h-5 w-5 text-violet-500" /> {editingPromo ? "Edit" : "Create"} {promoTab === "public" ? "Promo Code" : "Private Coupon"}</DialogTitle></DialogHeader>
                    <div className="space-y-4">
                        <div className="grid grid-cols-2 gap-4">
                            <div className="space-y-2"><Label>Code</Label><Input placeholder="e.g. SAVE10" value={form.code} onChange={(e) => setForm({ ...form, code: e.target.value })} className="uppercase tracking-widest font-mono" /></div>
                            <div className="space-y-2"><Label>Type</Label><Select value={form.discount_type} onValueChange={(v) => setForm({ ...form, discount_type: v as any })}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="flat">Flat ($)</SelectItem><SelectItem value="percentage">Percentage (%)</SelectItem></SelectContent></Select></div>
                        </div>
                        <div className="grid grid-cols-2 gap-4">
                            <div className="space-y-2"><Label>Discount Value</Label><Input type="number" placeholder={form.discount_type === "flat" ? "5.00" : "10"} value={form.discount_value} onChange={(e) => setForm({ ...form, discount_value: e.target.value })} /></div>
                            {form.discount_type === "percentage" && <div className="space-y-2"><Label>Max Discount Cap ($)</Label><Input type="number" placeholder="25.00" value={form.max_discount} onChange={(e) => setForm({ ...form, max_discount: e.target.value })} /></div>}
                        </div>
                        <div className="grid grid-cols-3 gap-4">
                            <div className="space-y-2"><Label>Max Uses</Label><Input type="number" value={form.max_uses} onChange={(e) => setForm({ ...form, max_uses: e.target.value })} /></div>
                            <div className="space-y-2"><Label>Per User</Label><Input type="number" value={form.max_uses_per_user} onChange={(e) => setForm({ ...form, max_uses_per_user: e.target.value })} /></div>
                            <div className="space-y-2"><Label>Expiry Date</Label><Input type="date" value={form.expiry_date} onChange={(e) => setForm({ ...form, expiry_date: e.target.value })} /></div>
                        </div>
                        <div className="grid grid-cols-2 gap-4">
                            <div className="space-y-2"><Label>Min Ride Fare ($)</Label><Input type="number" placeholder="0" value={form.min_ride_fare} onChange={(e) => setForm({ ...form, min_ride_fare: e.target.value })} /></div>
                            <div className="flex items-center gap-2 pt-6"><Switch id="first-ride" checked={form.first_ride_only} onCheckedChange={(v) => setForm({ ...form, first_ride_only: v })} /><Label htmlFor="first-ride" className="cursor-pointer text-sm">First ride only</Label></div>
                        </div>
                        <div className="space-y-2"><Label>Description</Label><Input placeholder="Optional description" value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} /></div>

                        {/* Private coupon: assign users */}
                        {promoTab === "private" && (
                            <>
                                <Separator />
                                <div className="space-y-2">
                                    <Label className="text-sm font-semibold">Assign to Users</Label>
                                    {selectedUsers.length > 0 && (
                                        <div className="flex flex-wrap gap-1.5">
                                            {selectedUsers.map((u) => (
                                                <span key={u.id} className="inline-flex items-center gap-1 bg-violet-500/10 text-violet-700 dark:text-violet-300 rounded-full px-2.5 py-1 text-xs font-medium">
                                                    <User className="h-3 w-3" /> {u.label}
                                                    <button onClick={() => toggleUserSelection(u)} className="ml-0.5 hover:text-red-500"><X className="h-3 w-3" /></button>
                                                </span>
                                            ))}
                                        </div>
                                    )}
                                    <div className="relative"><Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" /><Input placeholder="Search users..." value={userSearchText} onChange={(e) => setUserSearchText(e.target.value)} className="pl-9" /></div>
                                    {userSearchLoading && <p className="text-xs text-muted-foreground">Searching...</p>}
                                    {!userSearchLoading && userOptions.length > 0 && (
                                        <div className="border rounded-md max-h-36 overflow-y-auto">
                                            {userOptions.map((opt) => {
                                                const isSel = form.assigned_user_ids.includes(opt.id);
                                                return (
                                                    <button key={opt.id} className={`w-full text-left px-3 py-1.5 text-sm border-b last:border-b-0 flex items-center justify-between ${isSel ? "bg-violet-500/5" : "hover:bg-muted/50"}`} onClick={() => toggleUserSelection(opt)}>
                                                        <div><p className="font-medium text-xs">{opt.label}</p><p className="text-[10px] text-muted-foreground">{[opt.email, opt.phone].filter(Boolean).join(" · ")}</p></div>
                                                        {isSel && <Check className="h-3 w-3 text-violet-500" />}
                                                    </button>
                                                );
                                            })}
                                        </div>
                                    )}
                                </div>
                            </>
                        )}

                        <Button className="w-full" onClick={handleSave} disabled={saving}>{saving ? "Saving..." : editingPromo ? "Update" : "Create"}</Button>
                    </div>
                </DialogContent>
            </Dialog>
        </div>
    );
}
