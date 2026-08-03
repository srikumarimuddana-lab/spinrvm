"use client";

import { useEffect, useRef, useState, useMemo, useCallback } from "react";
import { useToast } from "@/components/ui/use-toast";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import {
    Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";
import { useTableSort, SortableHead } from "@/components/ui/sortable-table";
import {
    Dialog, DialogContent, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import {
    AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent,
    AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import {
    Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { Pagination } from "@/components/ui/pagination";
import {
    Ticket, Plus, Trash2, ToggleLeft, ToggleRight, Pencil, Search, Download,
    RefreshCw, Tag, Users, Lock, Globe,
    DollarSign, TrendingUp, BarChart3, X, Check, User, Clock,
} from "lucide-react";
import { formatDate, formatCurrency } from "@/lib/utils";
import { getPromotions, createPromotion, updatePromotion, deletePromotion, getPromoUsage, getPromoStats, getUsers, getServiceAreas } from "@/lib/api";
import { BarChart, Bar, LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from "recharts";
import { useRequireModule } from "@/hooks/useRequireModule";
import RideDetailModal from "../rides/_components/ride-detail-modal";

// --- Types ---

interface PromoCode {
    id: string;
    code: string;
    promo_type?: string;
    free_ride?: boolean;
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
    service_area_id?: string | null;
    created_at: string;
}

interface ServiceArea {
    id: string;
    name: string;
}

interface PromoUsageRecord {
    id: string;
    user_id: string;
    user_name?: string;
    ride_id?: string | null;
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
    daily_usage: {
        date: string;
        count: number;
        amount: number;
        public_count?: number;
        public_amount?: number;
        private_count?: number;
        private_amount?: number;
    }[];
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

const PAGE_SIZE = 25;

function getPromoStatus(p: PromoCode): string {
    if (p.expiry_date && new Date(p.expiry_date) < new Date()) return "expired";
    return p.is_active ? "active" : "inactive";
}

// --- Component ---

export default function PromotionsPage() {
    const { allowed } = useRequireModule("promotions");
    const { toast } = useToast();
    const [deleteTarget, setDeleteTarget] = useState<string | null>(null);
    const [selectedRideId, setSelectedRideId] = useState<string | null>(null);
    const [promos, setPromos] = useState<PromoCode[]>([]);
    const [usage, setUsage] = useState<PromoUsageRecord[]>([]);
    const [stats, setStats] = useState<PromoStatsData | null>(null);
    const [loading, setLoading] = useState(true);
    const [usageLoading, setUsageLoading] = useState(false);
    const [search, setSearch] = useState("");
    const [promoTab, setPromoTab] = useState<"public" | "private" | "expired">("public");
    const [dialogOpen, setDialogOpen] = useState(false);
    const [editingPromo, setEditingPromo] = useState<PromoCode | null>(null);
    const [saving, setSaving] = useState(false);

    // Promo table pagination (server-side)
    const [page, setPage] = useState(0);
    const [hasNextPage, setHasNextPage] = useState(false);
    const promosReqIdRef = useRef(0);

    // Usage History filters
    const [usagePage, setUsagePage] = useState(0);
    const [usageHasNext, setUsageHasNext] = useState(false);
    const usageReqIdRef = useRef(0);
    const [usageSearch, setUsageSearch] = useState("");
    const [usageDateRange, setUsageDateRange] = useState("month");
    const [usageDateFrom, setUsageDateFrom] = useState("");
    const [usageDateTo, setUsageDateTo] = useState("");
    const [usageTypeFilter, setUsageTypeFilter] = useState("all"); // all, public, private

    // Charts filter
    const [chartFilter, setChartFilter] = useState("all"); // all, public, private

    // Multi-select for private coupon users
    const [userOptions, setUserOptions] = useState<UserOption[]>([]);
    const [selectedUsers, setSelectedUsers] = useState<UserOption[]>([]);
    const [userSearchText, setUserSearchText] = useState("");
    const [userSearchLoading, setUserSearchLoading] = useState(false);

    // Service areas for promo scoping
    const [serviceAreas, setServiceAreas] = useState<ServiceArea[]>([]);

    // Form
    const [form, setForm] = useState({
        code: "",
        free_ride: false,
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
        service_area_id: "",
    });

    // --- Data fetching ---

    const fetchStats = async () => {
        try {
            const statsData = await getPromoStats(usageDateRange);
            setStats(statsData ?? null);
        } catch {
            setStats(null);
        }
    };

    const fetchPromos = async () => {
        setLoading(true);
        const reqId = ++promosReqIdRef.current;
        try {
            const data = await getPromotions({
                limit: PAGE_SIZE + 1,
                offset: page * PAGE_SIZE,
                promo_type: promoTab === "expired" ? undefined : promoTab,
                status:
                    promoTab === "expired"
                        ? "expired"
                        : "not_expired",
                search: search.trim() || undefined,
            });
            if (reqId !== promosReqIdRef.current) return;
            const arr = Array.isArray(data) ? data : [];
            setHasNextPage(arr.length > PAGE_SIZE);
            setPromos(arr.slice(0, PAGE_SIZE));
        } catch {
            if (reqId !== promosReqIdRef.current) return;
            setPromos([]);
            setHasNextPage(false);
        } finally {
            if (reqId === promosReqIdRef.current) setLoading(false);
        }
    };

    const fetchUsage = async () => {
        setUsageLoading(true);
        const reqId = ++usageReqIdRef.current;
        try {
            const data = await getPromoUsage({
                date_from: usageDateFrom || undefined,
                date_to: usageDateTo || undefined,
                limit: PAGE_SIZE + 1,
                offset: usagePage * PAGE_SIZE,
            });
            if (reqId !== usageReqIdRef.current) return;
            const arr = Array.isArray(data) ? data : [];
            setUsageHasNext(arr.length > PAGE_SIZE);
            setUsage(arr.slice(0, PAGE_SIZE));
        } catch {
            if (reqId !== usageReqIdRef.current) return;
            setUsage([]);
            setUsageHasNext(false);
        } finally {
            if (reqId === usageReqIdRef.current) setUsageLoading(false);
        }
    };

    const fetchAll = async () => {
        await Promise.all([fetchStats(), fetchPromos(), fetchUsage()]);
    };

    // Load service areas once on mount for the promo scoping dropdown
    useEffect(() => {
        getServiceAreas().then((areas) => setServiceAreas(Array.isArray(areas) ? areas : [])).catch(() => {});
    }, []);

    // Re-fetch stats when range changes
    useEffect(() => { fetchStats(); }, [usageDateRange]);

    // Reset to page 0 when tab/search changes (debounce search)
    useEffect(() => {
        const t = setTimeout(() => {
            if (page !== 0) setPage(0);
            else fetchPromos();
        }, 300);
        return () => clearTimeout(t);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [promoTab, search]);

    // Re-fetch promos when page changes
    useEffect(() => {
        fetchPromos();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [page]);

    // Reset usage page when filters change
    useEffect(() => {
        if (usagePage !== 0) setUsagePage(0);
        else fetchUsage();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [usageDateFrom, usageDateTo]);

    // Re-fetch usage when its page changes
    useEffect(() => {
        fetchUsage();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [usagePage]);

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

    // Server returns the already-filtered slice for the active tab.
    // Keep `filtered` as an alias so the existing render code below works unchanged.
    const filtered = promos;

    // Tab counts come from the stats endpoint (accurate across pages).
    const publicCount = stats ? Math.max(0, (stats.total_codes || 0)) : 0;
    const privateCount = stats?.total_private ?? 0;
    const expiredCount = stats?.expired_codes ?? 0;

    // Usage list is server-paginated. Search/type filters apply only to the loaded page.
    const promoIdTypeMap = useMemo(() => {
        const map: Record<string, string> = {};
        promos.forEach((p) => { map[p.id] = p.promo_type || "discount"; });
        return map;
    }, [promos]);

    const filteredUsage = useMemo(() => {
        return usage.filter((u) => {
            if (usageSearch) {
                const q = usageSearch.toLowerCase();
                if (!u.code?.toLowerCase().includes(q) && !u.user_id?.toLowerCase().includes(q)) return false;
            }
            if (usageTypeFilter !== "all") {
                const pType = promoIdTypeMap[u.promo_id] || "discount";
                if (usageTypeFilter === "private" && pType !== "private") return false;
                if (usageTypeFilter === "public" && pType === "private") return false;
            }
            return true;
        });
    }, [usage, usageSearch, usageTypeFilter, promoIdTypeMap]);

    // Client-side sorting for the loaded page of each table.
    const { sorted: sortedPromos, sort: promoSort, toggle: togglePromoSort } = useTableSort(filtered);
    const { sorted: sortedUsage, sort: usageSort, toggle: toggleUsageSort } = useTableSort(filteredUsage);

    // Chart data filtered by chartFilter — the backend now splits each
    // day's usage into public_count/public_amount and
    // private_count/private_amount alongside the combined count/amount.
    const chartData = useMemo(() => {
        if (!stats?.daily_usage) return [];
        if (chartFilter === "public") {
            return stats.daily_usage.map((d) => ({
                date: d.date,
                count: d.public_count ?? 0,
                amount: d.public_amount ?? 0,
            }));
        }
        if (chartFilter === "private") {
            return stats.daily_usage.map((d) => ({
                date: d.date,
                count: d.private_count ?? 0,
                amount: d.private_amount ?? 0,
            }));
        }
        return stats.daily_usage;
    }, [stats, chartFilter]);

    // --- CRUD ---

    const resetForm = () => {
        setForm({ code: "", free_ride: false, discount_type: "flat", discount_value: "", max_discount: "", max_uses: "100", max_uses_per_user: "1", expiry_date: "", description: "", min_ride_fare: "", first_ride_only: false, assigned_user_ids: [], service_area_id: "" });
        setEditingPromo(null);
        setSelectedUsers([]);
        setUserSearchText("");
    };

    const openCreate = () => { resetForm(); setDialogOpen(true); };

    const openEdit = (p: PromoCode) => {
        setEditingPromo(p);
        setForm({
            code: p.code, free_ride: p.free_ride || false, discount_type: p.discount_type,
            discount_value: String(p.discount_value),
            max_discount: p.max_discount ? String(p.max_discount) : "", max_uses: String(p.max_uses),
            max_uses_per_user: String(p.max_uses_per_user), expiry_date: p.expiry_date ? p.expiry_date.split("T")[0] : "",
            description: p.description || "", min_ride_fare: p.min_ride_fare ? String(p.min_ride_fare) : "",
            first_ride_only: p.first_ride_only || false, assigned_user_ids: p.assigned_user_ids || [],
            service_area_id: p.service_area_id || "",
        });
        setSelectedUsers((p.assigned_user_ids || []).map((id) => ({ id, label: id.slice(0, 8) + "..." })));
        setDialogOpen(true);
    };

    const handleSave = async () => {
        if (!form.code.trim()) {
            toast({ title: "Missing required fields", description: "Please fill in the code.", variant: "destructive" });
            return;
        }
        if (!form.free_ride) {
            if (!form.discount_value) {
                toast({ title: "Missing required fields", description: "Please fill in code and discount value.", variant: "destructive" });
                return;
            }
            const discountVal = parseFloat(form.discount_value);
            if (isNaN(discountVal) || discountVal <= 0) {
                toast({ title: "Invalid discount value", description: "Discount must be greater than zero.", variant: "destructive" });
                return;
            }
            if (form.discount_type === "percentage" && discountVal > 100) {
                toast({ title: "Invalid percentage", description: "Percentage discount cannot exceed 100%.", variant: "destructive" });
                return;
            }
            if (form.discount_type === "flat" && discountVal > 500) {
                toast({ title: "Discount too large", description: "Flat discount cannot exceed $500.", variant: "destructive" });
                return;
            }
            if (form.max_discount) {
                const maxD = parseFloat(form.max_discount);
                if (isNaN(maxD) || maxD <= 0) {
                    toast({ title: "Invalid max discount cap", description: "Max discount cap must be greater than zero.", variant: "destructive" });
                    return;
                }
                if (maxD > 500) {
                    toast({ title: "Max discount cap too large", description: "Max discount cap cannot exceed $500.", variant: "destructive" });
                    return;
                }
            }
        }
        const maxUses = parseInt(form.max_uses);
        if (isNaN(maxUses) || maxUses < 1) {
            toast({ title: "Invalid max uses", description: "Max uses must be at least 1.", variant: "destructive" });
            return;
        }
        if (form.expiry_date) {
            const expiry = new Date(form.expiry_date);
            if (expiry <= new Date()) {
                toast({ title: "Invalid expiry date", description: "Expiry date must be in the future.", variant: "destructive" });
                return;
            }
        }
        setSaving(true);
        try {
            const isPrivateTab = promoTab === "private" || (editingPromo?.promo_type === "private");
            const payload: any = {
                code: form.code.trim().toUpperCase(),
                promo_type: isPrivateTab ? "private" : "discount",
                free_ride: form.free_ride,
                discount_type: form.discount_type,
                discount_value: form.free_ride ? 0 : parseFloat(form.discount_value),
                max_discount: form.max_discount ? parseFloat(form.max_discount) : null,
                max_uses: parseInt(form.max_uses),
                max_uses_per_user: parseInt(form.max_uses_per_user),
                expiry_date: form.expiry_date || null,
                description: form.description || "",
                min_ride_fare: form.min_ride_fare ? parseFloat(form.min_ride_fare) : 0,
                first_ride_only: form.first_ride_only,
                service_area_id: form.service_area_id || null,
            };
            if (isPrivateTab) payload.assigned_user_ids = form.assigned_user_ids;
            if (editingPromo) { await updatePromotion(editingPromo.id, payload); }
            else { await createPromotion(payload); }
            toast({ title: editingPromo ? "Promo updated" : "Promo created" });
            setDialogOpen(false);
            resetForm();
            await fetchAll();
        } catch (error: any) {
            toast({ title: "Failed to save", description: error.message, variant: "destructive" });
        } finally {
            setSaving(false);
        }
    };

    const toggleActive = async (p: PromoCode) => {
        try {
            await updatePromotion(p.id, { is_active: !p.is_active });
            toast({ title: `Promo ${!p.is_active ? 'activated' : 'deactivated'}` });
            await fetchAll();
        }
        catch (e: any) { toast({ title: "Failed to toggle promo", description: e.message, variant: "destructive" }); }
    };

    const handleDelete = async (id: string) => {
        setDeleteTarget(id);
    };

    const confirmDelete = async () => {
        if (!deleteTarget) return;
        try {
            await deletePromotion(deleteTarget);
            toast({ title: "Promo deleted" });
            await fetchAll();
        }
        catch (e: any) { toast({ title: "Failed to delete promo", description: e.message, variant: "destructive" }); }
        finally { setDeleteTarget(null); }
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

    if (!allowed) return null;

    return (
        <div className="space-y-6">
            {/* Header */}
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-3xl font-bold tracking-tight flex items-center gap-2"><Ticket className="h-8 w-8 text-violet-500" /> Promotions</h1>
                    <p className="text-muted-foreground mt-1">Manage promo codes, private coupons, and track usage.</p>
                </div>
                <Button variant="outline" size="sm" onClick={() => { fetchAll(); fetchUsage(); }} disabled={loading}>
                    <RefreshCw className={`mr-2 h-4 w-4 ${loading ? "animate-spin" : ""}`} /> Refresh
                </Button>
            </div>

            {/* Summary Stats */}
            {stats && (
                <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4">
                    {[
                        { label: "Total Codes", value: stats.total_codes, icon: Tag, color: "text-violet-500" },
                        { label: "Active", value: stats.active_codes, icon: ToggleRight, color: "text-emerald-500" },
                        { label: "Expired", value: stats.expired_codes, icon: Clock, color: "text-red-500" },
                        { label: "Private Coupons", value: stats.total_private, icon: Lock, color: "text-blue-500" },
                        { label: "Redemptions", value: stats.total_redemptions, icon: Users, color: "text-amber-500" },
                        { label: "Discount Given", value: formatCurrency(stats.total_discount_given), icon: DollarSign, color: "text-red-500" },
                    ].map((s, i) => (
                        <Card key={i}><CardContent className="pt-4 pb-3"><div className="flex items-center gap-2"><s.icon className={`h-5 w-5 ${s.color}`} /><div><p className="text-xs text-muted-foreground">{s.label}</p><p className="text-2xl font-bold">{s.value}</p></div></div></CardContent></Card>
                    ))}
                </div>
            )}

            {/* Promo Tabs: Public | Private | Expired */}
            <div className="flex gap-1 border-b">
                <button onClick={() => setPromoTab("public")} className={`flex items-center gap-2 px-4 py-2.5 text-sm font-medium border-b-2 -mb-px ${promoTab === "public" ? "border-red-500 text-red-600 dark:text-red-400" : "border-transparent text-muted-foreground hover:text-foreground"}`}>
                    <Globe className="h-4 w-4" /> Public Codes ({publicCount})
                </button>
                <button onClick={() => setPromoTab("private")} className={`flex items-center gap-2 px-4 py-2.5 text-sm font-medium border-b-2 -mb-px ${promoTab === "private" ? "border-red-500 text-red-600 dark:text-red-400" : "border-transparent text-muted-foreground hover:text-foreground"}`}>
                    <Lock className="h-4 w-4" /> Private Coupons ({privateCount})
                </button>
                <button onClick={() => setPromoTab("expired")} className={`flex items-center gap-2 px-4 py-2.5 text-sm font-medium border-b-2 -mb-px ${promoTab === "expired" ? "border-red-500 text-red-600 dark:text-red-400" : "border-transparent text-muted-foreground hover:text-foreground"}`}>
                    <Clock className="h-4 w-4" /> Expired ({expiredCount})
                </button>
            </div>

            {/* Promo Table */}
            <Card>
                <CardHeader className="pb-3">
                    <div className="flex items-center justify-between">
                        <CardTitle className="text-lg">{promoTab === "public" ? "Active Public Promo Codes" : promoTab === "private" ? "Active Private Coupons" : "Expired Promo Codes"}</CardTitle>
                        <div className="flex gap-2">
                            <Button variant="outline" size="sm" onClick={handleExportPromos} disabled={filtered.length === 0}><Download className="mr-2 h-4 w-4" /> Export</Button>
                            {promoTab !== "expired" && <Button size="sm" onClick={openCreate}><Plus className="mr-2 h-4 w-4" /> {promoTab === "public" ? "Create Code" : "Create Coupon"}</Button>}
                        </div>
                    </div>
                </CardHeader>
                <CardContent className="space-y-4">
                    <div className="flex flex-wrap items-center gap-3">
                        <div className="relative flex-1 max-w-sm"><Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" /><Input placeholder="Search by code or description..." aria-label="Search promotions" value={search} onChange={(e) => setSearch(e.target.value)} className="pl-9" /></div>
                    </div>

                    {loading ? (
                        <div className="flex items-center justify-center p-12"><div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" /></div>
                    ) : filtered.length === 0 ? (
                        <div className="text-center py-12"><Ticket className="h-12 w-12 text-muted-foreground/30 mx-auto mb-4" /><h3 className="text-lg font-semibold">No {promoTab === "expired" ? "expired codes" : promoTab === "public" ? "promo codes" : "coupons"} found</h3><p className="text-muted-foreground mt-1">{promoTab === "expired" ? "No expired promo codes." : "Create one to get started."}</p></div>
                    ) : (
                        <div className="border rounded-lg">
                            <Table>
                                <TableHeader><TableRow>
                                    <SortableHead column="code" sort={promoSort} onSort={togglePromoSort}>Code</SortableHead>
                                    <SortableHead column="discount_value" sort={promoSort} onSort={togglePromoSort}>Discount</SortableHead>
                                    <SortableHead column="uses" sort={promoSort} onSort={togglePromoSort}>Uses</SortableHead>
                                    {promoTab === "expired" && <SortableHead column="promo_type" sort={promoSort} onSort={togglePromoSort}>Type</SortableHead>}
                                    {(promoTab === "private" || promoTab === "expired") && <TableHead>Assigned To</TableHead>}
                                    <SortableHead column="expiry_date" sort={promoSort} onSort={togglePromoSort}>Expiry</SortableHead>
                                    <SortableHead column="is_active" sort={promoSort} onSort={togglePromoSort}>Status</SortableHead>
                                    <TableHead className="text-right">Actions</TableHead>
                                </TableRow></TableHeader>
                                <TableBody>
                                    {sortedPromos.map((p) => {
                                        const status = getPromoStatus(p);
                                        const sc = STATUS_CONFIG[status] || STATUS_CONFIG.inactive;
                                        return (
                                            <TableRow key={p.id}>
                                                <TableCell><span className="font-mono font-semibold text-violet-600 dark:text-violet-400 bg-violet-500/10 px-2 py-0.5 rounded">{p.code}</span>{p.description && <p className="text-xs text-muted-foreground mt-1">{p.description}</p>}</TableCell>
                                                <TableCell className="text-sm">{p.free_ride ? <span className="text-emerald-600 dark:text-emerald-400 font-semibold">Free Ride</span> : p.discount_type === "flat" ? formatCurrency(p.discount_value) : `${p.discount_value}%`}{!p.free_ride && p.max_discount != null && <span className="text-xs text-muted-foreground ml-1">(max {formatCurrency(p.max_discount)})</span>}</TableCell>
                                                <TableCell className="text-sm">{p.uses}/{p.max_uses || "∞"}</TableCell>
                                                {promoTab === "expired" && <TableCell><Badge variant="outline" className="text-xs">{p.promo_type === "private" ? "Private" : "Public"}</Badge></TableCell>}
                                                {(promoTab === "private" || promoTab === "expired") && <TableCell className="text-sm">{(p.assigned_user_ids || []).length > 0 ? `${(p.assigned_user_ids || []).length} user${(p.assigned_user_ids || []).length !== 1 ? "s" : ""}` : "—"}</TableCell>}
                                                <TableCell className="text-sm text-muted-foreground">{p.expiry_date ? formatDate(p.expiry_date) : "No expiry"}</TableCell>
                                                <TableCell><Badge className={sc.color}>{sc.label}</Badge></TableCell>
                                                <TableCell className="text-right"><div className="flex gap-1 justify-end">
                                                    <Button variant="ghost" size="icon" className="h-8 w-8" onClick={() => openEdit(p)}><Pencil className="h-4 w-4" /></Button>
                                                    {promoTab !== "expired" && <Button variant="ghost" size="icon" className="h-8 w-8" onClick={() => toggleActive(p)}>{p.is_active ? <ToggleRight className="h-4 w-4 text-emerald-500" /> : <ToggleLeft className="h-4 w-4 text-muted-foreground" />}</Button>}
                                                    <Button variant="ghost" size="icon" className="h-8 w-8 text-destructive" onClick={() => handleDelete(p.id)}><Trash2 className="h-4 w-4" /></Button>
                                                </div></TableCell>
                                            </TableRow>
                                        );
                                    })}
                                </TableBody>
                            </Table>
                            <div className="px-4 border-t">
                                <Pagination
                                    page={page}
                                    pageSize={PAGE_SIZE}
                                    hasNextPage={hasNextPage}
                                    onPageChange={setPage}
                                />
                            </div>
                        </div>
                    )}
                </CardContent>
            </Card>

            {/* Charts with filter dropdown */}
            {stats && stats.daily_usage && stats.daily_usage.length > 0 && (
                <div className="space-y-3">
                    <div className="flex items-center justify-between">
                        <h2 className="text-lg font-semibold flex items-center gap-2"><BarChart3 className="h-5 w-5 text-blue-500" /> Analytics</h2>
                        <Select value={chartFilter} onValueChange={setChartFilter}>
                            <SelectTrigger className="w-44" aria-label="Filter chart by promotion type"><SelectValue /></SelectTrigger>
                            <SelectContent>
                                <SelectItem value="all">All Promos</SelectItem>
                                <SelectItem value="public">Public Codes Only</SelectItem>
                                <SelectItem value="private">Private Coupons Only</SelectItem>
                            </SelectContent>
                        </Select>
                    </div>
                    <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                        <Card>
                            <CardHeader className="pb-2"><CardTitle className="text-sm flex items-center gap-2"><BarChart3 className="h-4 w-4 text-blue-500" /> Daily Redemptions</CardTitle></CardHeader>
                            <CardContent>
                                <ResponsiveContainer width="100%" height={220}>
                                    <BarChart data={chartData}>
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
                                    <LineChart data={chartData}>
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
                    {/* Date range selectors for usage */}
                    <div className="flex flex-wrap items-center gap-2">
                        {DATE_RANGES.map((r) => (
                            <Button key={r.key} variant={usageDateRange === r.key ? "default" : "outline"} size="sm" onClick={() => setUsageDateRange(r.key)} className={usageDateRange === r.key ? "bg-red-500 hover:bg-red-600 text-white" : ""}>
                                {r.label}
                            </Button>
                        ))}
                        <Separator orientation="vertical" className="h-6 mx-1" />
                        <div className="flex items-center gap-2">
                            <Label className="text-xs text-muted-foreground" htmlFor="usage-date-from">From</Label>
                            <Input id="usage-date-from" type="date" value={usageDateFrom} onChange={(e) => setUsageDateFrom(e.target.value)} className="w-36 h-8 text-xs" />
                        </div>
                        <div className="flex items-center gap-2">
                            <Label className="text-xs text-muted-foreground" htmlFor="usage-date-to">To</Label>
                            <Input id="usage-date-to" type="date" value={usageDateTo} onChange={(e) => setUsageDateTo(e.target.value)} className="w-36 h-8 text-xs" />
                        </div>
                        {(usageDateFrom || usageDateTo) && <Button variant="ghost" size="sm" onClick={() => { setUsageDateFrom(""); setUsageDateTo(""); }}>Clear</Button>}
                    </div>

                    {/* Usage filters */}
                    <div className="flex flex-wrap items-center gap-3">
                        <div className="relative flex-1 max-w-sm"><Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" /><Input placeholder="Search by code or user ID..." aria-label="Search usage history" value={usageSearch} onChange={(e) => setUsageSearch(e.target.value)} className="pl-9" /></div>
                        <Select value={usageTypeFilter} onValueChange={setUsageTypeFilter}>
                            <SelectTrigger className="w-44" aria-label="Filter usage by promotion type"><SelectValue /></SelectTrigger>
                            <SelectContent>
                                <SelectItem value="all">All Promos</SelectItem>
                                <SelectItem value="public">Public Codes Only</SelectItem>
                                <SelectItem value="private">Private Coupons Only</SelectItem>
                            </SelectContent>
                        </Select>
                    </div>

                    {usageLoading ? (
                        <div className="flex items-center justify-center p-12"><div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" /></div>
                    ) : filteredUsage.length === 0 ? (
                        <div className="text-center py-8 text-muted-foreground">No usage records found.</div>
                    ) : (
                        <div className="border rounded-lg">
                            <Table>
                                <TableHeader><TableRow>
                                    <SortableHead column="created_at" sort={usageSort} onSort={toggleUsageSort}>Date</SortableHead>
                                    <SortableHead column="user_name" sort={usageSort} onSort={toggleUsageSort}>User</SortableHead>
                                    <SortableHead column="code" sort={usageSort} onSort={toggleUsageSort}>Code</SortableHead>
                                    <TableHead>Type</TableHead>
                                    <SortableHead column="ride_id" sort={usageSort} onSort={toggleUsageSort}>Booking</SortableHead>
                                    <SortableHead column="discount_applied" sort={usageSort} onSort={toggleUsageSort}>Discount Applied</SortableHead>
                                </TableRow></TableHeader>
                                <TableBody>
                                    {sortedUsage.map((u) => {
                                        const pType = promoIdTypeMap[u.promo_id] || "discount";
                                        return (
                                            <TableRow key={u.id}>
                                                <TableCell className="text-sm text-muted-foreground">{formatDate(u.created_at)}</TableCell>
                                                <TableCell className="text-sm">
                                                    {u.user_name
                                                        ? <span className="font-medium">{u.user_name}</span>
                                                        : <span className="font-mono text-muted-foreground">{u.user_id?.slice(0, 10)}…</span>}
                                                </TableCell>
                                                <TableCell><span className="font-mono font-semibold text-violet-600 dark:text-violet-400 bg-violet-500/10 px-2 py-0.5 rounded text-xs">{u.code}</span></TableCell>
                                                <TableCell><Badge variant="outline" className="text-xs">{pType === "private" ? "Private" : "Public"}</Badge></TableCell>
                                                <TableCell>
                                                    {u.ride_id ? (
                                                        <button
                                                            onClick={() => setSelectedRideId(u.ride_id!)}
                                                            className="font-mono text-xs text-blue-600 dark:text-blue-400 hover:underline bg-blue-500/10 px-2 py-0.5 rounded"
                                                        >
                                                            {u.ride_id.slice(0, 8)}…
                                                        </button>
                                                    ) : (
                                                        <span className="text-muted-foreground text-xs">—</span>
                                                    )}
                                                </TableCell>
                                                <TableCell className="text-sm font-medium text-emerald-600 dark:text-emerald-400">{formatCurrency(u.discount_applied)}</TableCell>
                                            </TableRow>
                                        );
                                    })}
                                </TableBody>
                            </Table>
                            <div className="px-4 border-t">
                                <Pagination
                                    page={usagePage}
                                    pageSize={PAGE_SIZE}
                                    hasNextPage={usageHasNext}
                                    onPageChange={setUsagePage}
                                />
                            </div>
                        </div>
                    )}
                </CardContent>
            </Card>

            {/* Create/Edit Dialog */}
            <Dialog open={dialogOpen} onOpenChange={(open) => { if (!open) { setDialogOpen(false); resetForm(); } }}>
                <DialogContent className="sm:max-w-lg max-h-[90vh] overflow-y-auto">
                    <DialogHeader><DialogTitle className="flex items-center gap-2"><Ticket className="h-5 w-5 text-violet-500" /> {editingPromo ? "Edit" : "Create"} {(promoTab === "private" || editingPromo?.promo_type === "private") ? "Private Coupon" : "Promo Code"}</DialogTitle></DialogHeader>
                    <div className="space-y-4">
                        <div className="grid grid-cols-2 gap-4">
                            <div className="space-y-2"><Label>Code</Label><Input placeholder="e.g. SAVE10" value={form.code} onChange={(e) => setForm({ ...form, code: e.target.value })} className="uppercase tracking-widest font-mono" /></div>
                            <div className="flex items-center gap-2 pt-6">
                                <Switch id="free-ride-toggle" checked={form.free_ride} onCheckedChange={(v) => setForm({ ...form, free_ride: v })} />
                                <Label htmlFor="free-ride-toggle" className="cursor-pointer text-sm font-medium">Free ride (100% covered)</Label>
                            </div>
                        </div>
                        {!form.free_ride && (
                            <>
                                <div className="grid grid-cols-2 gap-4">
                                    <div className="space-y-2">
                                        <Label>Type</Label>
                                        <Select value={form.discount_type} onValueChange={(v) => setForm({ ...form, discount_type: v as any })}>
                                            <SelectTrigger><SelectValue /></SelectTrigger>
                                            <SelectContent>
                                                <SelectItem value="flat">Flat ($) — fixed dollar off</SelectItem>
                                                <SelectItem value="percentage">Percentage (%) — % of ride fare</SelectItem>
                                            </SelectContent>
                                        </Select>
                                    </div>
                                    <div className="space-y-2">
                                        <Label>{form.discount_type === "flat" ? "Discount Amount ($)" : "Discount Percentage (%)"}</Label>
                                        <Input type="number" placeholder={form.discount_type === "flat" ? "10.00" : "20"} value={form.discount_value} onChange={(e) => setForm({ ...form, discount_value: e.target.value })} />
                                        <p className="text-xs text-muted-foreground">
                                            {form.discount_type === "flat"
                                                ? "Deducted from ride fare only (not booking fees or taxes)"
                                                : "Applied to ride fare only — set a cap below to limit exposure"}
                                        </p>
                                    </div>
                                </div>
                                {form.discount_type === "percentage" && (
                                    <div className="grid grid-cols-2 gap-4">
                                        <div className="space-y-2">
                                            <Label>Max Discount Cap ($) <span className="text-red-500 font-normal">*required</span></Label>
                                            <Input type="number" placeholder="25.00" value={form.max_discount} onChange={(e) => setForm({ ...form, max_discount: e.target.value })} className={!form.max_discount ? "border-amber-400 focus-visible:ring-amber-400" : ""} />
                                            {!form.max_discount && (
                                                <p className="text-xs text-amber-600 dark:text-amber-400">Without a cap, a 75% promo on a $100 fare gives $75 off. Set a cap to limit this.</p>
                                            )}
                                        </div>
                                    </div>
                                )}
                            </>
                        )}
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

                        <div className="space-y-2">
                            <Label className="flex items-center gap-1.5">
                                Service Area
                                <span className="text-xs font-normal text-muted-foreground">(leave blank to apply in all areas)</span>
                            </Label>
                            <Select
                                value={form.service_area_id || "all"}
                                onValueChange={(v) => setForm({ ...form, service_area_id: v === "all" ? "" : v })}
                            >
                                <SelectTrigger>
                                    <SelectValue placeholder="All areas" />
                                </SelectTrigger>
                                <SelectContent>
                                    <SelectItem value="all">All areas</SelectItem>
                                    {serviceAreas.map((area) => (
                                        <SelectItem key={area.id} value={area.id}>{area.name}</SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                        </div>

                        {(promoTab === "private" || editingPromo?.promo_type === "private") && (
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

                        <Button className="w-full" onClick={handleSave} disabled={saving || !form.code.trim() || (!form.free_ride && !form.discount_value)}>{saving ? "Saving..." : editingPromo ? "Update" : "Create"}</Button>
                    </div>
                </DialogContent>
            </Dialog>

            <RideDetailModal
                rideId={selectedRideId}
                open={!!selectedRideId}
                onClose={() => setSelectedRideId(null)}
            />

            <AlertDialog open={!!deleteTarget} onOpenChange={(open) => { if (!open) setDeleteTarget(null); }}>
                <AlertDialogContent>
                    <AlertDialogHeader>
                        <AlertDialogTitle>Delete promo code?</AlertDialogTitle>
                        <AlertDialogDescription>This action cannot be undone.</AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                        <AlertDialogCancel>Cancel</AlertDialogCancel>
                        <AlertDialogAction onClick={confirmDelete} className="bg-red-600 hover:bg-red-700">Delete</AlertDialogAction>
                    </AlertDialogFooter>
                </AlertDialogContent>
            </AlertDialog>
        </div>
    );
}
