"use client";

import { useEffect, useState, useCallback } from "react";
import { getDriverStats, getDriverDocuments, reviewDocument, updateDriver, getServiceAreas } from "@/lib/api";
import { exportToCsv } from "@/lib/export-csv";
import { formatCurrency } from "@/lib/utils";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Sheet, SheetContent, SheetTitle, SheetDescription } from "@/components/ui/sheet";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from "@/components/ui/dialog";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Search, Users, Wifi, WifiOff, ShieldCheck, ShieldAlert, Download, X, Star, Car, MapPin, CreditCard, Clock, DollarSign, CheckCircle, XCircle, FileText, Phone, Mail, CalendarRange, ExternalLink, Copy, AlertTriangle, ZoomIn, Image, Pencil, Save, Loader2, Eye, ArrowUpDown, ArrowUp, ArrowDown, Ban, Pause } from "lucide-react";
import DriverStatsCards from "./_components/driver-stats-cards";
import DriverCharts from "./_components/driver-charts";
import AreaStatsTable from "./_components/area-stats-table";
import DriverActionBar from "./_components/driver-action-bar";
import DriverNotes from "./_components/driver-notes";
import DriverTimeline from "./_components/driver-timeline";

const STATUS_TABS = [
    { value: "all", label: "All", icon: Users },
    { value: "verified", label: "Verified", icon: ShieldCheck },
    { value: "unverified", label: "Unverified", icon: ShieldAlert },
    { value: "needs_review", label: "Needs Review", icon: AlertTriangle },
    { value: "suspended", label: "Suspended", icon: Pause },
    { value: "banned", label: "Banned", icon: Ban },
    { value: "online", label: "Online", icon: Wifi },
    { value: "offline", label: "Offline", icon: WifiOff },
];

export default function DriversPage() {
    const [data, setData] = useState<any>(null);
    const [drivers, setDrivers] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [search, setSearch] = useState("");
    const [statusFilter, setStatusFilter] = useState("all");
    const [sortKey, setSortKey] = useState<string>("created_at");
    const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");
    const [selected, setSelected] = useState<any>(null);
    const [verifying, setVerifying] = useState(false);
    const [driverDocs, setDriverDocs] = useState<any[]>([]);
    const [docsLoading, setDocsLoading] = useState(false);
    const [docBusy, setDocBusy] = useState<string | null>(null);
    const [reviewingDoc, setReviewingDoc] = useState<{ id: string; action: "approved" | "rejected"; docType?: string; requiresExpiry?: boolean } | null>(null);
    const [reviewExpiry, setReviewExpiry] = useState("");
    const [reviewReason, setReviewReason] = useState("");
    const [previewUrl, setPreviewUrl] = useState<string | null>(null);
    const [editing, setEditing] = useState(false);
    const [editForm, setEditForm] = useState<Record<string, any>>({});
    const [saving, setSaving] = useState(false);
    const [allServiceAreas, setAllServiceAreas] = useState<any[]>([]);
    const [serviceAreaId, setServiceAreaId] = useState<string>("");
    const [startDate, setStartDate] = useState("");
    const [endDate, setEndDate] = useState("");
    const [serviceAreas, setServiceAreas] = useState<{ id: string; name: string }[]>([]);

    const loadData = useCallback(() => {
        setLoading(true);
        const params: any = {};
        if (serviceAreaId) params.service_area_id = serviceAreaId;
        if (startDate) params.start_date = startDate;
        if (endDate) params.end_date = endDate;
        getDriverStats(params).then((res) => { setData(res); setDrivers(res.drivers || []); setServiceAreas(res.service_areas || []); }).catch(() => {}).finally(() => setLoading(false));
    }, [serviceAreaId, startDate, endDate]);

    useEffect(() => { loadData(); }, [loadData]);
    useEffect(() => { getServiceAreas().then(setAllServiceAreas).catch(() => {}); }, []);
    useEffect(() => { if (!selected?.id) { setDriverDocs([]); return; } setDocsLoading(true); getDriverDocuments(selected.id).then((d) => setDriverDocs(Array.isArray(d) ? d : [])).catch(() => setDriverDocs([])).finally(() => setDocsLoading(false)); }, [selected?.id]);
    useEffect(() => { setEditing(false); setEditForm({}); }, [selected?.id]);

    const reloadDriverDocs = async () => { if (!selected?.id) return; try { const d = await getDriverDocuments(selected.id); setDriverDocs(Array.isArray(d) ? d : []); } catch {} };

    const handleReviewDoc = async (docId: string, status: "approved" | "rejected", reason?: string, expiry?: string) => {
        setDocBusy(docId);
        try { await reviewDocument(docId, status, reason, expiry ? new Date(expiry).toISOString() : undefined); await reloadDriverDocs(); loadData(); } catch (e: any) { alert("Could not update document: " + (e?.message || "unknown error")); } finally { setDocBusy(null); }
    };

    const openReviewDialog = (docId: string, action: "approved" | "rejected") => {
        // Find the doc to check if its requirement has expiry
        const doc = activeDocs.find(d => d.id === docId);
        const docType = doc?.document_type || doc?.requirement_id || "";
        // Check if the matching required doc has has_expiry
        const matchedReq = requiredDocs.find(rd =>
            docType.toLowerCase().replace(/[^a-z0-9]/g, "_").includes(rd.key.replace(/[^a-z0-9]/g, "_")) ||
            docType.toLowerCase() === rd.label.toLowerCase() ||
            doc?.requirement_id === rd.key
        );
        setReviewingDoc({ id: docId, action, docType: matchedReq?.label || docType, requiresExpiry: matchedReq?.has_expiry || false });
        setReviewExpiry(""); setReviewReason("");
    };
    const confirmReview = async () => { if (!reviewingDoc) return; await handleReviewDoc(reviewingDoc.id, reviewingDoc.action, reviewReason || undefined, reviewExpiry || undefined); setReviewingDoc(null); };

    const handleVerify = async (driverId: string, verified: boolean) => {
        setVerifying(true);
        try { const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"; const token = (await import("@/store/authStore")).useAuthStore.getState().token; await fetch(`${API_BASE}/api/admin/drivers/${driverId}/verify`, { method: "POST", headers: { "Content-Type": "application/json", "Authorization": `Bearer ${token}` }, body: JSON.stringify({ verified }) }); loadData(); if (selected?.id === driverId) setSelected({ ...selected, is_verified: verified }); } catch {}
        setVerifying(false);
    };

    const startEditing = () => { if (!selected) return; setEditForm({ first_name: selected.first_name || "", last_name: selected.last_name || "", email: selected.email || "", phone: selected.phone || "", city: selected.city || "", service_area_id: selected.service_area_id || "", vehicle_make: selected.vehicle_make || "", vehicle_model: selected.vehicle_model || "", vehicle_color: selected.vehicle_color || "", vehicle_year: selected.vehicle_year || "", license_plate: selected.license_plate || "", vehicle_vin: selected.vehicle_vin || "" }); setEditing(true); };

    const saveEdits = async () => {
        if (!selected) return;
        const changes: Record<string, any> = {};
        for (const [k, v] of Object.entries(editForm)) { if (v !== (selected[k] || "")) changes[k] = v; }
        if (Object.keys(changes).length === 0) { setEditing(false); return; }
        setSaving(true);
        try { await updateDriver(selected.id, changes); const updated = { ...selected, ...changes }; setSelected(updated); setDrivers(prev => prev.map(d => d.id === selected.id ? { ...d, ...changes } : d)); setEditing(false); } catch (e: any) { alert("Failed to save: " + (e?.message || "unknown error")); } finally { setSaving(false); }
    };

    const ef = (field: string) => editForm[field] ?? "";
    const setEf = (field: string, value: string) => setEditForm(prev => ({ ...prev, [field]: value }));

    const filtered = drivers.filter(d => {
        const matchSearch = !search || (d.first_name + " " + d.last_name).toLowerCase().includes(search.toLowerCase()) || d.email?.toLowerCase().includes(search.toLowerCase()) || d.license_plate?.toLowerCase().includes(search.toLowerCase()) || d.id?.toLowerCase().includes(search.toLowerCase());
        let matchStatus = true;
        if (statusFilter === "online") matchStatus = d.is_online;
        if (statusFilter === "offline") matchStatus = !d.is_online;
        if (statusFilter === "verified") matchStatus = d.is_verified && !d.needs_review && d.status !== "suspended" && d.status !== "banned" && d.status !== "rejected";
        if (statusFilter === "unverified") matchStatus = !d.is_verified && d.status !== "rejected" && d.status !== "suspended" && d.status !== "banned";
        if (statusFilter === "needs_review") matchStatus = d.needs_review === true;
        if (statusFilter === "suspended") matchStatus = d.status === "suspended";
        if (statusFilter === "banned") matchStatus = d.status === "banned";
        return matchSearch && matchStatus;
    });

    // Sort
    const sorted = [...filtered].sort((a, b) => {
        let av: any, bv: any;
        if (sortKey === "name") { av = `${a.first_name} ${a.last_name}`.toLowerCase(); bv = `${b.first_name} ${b.last_name}`.toLowerCase(); }
        else if (sortKey === "rating") { av = a.rating || 0; bv = b.rating || 0; }
        else if (sortKey === "total_rides") { av = a.total_rides || 0; bv = b.total_rides || 0; }
        else if (sortKey === "total_earnings") { av = a.total_earnings || 0; bv = b.total_earnings || 0; }
        else if (sortKey === "created_at") { av = a.created_at || ""; bv = b.created_at || ""; }
        else if (sortKey === "region") { av = (serviceAreas.find(sa => sa.id === a.service_area_id)?.name || "zzz").toLowerCase(); bv = (serviceAreas.find(sa => sa.id === b.service_area_id)?.name || "zzz").toLowerCase(); }
        else { av = (a[sortKey] || "").toString().toLowerCase(); bv = (b[sortKey] || "").toString().toLowerCase(); }
        if (av < bv) return sortDir === "asc" ? -1 : 1;
        if (av > bv) return sortDir === "asc" ? 1 : -1;
        return 0;
    });

    const handleSort = (key: string) => { if (sortKey === key) { setSortDir(d => d === "asc" ? "desc" : "asc"); } else { setSortKey(key); setSortDir(key === "created_at" || key === "total_earnings" || key === "total_rides" || key === "rating" ? "desc" : "asc"); } };
    const SortIcon = ({ col }: { col: string }) => { if (sortKey !== col) return <ArrowUpDown className="h-3 w-3 opacity-30 inline ml-1" />; return sortDir === "asc" ? <ArrowUp className="h-3 w-3 inline ml-1" /> : <ArrowDown className="h-3 w-3 inline ml-1" />; };

    const statusCounts = (s: string) => { if (s === "all") return drivers.length; if (s === "online") return drivers.filter(d => d.is_online).length; if (s === "offline") return drivers.filter(d => !d.is_online).length; if (s === "verified") return drivers.filter(d => d.is_verified && !d.needs_review && d.status !== "suspended" && d.status !== "banned" && d.status !== "rejected").length; if (s === "unverified") return drivers.filter(d => !d.is_verified && d.status !== "rejected" && d.status !== "suspended" && d.status !== "banned").length; if (s === "needs_review") return drivers.filter(d => d.needs_review).length; if (s === "suspended") return drivers.filter(d => d.status === "suspended").length; if (s === "banned") return drivers.filter(d => d.status === "banned").length; return 0; };
    const fmtDate = (d: string) => { if (!d) return "\u2014"; try { return new Date(d).toLocaleDateString("en-CA", { month: "short", day: "numeric", year: "numeric" }); } catch { return d; } };

    const handleExport = () => { exportToCsv("drivers", sorted, [{ key: "id", label: "ID" }, { key: "first_name", label: "First Name" }, { key: "last_name", label: "Last Name" }, { key: "email", label: "Email" }, { key: "phone", label: "Phone" }, { key: "service_area_id", label: "Service Area ID" }, { key: "is_verified", label: "Verified" }, { key: "is_online", label: "Online" }, { key: "rating", label: "Rating" }, { key: "total_rides", label: "Rides" }, { key: "total_earnings", label: "Earnings" }, { key: "vehicle_make", label: "Vehicle Make" }, { key: "vehicle_model", label: "Vehicle Model" }, { key: "license_plate", label: "License Plate" }, { key: "created_at", label: "Joined" }]); };

    const selectedAreaName = serviceAreaId ? serviceAreas.find(a => a.id === serviceAreaId)?.name || "Selected Area" : "All Areas";
    const activeDocs = driverDocs.filter(d => d.status !== "superseded");
    const selectedDriverArea = selected ? allServiceAreas.find(a => a.id === selected.service_area_id) : null;
    const requiredDocs: { id?: string; key: string; label: string; has_expiry: boolean }[] = selectedDriverArea?.required_documents || [];

    // Map service area document key to driver profile legacy expiry field
    function _docKeyToExpiryField(key: string): string | null {
        const k = key.toLowerCase();
        if (k.includes("license") || k.includes("driving") || k.includes("permit")) return "license_expiry_date";
        if (k.includes("insurance")) return "insurance_expiry_date";
        if (k.includes("inspection")) return "vehicle_inspection_expiry_date";
        if (k.includes("background")) return "background_check_expiry_date";
        if (k.includes("work") || k.includes("eligibility")) return "work_eligibility_expiry_date";
        return null;
    }

    // Get expiry for a required document — prefers the expiry stored on the actual
    // document record (from the API), falls back to the legacy top-level field on
    // the driver row (set during onboarding or admin approval before docs flow existed).
    function _getDocExpiry(rdId: string | undefined, rdKey: string, rdLabel: string): string | undefined {
        const matchDoc = activeDocs.find(d => {
            if (d.requirement_id) return d.requirement_id === rdId || d.requirement_id === rdKey;
            const dt = (d.document_type || "").toLowerCase();
            const label = rdLabel.toLowerCase();
            const key = rdKey.toLowerCase().replace(/_/g, " ");
            return dt === label || dt === key || dt.replace(/[^a-z0-9]/g, "_").includes(rdKey.replace(/[^a-z0-9]/g, "_"));
        });
        // doc record expiry (set when admin approves with an expiry date)
        const docExpiry = matchDoc?.expiry_date || matchDoc?.expires_at;
        if (docExpiry) return docExpiry;
        // Legacy fallback: driver row top-level expiry field
        const legacyField = _docKeyToExpiryField(rdKey);
        return legacyField ? selected?.[legacyField] : undefined;
    }

    return (
        <div className="space-y-5">
            <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
                <div>
                    <h1 className="text-2xl font-bold">Drivers</h1>
                    <p className="text-sm text-muted-foreground">{drivers.length} drivers {serviceAreaId ? `in ${selectedAreaName}` : "overall"}</p>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                    <div className="flex items-center gap-1.5">
                        <MapPin className="h-4 w-4 text-muted-foreground" />
                        <Select value={serviceAreaId || "all"} onValueChange={(v) => setServiceAreaId(v === "all" ? "" : v)}>
                            <SelectTrigger className="h-9 text-xs w-[180px]"><SelectValue placeholder="All Service Areas" /></SelectTrigger>
                            <SelectContent><SelectItem value="all">All Service Areas</SelectItem>{serviceAreas.map(a => <SelectItem key={a.id} value={a.id}>{a.name}</SelectItem>)}</SelectContent>
                        </Select>
                    </div>
                    <div className="flex items-center gap-1.5">
                        <CalendarRange className="h-4 w-4 text-muted-foreground" />
                        <Input type="date" value={startDate} onChange={e => setStartDate(e.target.value)} className="h-9 w-[140px] text-xs" />
                        <span className="text-xs text-muted-foreground">to</span>
                        <Input type="date" value={endDate} onChange={e => setEndDate(e.target.value)} className="h-9 w-[140px] text-xs" />
                    </div>
                    {(serviceAreaId || startDate || endDate) && <Button variant="ghost" size="sm" onClick={() => { setServiceAreaId(""); setStartDate(""); setEndDate(""); }}><X className="h-3.5 w-3.5" /> Clear</Button>}
                    <Button variant="outline" size="sm" onClick={handleExport} disabled={filtered.length === 0}><Download className="h-4 w-4" /> Export</Button>
                </div>
            </div>

            <DriverStatsCards stats={data?.stats || null} loading={loading} />

            <div className="space-y-3">
                <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
                    <div className="flex gap-1.5 overflow-x-auto pb-1">
                        {STATUS_TABS.map(tab => (
                            <button key={tab.value} onClick={() => setStatusFilter(tab.value)} className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold whitespace-nowrap transition ${statusFilter === tab.value ? "bg-primary text-white" : "bg-muted text-muted-foreground hover:bg-muted/80"}`}>
                                <tab.icon className="h-3.5 w-3.5" />{tab.label}<span className={`ml-1 px-1.5 rounded text-[10px] ${statusFilter === tab.value ? "bg-white/20" : "bg-background"}`}>{statusCounts(tab.value)}</span>
                            </button>
                        ))}
                    </div>
                    <div className="relative w-full sm:w-72"><Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" /><Input placeholder="Search by name, email, plate..." value={search} onChange={e => setSearch(e.target.value)} className="pl-9 h-9 text-sm" /></div>
                </div>

                <div className="bg-card border rounded-2xl overflow-hidden shadow-sm">
                    <Table>
                        <TableHeader>
                            <TableRow className="bg-muted/50 hover:bg-muted/50 border-b-0">
                                <TableHead className="h-11 pl-5 w-20"><span className="text-[11px] font-semibold text-foreground/80 uppercase tracking-wider">Actions</span></TableHead>
                                <TableHead className="h-11 cursor-pointer select-none" onClick={() => handleSort("name")}><span className="text-[11px] font-semibold text-foreground/80 uppercase tracking-wider">Driver<SortIcon col="name" /></span></TableHead>
                                <TableHead className="h-11 cursor-pointer select-none" onClick={() => handleSort("status")}><span className="text-[11px] font-semibold text-foreground/80 uppercase tracking-wider">Status<SortIcon col="status" /></span></TableHead>
                                <TableHead className="h-11 cursor-pointer select-none" onClick={() => handleSort("vehicle_make")}><span className="text-[11px] font-semibold text-foreground/80 uppercase tracking-wider">Vehicle<SortIcon col="vehicle_make" /></span></TableHead>
                                <TableHead className="h-11 cursor-pointer select-none text-center" onClick={() => handleSort("rating")}><span className="text-[11px] font-semibold text-foreground/80 uppercase tracking-wider">Rating<SortIcon col="rating" /></span></TableHead>
                                <TableHead className="h-11 cursor-pointer select-none text-center" onClick={() => handleSort("total_rides")}><span className="text-[11px] font-semibold text-foreground/80 uppercase tracking-wider">Rides<SortIcon col="total_rides" /></span></TableHead>
                                <TableHead className="h-11 cursor-pointer select-none text-right" onClick={() => handleSort("total_earnings")}><span className="text-[11px] font-semibold text-foreground/80 uppercase tracking-wider">Earnings<SortIcon col="total_earnings" /></span></TableHead>
                                <TableHead className="h-11 cursor-pointer select-none" onClick={() => handleSort("region")}><span className="text-[11px] font-semibold text-foreground/80 uppercase tracking-wider">Region<SortIcon col="region" /></span></TableHead>
                                <TableHead className="h-11 cursor-pointer select-none pr-5" onClick={() => handleSort("created_at")}><span className="text-[11px] font-semibold text-foreground/80 uppercase tracking-wider">Joined<SortIcon col="created_at" /></span></TableHead>
                            </TableRow>
                        </TableHeader>
                        <TableBody>
                            {loading ? Array.from({ length: 5 }).map((_, i) => (
                                <TableRow key={i} className="animate-pulse">
                                    <TableCell><div className="h-8 w-16 bg-muted rounded" /></TableCell>
                                    <TableCell className="py-4"><div className="flex items-center gap-3"><div className="w-10 h-10 rounded-full bg-muted" /><div className="space-y-2"><div className="h-3 w-24 bg-muted rounded" /><div className="h-2 w-16 bg-muted rounded" /></div></div></TableCell>
                                    <TableCell><div className="h-4 w-16 bg-muted rounded" /></TableCell>
                                    <TableCell><div className="h-3 w-20 bg-muted rounded" /></TableCell>
                                    <TableCell><div className="h-4 w-8 bg-muted rounded mx-auto" /></TableCell>
                                    <TableCell><div className="h-4 w-8 bg-muted rounded mx-auto" /></TableCell>
                                    <TableCell><div className="h-4 w-12 bg-muted rounded ml-auto" /></TableCell>
                                    <TableCell><div className="h-3 w-16 bg-muted rounded" /></TableCell>
                                    <TableCell><div className="h-3 w-16 bg-muted rounded" /></TableCell>
                                </TableRow>
                            )) : sorted.length === 0 ? (
                                <TableRow>
                                    <TableCell colSpan={9} className="text-center py-20 text-muted-foreground"><Users className="h-12 w-12 mx-auto mb-3 opacity-20" /><p className="text-base font-medium">No drivers found</p><p className="text-sm mt-1">Try adjusting your search or filters</p></TableCell>
                                </TableRow>
                            ) : sorted.map(driver => {
                                const areaName = serviceAreas.find(a => a.id === driver.service_area_id)?.name;
                                return (
                                    <TableRow key={driver.id} className={`group cursor-pointer transition-colors hover:bg-muted/40 ${selected?.id === driver.id ? "bg-primary/5 hover:bg-primary/5" : ""}`} onClick={() => setSelected(driver)}>
                                        <TableCell className="pl-4 align-middle">
                                            <div className="flex items-center gap-1.5" onClick={(e) => e.stopPropagation()}>
                                                <Button size="sm" variant="secondary" className="h-7 text-[10px] font-medium px-2" onClick={(e) => { e.stopPropagation(); setSelected(driver); }}><Eye className="h-3 w-3 mr-1" />View</Button>
                                                {!driver.is_verified && (
                                                    <Button size="sm" variant="outline" className="h-7 text-[10px] font-medium px-2 border-amber-200 text-amber-600 hover:bg-amber-50 bg-amber-50/50" onClick={(e) => { e.stopPropagation(); handleVerify(driver.id, true); }}>
                                                        <ShieldCheck className="h-3 w-3 mr-1" />Verify
                                                    </Button>
                                                )}
                                            </div>
                                        </TableCell>
                                        <TableCell className="py-3">
                                            <div className="flex items-center gap-3">
                                                <div className="relative">
                                                    <div className="w-10 h-10 rounded-full bg-gradient-to-br from-primary/20 to-primary/5 flex items-center justify-center text-sm font-bold text-primary ring-1 ring-border shadow-sm">{(driver.first_name?.[0] || "")}{(driver.last_name?.[0] || "")}</div>
                                                    <span className={`absolute -bottom-0.5 -right-0.5 w-3 h-3 rounded-full border-2 border-card ${driver.is_online ? "bg-emerald-500" : "bg-gray-300"}`} />
                                                </div>
                                                <div className="flex-1 min-w-0">
                                                    <p className="text-sm font-semibold truncate">{driver.first_name} {driver.last_name}</p>
                                                    {driver.email && <p className="text-[11px] text-muted-foreground truncate">{driver.email}</p>}
                                                    {driver.phone && <p className="text-[11px] text-muted-foreground flex items-center gap-1 mt-0.5"><Phone className="h-2.5 w-2.5" /> {driver.phone}</p>}
                                                </div>
                                            </div>
                                        </TableCell>
                                        <TableCell>
                                            <div className="flex flex-col gap-1.5 items-start">
                                                {driver.status === "banned" ? <Badge variant="default" className="bg-red-200 text-red-800 hover:bg-red-200 dark:bg-red-900/40 dark:text-red-400 text-[10px] px-1.5 py-0 border-red-300 dark:border-red-800"><Ban className="h-3 w-3 mr-1" />Banned</Badge>
                                                : driver.status === "suspended" ? <Badge variant="default" className="bg-orange-100 text-orange-700 hover:bg-orange-100 dark:bg-orange-900/30 dark:text-orange-400 text-[10px] px-1.5 py-0 border-orange-200 dark:border-orange-800"><Pause className="h-3 w-3 mr-1" />Suspended</Badge>
                                                : driver.status === "rejected" ? <Badge variant="default" className="bg-red-100 text-red-700 hover:bg-red-100 dark:bg-red-900/30 dark:text-red-400 text-[10px] px-1.5 py-0 border-red-200 dark:border-red-800"><XCircle className="h-3 w-3 mr-1" />Rejected</Badge>
                                                : driver.needs_review ? <Badge variant="default" className="bg-amber-100 text-amber-700 hover:bg-amber-100 dark:bg-amber-900/30 dark:text-amber-400 text-[10px] px-1.5 py-0 border-amber-200 dark:border-amber-800"><AlertTriangle className="h-3 w-3 mr-1" />Needs Review</Badge>
                                                : driver.is_verified ? <Badge variant="default" className="bg-emerald-100 text-emerald-700 hover:bg-emerald-100 dark:bg-emerald-900/30 dark:text-emerald-400 text-[10px] px-1.5 py-0 border-emerald-200 dark:border-emerald-800"><ShieldCheck className="h-3 w-3 mr-1" />Verified</Badge>
                                                : <Badge variant="default" className="bg-amber-100 text-amber-700 hover:bg-amber-100 dark:bg-amber-900/30 dark:text-amber-400 text-[10px] px-1.5 py-0 border-amber-200 dark:border-amber-800"><ShieldAlert className="h-3 w-3 mr-1" />Pending</Badge>}
                                                <Badge variant="outline" className={`text-[10px] px-1.5 py-0 ${driver.is_online ? "border-emerald-300 text-emerald-600 bg-emerald-50 dark:bg-emerald-500/10" : ""}`}>{driver.is_online ? "Online" : "Offline"}</Badge>
                                            </div>
                                        </TableCell>
                                        <TableCell>
                                            <div className="flex flex-col gap-1 text-xs">
                                                <div className="flex items-center gap-1.5 text-muted-foreground font-medium">
                                                    <Car className="h-3.5 w-3.5" />
                                                    <span className="truncate max-w-[120px]">{[driver.vehicle_color, driver.vehicle_make, driver.vehicle_model].filter(Boolean).join(" ") || "No vehicle"}</span>
                                                </div>
                                                {driver.license_plate ? <span className="font-mono font-bold text-foreground/80 tracking-wider bg-muted px-1.5 py-0.5 rounded text-[10px] border shadow-sm self-start">{driver.license_plate}</span> : <span className="text-[10px] text-muted-foreground/60 italic">No plate</span>}
                                            </div>
                                        </TableCell>
                                        <TableCell className="text-center">
                                            <span className="text-xs font-bold flex items-center justify-center gap-1"><Star className="h-3 w-3 text-amber-500 fill-amber-500" />{driver.rating?.toFixed(1) || "\u2014"}</span>
                                        </TableCell>
                                        <TableCell className="text-center">
                                            <span className="text-xs font-bold">{(driver.total_rides || 0).toLocaleString()}</span>
                                        </TableCell>
                                        <TableCell className="text-right">
                                            <span className="text-xs font-bold text-emerald-600 dark:text-emerald-400">{formatCurrency(driver.total_earnings || 0)}</span>
                                        </TableCell>
                                        <TableCell>
                                            <div className="flex items-center gap-1.5 text-xs text-foreground font-medium truncate max-w-[120px]"><MapPin className="h-3.5 w-3.5 text-blue-500 shrink-0" />{areaName || "Unassigned"}</div>
                                        </TableCell>
                                        <TableCell className="pr-5">
                                            <div className="flex items-center gap-1.5 text-xs text-muted-foreground"><Clock className="h-3 w-3 shrink-0" />{fmtDate(driver.created_at)}</div>
                                        </TableCell>
                                    </TableRow>
                                );
                            })}
                        </TableBody>
                    </Table>
                </div>
            </div>

            {!serviceAreaId && <AreaStatsTable areaStats={data?.area_stats || []} loading={loading} onAreaClick={(areaId) => setServiceAreaId(areaId)} />}

            <DriverCharts charts={data?.charts || null} loading={loading} />

            {/* Driver Detail Slideout */}
            <Sheet open={!!selected} onOpenChange={(open) => { if (!open) { setSelected(null); setEditing(false); } }}>
                <SheetContent side="right" showCloseButton={false} className="w-full sm:max-w-none sm:w-[90vw] lg:w-[80vw] xl:w-[70vw] p-0 overflow-hidden flex flex-col" aria-describedby={undefined}>
                    <SheetTitle className="sr-only">Driver Details</SheetTitle>
                    <SheetDescription className="sr-only">View and edit driver information</SheetDescription>
                    {selected && (<>
                        <div className="border-b bg-gradient-to-r from-primary/5 to-transparent">
                            <div className="p-6">
                                <div className="flex items-start justify-between">
                                    <div className="flex items-center gap-4">
                                        <div className="relative">
                                            <div className="w-16 h-16 rounded-2xl bg-gradient-to-br from-primary/20 to-primary/5 flex items-center justify-center text-xl font-bold text-primary">{(selected.first_name?.[0] || "")}{(selected.last_name?.[0] || "")}</div>
                                            <span className={`absolute -bottom-1 -right-1 w-4 h-4 rounded-full border-2 border-background ${selected.is_online ? "bg-emerald-500" : "bg-gray-300"}`} />
                                        </div>
                                        <div>
                                            <h2 className="text-xl font-bold">{selected.first_name} {selected.last_name}</h2>
                                            <button onClick={() => navigator.clipboard.writeText(selected.id)} className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition font-mono bg-muted/50 px-2 py-0.5 rounded mt-1" title="Copy ID">{selected.id?.slice(0, 16)}...<Copy className="h-3 w-3" /></button>
                                            <div className="flex items-center gap-2 mt-2">
                                                {selected.status === "banned" ? <Badge className="bg-red-200 text-red-800 dark:bg-red-900/40 dark:text-red-400"><Ban className="h-3 w-3" /> Banned</Badge>
                                                : selected.status === "suspended" ? <Badge className="bg-orange-100 text-orange-700 dark:bg-orange-900/30 dark:text-orange-400"><Pause className="h-3 w-3" /> Suspended</Badge>
                                                : selected.status === "rejected" ? <Badge className="bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400"><XCircle className="h-3 w-3" /> Rejected</Badge>
                                                : selected.needs_review ? <Badge className="bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400"><AlertTriangle className="h-3 w-3" /> Needs Review</Badge>
                                                : selected.is_verified ? <Badge className="bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400"><ShieldCheck className="h-3 w-3" /> Verified</Badge>
                                                : <Badge className="bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400"><ShieldAlert className="h-3 w-3" /> Pending</Badge>}
                                                <Badge variant="outline" className={selected.is_online ? "border-emerald-300 text-emerald-600" : ""}>{selected.is_online ? "Online" : "Offline"}</Badge>
                                                {selected.subscription_status === "active" && <Badge className="bg-violet-100 text-violet-700 dark:bg-violet-900/30 dark:text-violet-400"><CreditCard className="h-3 w-3" /> Spinr Pass</Badge>}
                                            </div>
                                        </div>
                                    </div>
                                    <div className="flex items-center gap-2">
                                        {!editing ? <Button variant="outline" size="sm" onClick={startEditing}><Pencil className="h-3.5 w-3.5" /> Edit</Button> : (<>
                                            <Button variant="ghost" size="sm" onClick={() => setEditing(false)} disabled={saving}>Cancel</Button>
                                            <Button size="sm" onClick={saveEdits} disabled={saving} className="bg-emerald-600 hover:bg-emerald-700 text-white">{saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />} Save</Button>
                                        </>)}
                                        <Button variant="ghost" size="icon-sm" onClick={() => { setSelected(null); setEditing(false); }}><X className="h-4 w-4" /></Button>
                                    </div>
                                </div>
                                <div className="grid grid-cols-4 gap-3 mt-5">
                                    <QuickStat icon={Star} color="text-amber-500" bg="bg-amber-50 dark:bg-amber-900/20" label="Rating" value={selected.rating?.toFixed(1) || "New"} />
                                    <QuickStat icon={Car} color="text-blue-500" bg="bg-blue-50 dark:bg-blue-900/20" label="Rides" value={(selected.total_rides || 0).toLocaleString()} />
                                    <QuickStat icon={DollarSign} color="text-emerald-500" bg="bg-emerald-50 dark:bg-emerald-900/20" label="Earnings" value={formatCurrency(selected.total_earnings || 0)} />
                                    <QuickStat icon={CheckCircle} color="text-violet-500" bg="bg-violet-50 dark:bg-violet-900/20" label="Accept Rate" value={selected.acceptance_rate || "\u2014"} />
                                </div>
                            </div>
                        </div>

                        <Tabs defaultValue="overview" className="flex-1 overflow-hidden flex flex-col">
                            <TabsList className="mx-6 mt-4 w-fit">
                                <TabsTrigger value="overview">Overview</TabsTrigger>
                                <TabsTrigger value="documents">Documents{activeDocs.length > 0 && <span className="ml-1.5 bg-primary/10 text-primary text-[10px] font-bold px-1.5 py-0.5 rounded-full">{activeDocs.length}</span>}</TabsTrigger>
                                <TabsTrigger value="verification">Actions</TabsTrigger>
                                <TabsTrigger value="notes">Notes</TabsTrigger>
                                <TabsTrigger value="history">History</TabsTrigger>
                            </TabsList>
                            <div className="flex-1 overflow-y-auto px-6 pb-6">
                                {/* Overview */}
                                <TabsContent value="overview" className="mt-4 space-y-5">
                                    <DetailSection title="Contact Information" icon={Mail}>
                                        {editing ? (
                                            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                                                <EditField label="First Name" value={ef("first_name")} onChange={v => setEf("first_name", v)} />
                                                <EditField label="Last Name" value={ef("last_name")} onChange={v => setEf("last_name", v)} />
                                                <EditField label="Email" value={ef("email")} onChange={v => setEf("email", v)} type="email" />
                                                <EditField label="Phone" value={ef("phone")} onChange={v => setEf("phone", v)} type="tel" />
                                                <EditField label="City" value={ef("city")} onChange={v => setEf("city", v)} />
                                                <div><label className="text-[11px] text-muted-foreground mb-1 block">Service Area</label><Select value={ef("service_area_id") || "none"} onValueChange={v => setEf("service_area_id", v === "none" ? "" : v)}><SelectTrigger className="h-9 text-sm"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="none">Not assigned</SelectItem>{allServiceAreas.map(a => <SelectItem key={a.id} value={a.id}>{a.name}</SelectItem>)}</SelectContent></Select></div>
                                            </div>
                                        ) : (
                                            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                                                <DetailField icon={Mail} label="Email" value={selected.email || "\u2014"} />
                                                <DetailField icon={Phone} label="Phone" value={selected.phone || "\u2014"} />
                                                <DetailField icon={MapPin} label="City" value={selected.city || "\u2014"} />
                                                <DetailField icon={MapPin} label="Service Area" value={serviceAreas.find(a => a.id === selected.service_area_id)?.name || selected.service_area_id?.slice(0, 8) || "Not assigned"} />
                                            </div>
                                        )}
                                    </DetailSection>
                                    <DetailSection title="Vehicle Information" icon={Car}>
                                        {editing ? (
                                            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                                                <EditField label="Make" value={ef("vehicle_make")} onChange={v => setEf("vehicle_make", v)} />
                                                <EditField label="Model" value={ef("vehicle_model")} onChange={v => setEf("vehicle_model", v)} />
                                                <EditField label="Color" value={ef("vehicle_color")} onChange={v => setEf("vehicle_color", v)} />
                                                <EditField label="Year" value={ef("vehicle_year")} onChange={v => setEf("vehicle_year", v)} />
                                                <EditField label="License Plate" value={ef("license_plate")} onChange={v => setEf("license_plate", v)} />
                                                <EditField label="VIN" value={ef("vehicle_vin")} onChange={v => setEf("vehicle_vin", v)} />
                                            </div>
                                        ) : (
                                            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                                                <DetailField icon={Car} label="Vehicle" value={`${selected.vehicle_color || ""} ${selected.vehicle_make || ""} ${selected.vehicle_model || ""}`.trim() || "\u2014"} />
                                                <DetailField icon={CalendarRange} label="Year" value={selected.vehicle_year || "\u2014"} />
                                                <DetailField icon={FileText} label="License Plate" value={selected.license_plate || "\u2014"} mono />
                                                <DetailField icon={FileText} label="VIN" value={selected.vehicle_vin || "\u2014"} mono />
                                            </div>
                                        )}
                                    </DetailSection>
                                    <DetailSection title="Spinr Pass" icon={CreditCard}>
                                        {selected.subscription_status === "active" ? (
                                            <div className="flex items-center gap-3 bg-violet-50 dark:bg-violet-900/20 rounded-xl p-4 border border-violet-200 dark:border-violet-800"><div className="w-10 h-10 rounded-xl bg-violet-100 dark:bg-violet-900/30 flex items-center justify-center"><CreditCard className="h-5 w-5 text-violet-600 dark:text-violet-400" /></div><div><p className="text-sm font-semibold text-violet-700 dark:text-violet-300">{selected.subscription_plan || "Active Plan"}</p><p className="text-xs text-violet-600/70 dark:text-violet-400/70">Subscription active</p></div></div>
                                        ) : (
                                            <div className="flex items-center gap-3 bg-muted/30 rounded-xl p-4"><div className="w-10 h-10 rounded-xl bg-muted flex items-center justify-center"><CreditCard className="h-5 w-5 text-muted-foreground" /></div><div><p className="text-sm font-medium text-muted-foreground">No active subscription</p></div></div>
                                        )}
                                    </DetailSection>
                                    <div className="bg-muted/30 rounded-xl p-4 space-y-2">
                                        <div className="flex items-center justify-between text-sm"><span className="text-muted-foreground">Joined</span><span className="font-medium">{fmtDate(selected.created_at)}</span></div>
                                        <div className="flex items-center justify-between text-sm"><span className="text-muted-foreground">Last updated</span><span className="font-medium">{fmtDate(selected.updated_at)}</span></div>
                                    </div>
                                </TabsContent>

                                {/* Documents */}
                                <TabsContent value="documents" className="mt-4 space-y-6">
                                    <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
                                        {docsLoading ? (
                                            <>{[1,2,3,4,5].map(i => <div key={i} className="h-24 bg-muted rounded-xl animate-pulse" />)}</>
                                        ) : requiredDocs.length > 0 ? requiredDocs.filter(rd => rd.has_expiry).map(rd => (
                                            <DocExpirySummaryCard
                                                key={rd.key}
                                                label={rd.label}
                                                expiry={_getDocExpiry(rd.id, rd.key, rd.label)}
                                            />
                                        )) : (<>
                                            <DocExpirySummaryCard label="Driver's License"    expiry={_getDocExpiry(undefined, "drivers_license",      "Driver's License")} />
                                            <DocExpirySummaryCard label="Vehicle Insurance"   expiry={_getDocExpiry(undefined, "vehicle_insurance",    "Vehicle Insurance")} />
                                            <DocExpirySummaryCard label="Vehicle Registration" expiry={_getDocExpiry(undefined, "vehicle_registration", "Vehicle Registration")} />
                                            <DocExpirySummaryCard label="Vehicle Inspection"  expiry={_getDocExpiry(undefined, "vehicle_inspection",  "Vehicle Inspection")} />
                                            <DocExpirySummaryCard label="Background Check"    expiry={_getDocExpiry(undefined, "background_check",    "Background Check")} />
                                        </>)}
                                    </div>
                                    {docsLoading ? <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">{[1,2,3,4].map(i=><div key={i} className="h-48 bg-muted rounded-xl animate-pulse" />)}</div>
                                    : requiredDocs.length > 0 ? (
                                        <div className="space-y-6">
                                            {requiredDocs.map(reqDoc => {
                                                const matchingDocs = activeDocs.filter(d => {
                                                    // 1. Best: match by requirement_id stored on the doc (set when driver uploads via /drivers/documents)
                                                    if (d.requirement_id) return d.requirement_id === reqDoc.id || d.requirement_id === reqDoc.key;
                                                    // 2. Match document_type against the label (set when driver uses become-driver flow)
                                                    const dt = (d.document_type || "").toLowerCase();
                                                    const label = reqDoc.label.toLowerCase();
                                                    const key = reqDoc.key.toLowerCase().replace(/_/g, " ");
                                                    if (dt === label || dt === key) return true;
                                                    // 3. Fuzzy fallback: key slug appears inside document_type
                                                    return dt.replace(/[^a-z0-9]/g, "_").includes(reqDoc.key.replace(/[^a-z0-9]/g, "_"));
                                                });
                                                return (
                                                    <div key={reqDoc.key}>
                                                        <div className="flex items-center gap-2 mb-3">
                                                            <FileText className="h-4 w-4 text-muted-foreground" /><h4 className="text-sm font-semibold">{reqDoc.label}</h4>
                                                            {reqDoc.has_expiry && <Badge variant="outline" className="text-[10px]">Requires Expiry</Badge>}
                                                            {matchingDocs.length > 0 ? <Badge className="bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400 text-[10px]">{matchingDocs.length} uploaded</Badge> : <Badge className="bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400 text-[10px]">Missing</Badge>}
                                                        </div>
                                                        {matchingDocs.length > 0 ? <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">{matchingDocs.map(d=><DocCard key={d.id} d={d} docBusy={docBusy} onPreview={setPreviewUrl} onReview={openReviewDialog} />)}</div>
                                                        : <div className="bg-muted/20 border border-dashed rounded-xl p-6 text-center text-muted-foreground"><Image className="h-8 w-8 mx-auto mb-2 opacity-20" /><p className="text-sm">No {reqDoc.label} uploaded yet</p></div>}
                                                    </div>
                                                );
                                            })}
                                            {/* Only show required documents — no "Other Documents" section */}
                                        </div>
                                    ) : (
                                        <div className="text-center py-12 text-muted-foreground bg-muted/20 rounded-xl border border-dashed"><Image className="h-10 w-10 mx-auto mb-3 opacity-30" /><p className="text-sm font-medium">No document requirements configured for this service area</p></div>
                                    )}
                                </TabsContent>

                                {/* Actions & Verification */}
                                <TabsContent value="verification" className="mt-4 space-y-5">
                                    <DriverActionBar driver={selected} onActionComplete={() => { loadData(); setSelected(null); }} />
                                    <DetailSection title="Verification Checklist" icon={CheckCircle}>
                                        <div className="space-y-2">
                                            {(requiredDocs.length > 0 ? requiredDocs : [
                                                { key: "drivers_license",      label: "Driver's License",    has_expiry: true },
                                                { key: "vehicle_insurance",    label: "Vehicle Insurance",   has_expiry: true },
                                                { key: "vehicle_registration", label: "Vehicle Registration",has_expiry: true },
                                                { key: "vehicle_inspection",   label: "Vehicle Inspection",  has_expiry: true },
                                                { key: "background_check",     label: "Background Check",   has_expiry: true },
                                            ]).map(rd => {
                                                const matchingDocs = activeDocs.filter(d => {
                                                    if (d.requirement_id) return d.requirement_id === rd.key;
                                                    const dt = (d.document_type || "").toLowerCase();
                                                    return dt === rd.label.toLowerCase() || dt.replace(/[^a-z0-9]/g, "_").includes(rd.key.replace(/[^a-z0-9]/g, "_"));
                                                });
                                                const hasApproved = matchingDocs.some(d => d.status === "approved");
                                                const hasPending = matchingDocs.some(d => d.status === "pending");
                                                const pendingDoc = matchingDocs.find(d => d.status === "pending");
                                                const expiryField = _docKeyToExpiryField(rd.key);
                                                const expiryVal = expiryField ? selected[expiryField] : undefined;
                                                const isExpired = expiryVal && new Date(expiryVal) < new Date();

                                                let status: "approved" | "pending" | "missing" | "expired" = "missing";
                                                if (isExpired) status = "expired";
                                                else if (hasApproved) status = "approved";
                                                else if (hasPending || matchingDocs.length > 0) status = "pending";

                                                return (
                                                    <VerificationRow
                                                        key={rd.key}
                                                        label={rd.label}
                                                        status={status}
                                                        hasExpiry={rd.has_expiry}
                                                        expiryDate={expiryVal}
                                                        pendingDocId={pendingDoc?.id}
                                                        pendingDocUrl={pendingDoc?.document_url}
                                                        docBusy={docBusy}
                                                        onApprove={(docId) => openReviewDialog(docId, "approved")}
                                                        onReject={(docId) => openReviewDialog(docId, "rejected")}
                                                        onPreview={(url) => setPreviewUrl(url)}
                                                    />
                                                );
                                            })}
                                            <CheckItem label="Profile Photo" checked={!!selected.profile_photo_url} />
                                            <CheckItem label="Vehicle Photo" checked={!!selected.vehicle_photo_url} />
                                        </div>
                                    </DetailSection>
                                </TabsContent>

                                {/* Notes */}
                                <TabsContent value="notes" className="mt-4">
                                    <DriverNotes driverId={selected.id} />
                                </TabsContent>

                                {/* History / Audit Timeline */}
                                <TabsContent value="history" className="mt-4">
                                    <DriverTimeline driverId={selected.id} driver={selected} />
                                </TabsContent>
                            </div>
                        </Tabs>
                    </>)}
                </SheetContent>
            </Sheet>

            {previewUrl && (
                <div className="fixed inset-0 z-[100] bg-black/80 flex items-center justify-center p-8 cursor-pointer" onClick={() => setPreviewUrl(null)}>
                    <div className="relative max-w-5xl max-h-[90vh] w-full h-full flex items-center justify-center">
                        <img src={previewUrl} alt="Document preview" className="max-w-full max-h-full object-contain rounded-lg shadow-2xl" onClick={e => e.stopPropagation()} />
                        <button onClick={() => setPreviewUrl(null)} className="absolute top-2 right-2 bg-black/60 hover:bg-black/80 text-white rounded-full p-2 transition"><X className="h-5 w-5" /></button>
                        <a href={previewUrl} target="_blank" rel="noreferrer" onClick={e => e.stopPropagation()} className="absolute bottom-4 right-4 bg-white/90 hover:bg-white text-gray-800 rounded-lg px-3 py-1.5 text-sm font-medium flex items-center gap-1.5 transition"><ExternalLink className="h-4 w-4" /> Open original</a>
                    </div>
                </div>
            )}

            <Dialog open={!!reviewingDoc} onOpenChange={open => { if (!open) setReviewingDoc(null); }}>
                <DialogContent className="sm:max-w-md">
                    <DialogHeader>
                        <DialogTitle>{reviewingDoc?.action === "approved" ? "Approve Document" : "Reject Document"}</DialogTitle>
                        <DialogDescription>
                            {reviewingDoc?.docType && <span className="font-semibold text-foreground">{reviewingDoc.docType}</span>}
                            {reviewingDoc?.action === "approved"
                                ? reviewingDoc?.requiresExpiry
                                    ? " — This document requires an expiry date. Set the date from the document."
                                    : " — Optionally set an expiry date."
                                : " — Provide a reason for rejection."}
                        </DialogDescription>
                    </DialogHeader>
                    <div className="space-y-4 py-2">
                        {reviewingDoc?.action === "approved" ? (
                            <div>
                                <label className="text-sm font-medium mb-1.5 block">
                                    Expiry Date {reviewingDoc?.requiresExpiry ? <span className="text-red-500">*</span> : "(optional)"}
                                </label>
                                <Input type="date" value={reviewExpiry} onChange={e => setReviewExpiry(e.target.value)} className="w-full" />
                                {reviewingDoc?.requiresExpiry && !reviewExpiry && (
                                    <p className="text-xs text-red-500 mt-1 flex items-center gap-1"><AlertTriangle className="h-3 w-3" /> Expiry date is required for this document type. This will update the driver's profile.</p>
                                )}
                                {!reviewingDoc?.requiresExpiry && <p className="text-xs text-muted-foreground mt-1">Leave empty if no expiry.</p>}
                            </div>
                        ) : (
                            <div><label className="text-sm font-medium mb-1.5 block">Reason (optional)</label><Input value={reviewReason} onChange={e => setReviewReason(e.target.value)} placeholder="e.g., Document is blurry" className="w-full" /></div>
                        )}
                    </div>
                    <DialogFooter>
                        <Button variant="outline" onClick={() => setReviewingDoc(null)}>Cancel</Button>
                        <Button
                            onClick={confirmReview}
                            disabled={reviewingDoc?.action === "approved" && reviewingDoc?.requiresExpiry && !reviewExpiry}
                            className={reviewingDoc?.action === "approved" ? "bg-emerald-600 hover:bg-emerald-700 text-white" : "bg-red-600 hover:bg-red-700 text-white"}
                        >
                            {reviewingDoc?.action === "approved" ? "Approve" : "Reject"}
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </div>
    );
}

function QuickStat({ icon: Icon, color, bg, label, value }: { icon: any; color: string; bg: string; label: string; value: string }) {
    return <div className={`${bg} rounded-xl p-3 text-center`}><Icon className={`h-4 w-4 ${color} mx-auto mb-1`} /><p className="text-sm font-bold">{value}</p><p className="text-[10px] text-muted-foreground">{label}</p></div>;
}

function DetailSection({ title, icon: Icon, children }: { title: string; icon: any; children: React.ReactNode }) {
    return <div><div className="flex items-center gap-2 mb-3"><Icon className="h-4 w-4 text-muted-foreground" /><h4 className="text-sm font-semibold">{title}</h4></div><div className="bg-muted/20 rounded-xl p-4 border border-border/50">{children}</div></div>;
}

function DetailField({ icon: Icon, label, value, mono }: { icon: any; label: string; value: string; mono?: boolean }) {
    return <div className="flex items-center gap-2.5"><Icon className="h-4 w-4 text-muted-foreground shrink-0" /><div className="min-w-0"><p className="text-[11px] text-muted-foreground">{label}</p><p className={`text-sm font-medium truncate ${mono ? "font-mono tracking-wider" : ""}`}>{value}</p></div></div>;
}

function EditField({ label, value, onChange, type = "text" }: { label: string; value: string; onChange: (v: string) => void; type?: string }) {
    return <div><label className="text-[11px] text-muted-foreground mb-1 block">{label}</label><Input type={type} value={value} onChange={e => onChange(e.target.value)} className="h-9 text-sm" /></div>;
}

function DocExpirySummaryCard({ label, expiry }: { label: string; expiry?: string }) {
    const isExpired = expiry && new Date(expiry) < new Date();
    const daysUntil = expiry ? Math.ceil((new Date(expiry).getTime() - Date.now()) / 86400000) : null;
    const isExpiringSoon = daysUntil !== null && daysUntil > 0 && daysUntil <= 30;
    const fmt = (d: string) => { try { return new Date(d).toLocaleDateString("en-CA", { month: "short", day: "numeric", year: "numeric" }); } catch { return d; } };
    return (
        <div className={`rounded-xl p-3 border ${!expiry ? "bg-muted/30 border-border" : isExpired ? "bg-red-50 dark:bg-red-900/10 border-red-200 dark:border-red-800" : isExpiringSoon ? "bg-amber-50 dark:bg-amber-900/10 border-amber-200 dark:border-amber-800" : "bg-emerald-50 dark:bg-emerald-900/10 border-emerald-200 dark:border-emerald-800"}`}>
            <div className="flex items-center gap-2 mb-1"><div className={`w-2 h-2 rounded-full ${!expiry ? "bg-gray-300" : isExpired ? "bg-red-500" : isExpiringSoon ? "bg-amber-500" : "bg-emerald-500"}`} /><p className="text-xs font-medium text-muted-foreground">{label}</p></div>
            {expiry ? (<><p className={`text-sm font-bold ${isExpired ? "text-red-600" : isExpiringSoon ? "text-amber-600" : "text-emerald-600"}`}>{isExpired ? "EXPIRED" : fmt(expiry)}</p>{!isExpired && daysUntil !== null && <p className={`text-[10px] mt-0.5 ${isExpiringSoon ? "text-amber-500" : "text-muted-foreground"}`}>{daysUntil} day{daysUntil !== 1 ? "s" : ""} remaining</p>}</>) : <p className="text-sm font-medium text-muted-foreground">Not set</p>}
        </div>
    );
}

function DocCard({ d, docBusy, onPreview, onReview }: { d: any; docBusy: string | null; onPreview: (url: string) => void; onReview: (id: string, action: "approved" | "rejected") => void }) {
    const exp = d.expiry_date || d.expires_at;
    const expired = exp && new Date(exp) < new Date();
    const isImage = d.document_url && /\.(jpg|jpeg|png|gif|webp|bmp|svg)(\?|$)/i.test(d.document_url);
    const sc = d.status === "approved" && !expired ? "emerald" : d.status === "rejected" ? "red" : expired ? "red" : "amber";
    return (
        <div className="bg-card rounded-xl border overflow-hidden transition hover:shadow-md group">
            <div className="relative h-44 bg-muted/50 flex items-center justify-center overflow-hidden">
                {isImage ? (<><img src={d.document_url} alt={d.document_type||"Document"} className="w-full h-full object-cover" onError={e=>{(e.target as HTMLImageElement).style.display='none';}} /><button onClick={()=>onPreview(d.document_url)} className="absolute inset-0 bg-black/0 group-hover:bg-black/30 transition flex items-center justify-center opacity-0 group-hover:opacity-100"><div className="bg-white/90 rounded-full p-2"><ZoomIn className="h-5 w-5 text-gray-800" /></div></button></>)
                : d.document_url ? <a href={d.document_url} target="_blank" rel="noreferrer" className="flex flex-col items-center gap-2 text-muted-foreground hover:text-foreground transition"><FileText className="h-12 w-12 opacity-40" /><span className="text-xs font-medium">Click to view</span></a>
                : <div className="flex flex-col items-center gap-2 text-muted-foreground"><Image className="h-12 w-12 opacity-20" /><span className="text-xs">No file</span></div>}
                <div className="absolute top-2 right-2"><Badge className={`text-[10px] shadow-sm ${sc==="emerald"?"bg-emerald-500 text-white":sc==="red"?"bg-red-500 text-white":"bg-amber-500 text-white"}`}>{expired&&d.status==="approved"?"EXPIRED":d.status?.toUpperCase()}</Badge></div>
                {d.side && <div className="absolute top-2 left-2"><Badge variant="secondary" className="text-[10px] shadow-sm bg-black/60 text-white border-none">{d.side}</Badge></div>}
            </div>
            <div className="p-3 space-y-2">
                <p className="text-sm font-semibold truncate">{d.document_type||"Document"}{d.side?` (${d.side})`:""}</p>
                <div className="space-y-1">
                    {d.created_at && <p className="text-[11px] text-muted-foreground flex items-center gap-1"><CalendarRange className="h-3 w-3" />Uploaded: {new Date(d.created_at).toLocaleDateString("en-CA",{month:"short",day:"numeric",year:"numeric"})}</p>}
                    {exp && <p className={`text-[11px] flex items-center gap-1 ${expired?"text-red-500 font-medium":"text-muted-foreground"}`}><Clock className="h-3 w-3" />Expires: {new Date(exp).toLocaleDateString("en-CA",{month:"short",day:"numeric",year:"numeric"})}{expired&&" (EXPIRED)"}</p>}
                </div>
                {d.rejection_reason && <p className="text-[11px] text-red-500 bg-red-50 dark:bg-red-900/20 rounded-lg px-2 py-1"><AlertTriangle className="h-3 w-3 inline mr-1" />{d.rejection_reason}</p>}
                <div className="flex items-center gap-1.5 pt-1">
                    <Button variant="outline" size="xs" className="flex-1 text-emerald-600 border-emerald-200 hover:bg-emerald-50" disabled={docBusy===d.id} onClick={()=>onReview(d.id,"approved")}><CheckCircle className="h-3 w-3" /> Approve</Button>
                    <Button variant="outline" size="xs" className="flex-1 text-red-600 border-red-200 hover:bg-red-50" disabled={docBusy===d.id} onClick={()=>onReview(d.id,"rejected")}><XCircle className="h-3 w-3" /> Reject</Button>
                </div>
            </div>
        </div>
    );
}

function CheckItem({ label, checked, expired, status }: { label: string; checked?: boolean; expired?: boolean; status?: "approved" | "pending" | "missing" | "expired" }) {
    const s = status || (expired ? "expired" : checked ? "approved" : "missing");
    const config = {
        approved: { bg: "bg-emerald-50 dark:bg-emerald-900/10 border-emerald-200 dark:border-emerald-800", icon: <CheckCircle className="h-4 w-4 text-emerald-500" />, text: "text-emerald-600", label: "Approved" },
        pending:  { bg: "bg-amber-50 dark:bg-amber-900/10 border-amber-200 dark:border-amber-800", icon: <Clock className="h-4 w-4 text-amber-500" />, text: "text-amber-600", label: "Pending Review" },
        expired:  { bg: "bg-red-50 dark:bg-red-900/10 border-red-200 dark:border-red-800", icon: <AlertTriangle className="h-4 w-4 text-red-500" />, text: "text-red-500", label: "Expired" },
        missing:  { bg: "bg-muted/30 border-border", icon: <div className="w-4 h-4 rounded-full border-2 border-muted-foreground/30" />, text: "text-muted-foreground", label: "Missing" },
    }[s];
    return (
        <div className={`flex items-center justify-between p-3 rounded-lg border ${config.bg}`}>
            <div className="flex items-center gap-2">{config.icon}<span className="text-sm font-medium">{label}</span></div>
            <span className={`text-xs font-medium ${config.text}`}>{config.label}</span>
        </div>
    );
}

function VerificationRow({ label, status, hasExpiry, expiryDate, pendingDocId, pendingDocUrl, docBusy, onApprove, onReject, onPreview }: {
    label: string;
    status: "approved" | "pending" | "missing" | "expired";
    hasExpiry: boolean;
    expiryDate?: string;
    pendingDocId?: string;
    pendingDocUrl?: string;
    docBusy: string | null;
    onApprove: (docId: string) => void;
    onReject: (docId: string) => void;
    onPreview: (url: string) => void;
}) {
    const fmtDate = (d: string) => { try { return new Date(d).toLocaleDateString("en-CA", { month: "short", day: "numeric", year: "numeric" }); } catch { return d; } };
    const isImage = pendingDocUrl && /\.(jpg|jpeg|png|gif|webp|bmp|svg)(\?|$)/i.test(pendingDocUrl);

    const statusConfig = {
        approved: { bg: "bg-emerald-50 dark:bg-emerald-900/10 border-emerald-200 dark:border-emerald-800", icon: <CheckCircle className="h-5 w-5 text-emerald-500" />, badgeBg: "bg-emerald-100 text-emerald-700", statusLabel: "Approved" },
        pending:  { bg: "bg-amber-50 dark:bg-amber-900/10 border-amber-200 dark:border-amber-800", icon: <Clock className="h-5 w-5 text-amber-500" />, badgeBg: "bg-amber-100 text-amber-700", statusLabel: "Pending Review" },
        expired:  { bg: "bg-red-50 dark:bg-red-900/10 border-red-200 dark:border-red-800", icon: <AlertTriangle className="h-5 w-5 text-red-500" />, badgeBg: "bg-red-100 text-red-700", statusLabel: "Expired" },
        missing:  { bg: "bg-muted/30 border-border", icon: <div className="w-5 h-5 rounded-full border-2 border-muted-foreground/30" />, badgeBg: "bg-muted text-muted-foreground", statusLabel: "Missing" },
    }[status];

    return (
        <div className={`rounded-xl border p-4 ${statusConfig.bg}`}>
            <div className="flex items-start gap-3">
                <div className="mt-0.5 shrink-0">{statusConfig.icon}</div>
                <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                        <span className="text-sm font-semibold">{label}</span>
                        <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-bold ${statusConfig.badgeBg}`}>{statusConfig.statusLabel}</span>
                    </div>

                    {/* Expiry info */}
                    {hasExpiry && (
                        <div className="mt-1">
                            {expiryDate ? (
                                <p className={`text-xs flex items-center gap-1 ${status === "expired" ? "text-red-500 font-medium" : "text-muted-foreground"}`}>
                                    <CalendarRange className="h-3 w-3" />
                                    Expires: {fmtDate(expiryDate)}
                                    {status === "expired" && " (EXPIRED)"}
                                </p>
                            ) : status !== "missing" ? (
                                <p className="text-xs text-amber-600 flex items-center gap-1"><AlertTriangle className="h-3 w-3" /> No expiry date set — set one when approving</p>
                            ) : null}
                        </div>
                    )}

                    {/* Pending doc preview + actions */}
                    {status === "pending" && pendingDocId && (
                        <div className="mt-3 flex items-center gap-2">
                            {pendingDocUrl && (
                                <button onClick={() => onPreview(pendingDocUrl)} className="flex items-center gap-1.5 text-xs text-primary hover:underline font-medium">
                                    {isImage ? <ZoomIn className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
                                    Preview
                                </button>
                            )}
                            <div className="flex-1" />
                            <Button variant="outline" size="xs" className="text-emerald-600 border-emerald-300 hover:bg-emerald-50" disabled={docBusy === pendingDocId} onClick={() => onApprove(pendingDocId)}>
                                <CheckCircle className="h-3 w-3" /> Approve
                            </Button>
                            <Button variant="outline" size="xs" className="text-red-600 border-red-300 hover:bg-red-50" disabled={docBusy === pendingDocId} onClick={() => onReject(pendingDocId)}>
                                <XCircle className="h-3 w-3" /> Reject
                            </Button>
                        </div>
                    )}

                    {/* Missing — hint */}
                    {status === "missing" && (
                        <p className="text-xs text-muted-foreground mt-1">Driver has not uploaded this document yet.</p>
                    )}
                </div>
            </div>
        </div>
    );
}
