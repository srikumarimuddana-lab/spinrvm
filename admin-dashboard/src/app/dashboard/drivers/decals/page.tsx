"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
    Search, Users, Loader2, CheckCircle, Clock, AlertTriangle, FileText,
    Download, Car, Mail, RefreshCw, ChevronLeft, ChevronRight, MapPin, Filter,
} from "lucide-react";
import { getDrivers, getServiceAreas, generateDecalPdf } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useToast } from "@/components/ui/use-toast";
import { useRequireModule } from "@/hooks/useRequireModule";
import { exportToCsv } from "@/lib/export-csv";

type LetterFilter = "all" | "needs_letter" | "generated" | "sent";
type DriverStatus = "all" | "active" | "pending" | "suspended" | "banned";

interface ServiceArea {
    id: string;
    name: string;
}

const FILTER_TABS: { value: LetterFilter; label: string; icon: any }[] = [
    { value: "all", label: "All Drivers", icon: Users },
    { value: "needs_letter", label: "Needs Letter", icon: AlertTriangle },
    { value: "generated", label: "Generated", icon: FileText },
    { value: "sent", label: "Sent", icon: CheckCircle },
];

const STATUS_OPTIONS: { value: DriverStatus; label: string }[] = [
    { value: "all", label: "All Statuses" },
    { value: "active", label: "Approved" },
    { value: "pending", label: "Pending" },
    { value: "suspended", label: "Suspended" },
    { value: "banned", label: "Banned" },
];

const PAGE_SIZE = 25;

const fmtDate = (iso: string | null | undefined) => {
    if (!iso) return "—";
    try {
        return new Date(iso).toLocaleDateString("en-CA", {
            month: "short", day: "numeric", year: "numeric",
        });
    } catch { return iso; }
};

const initials = (first: string, last: string) => {
    const f = (first || "")[0] || "";
    const l = (last || "")[0] || "";
    return (f + l).toUpperCase() || "?";
};

export default function WelcomeLettersPage() {
    const { allowed } = useRequireModule("drivers");
    const { toast } = useToast();
    const [drivers, setDrivers] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [refreshing, setRefreshing] = useState(false);
    const [search, setSearch] = useState("");
    const [filter, setFilter] = useState<LetterFilter>("all");
    const [driverStatus, setDriverStatus] = useState<DriverStatus>("all");
    const [serviceAreaId, setServiceAreaId] = useState("all");
    const [serviceAreas, setServiceAreas] = useState<ServiceArea[]>([]);
    const [dateFrom, setDateFrom] = useState("");
    const [dateTo, setDateTo] = useState("");
    const [page, setPage] = useState(1);
    const [generatingId, setGeneratingId] = useState<string | null>(null);
    const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
    const [province, setProvince] = useState<"SK" | "AB">("SK");

    useEffect(() => {
        getServiceAreas()
            .then((data) => setServiceAreas((data || []).map((a: any) => ({ id: a.id, name: a.name || a.city || a.id }))))
            .catch(() => setServiceAreas([]));
    }, []);

    const load = useCallback(async () => {
        try {
            setRefreshing(true);
            const opts: Record<string, any> = { limit: 500 };
            if (driverStatus !== "all") opts.status = driverStatus;
            if (serviceAreaId !== "all") opts.service_area_id = serviceAreaId;
            const res = await getDrivers(opts);
            const list = Array.isArray(res) ? res : (res as any)?.drivers || [];
            setDrivers(list);
        } catch (e: any) {
            toast({ title: "Failed to load drivers", description: e?.message, variant: "destructive" });
        } finally {
            setLoading(false);
            setRefreshing(false);
        }
    }, [toast, driverStatus, serviceAreaId]);

    useEffect(() => { if (allowed) load(); }, [allowed, load]);

    useEffect(() => { setPage(1); setSelectedIds(new Set()); }, [filter, driverStatus, serviceAreaId, search, dateFrom, dateTo]);

    const filtered = useMemo(() => {
        let list = drivers;

        if (filter === "needs_letter") {
            list = list.filter(d => !d.decal_generated_at && !d.decals_sent);
        } else if (filter === "generated") {
            list = list.filter(d => d.decal_generated_at && !d.decals_sent);
        } else if (filter === "sent") {
            list = list.filter(d => d.decals_sent);
        }

        if (dateFrom) {
            const from = new Date(dateFrom);
            list = list.filter(d => {
                if (!d.decal_generated_at) return false;
                return new Date(d.decal_generated_at) >= from;
            });
        }
        if (dateTo) {
            const to = new Date(dateTo + "T23:59:59");
            list = list.filter(d => {
                if (!d.decal_generated_at) return false;
                return new Date(d.decal_generated_at) <= to;
            });
        }

        if (search.trim()) {
            const q = search.toLowerCase().trim();
            list = list.filter(d => {
                const name = `${d.first_name || ""} ${d.last_name || ""}`.toLowerCase();
                const plate = (d.license_plate || "").toLowerCase();
                const code = (d.driver_code || "").toLowerCase();
                const refNum = (d.decal_number || "").toLowerCase();
                return name.includes(q) || plate.includes(q) || code.includes(q) || refNum.includes(q);
            });
        }

        return list;
    }, [drivers, filter, search, dateFrom, dateTo]);

    const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
    const paged = useMemo(() => {
        const start = (page - 1) * PAGE_SIZE;
        return filtered.slice(start, start + PAGE_SIZE);
    }, [filtered, page]);

    const counts = useMemo(() => ({
        all: drivers.length,
        needs_letter: drivers.filter(d => !d.decal_generated_at && !d.decals_sent).length,
        generated: drivers.filter(d => d.decal_generated_at && !d.decals_sent).length,
        sent: drivers.filter(d => d.decals_sent).length,
    }), [drivers]);

    const downloadBlob = (blob: Blob, filename: string) => {
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(url);
    };

    const handleGenerate = async (driver: any) => {
        setGeneratingId(driver.id);
        try {
            const blob = await generateDecalPdf([driver.id], province);
            const now = new Date().toISOString();
            setDrivers(prev => prev.map(d =>
                d.id === driver.id
                    ? { ...d, decal_generated_at: d.decal_generated_at || now, decal_number: d.decal_number || `SPR-${new Date().getFullYear()}-pending` }
                    : d
            ));
            const filename = `welcome_letter_${driver.first_name}_${driver.last_name}.pdf`.replace(/\s+/g, "_");
            downloadBlob(blob, filename);
            toast({
                title: "Welcome letter downloaded",
                description: `Welcome letter generated for ${driver.first_name} ${driver.last_name}.`,
            });
            await load();
        } catch (e: any) {
            toast({ title: "Failed to generate welcome letter", description: e?.message, variant: "destructive" });
        } finally {
            setGeneratingId(null);
        }
    };

    const handleBulkGenerate = async () => {
        const targets = filtered.filter(d => selectedIds.has(d.id) && !d.decal_generated_at);
        if (!targets.length) {
            toast({ title: "No eligible drivers selected", description: "Select drivers that haven't had welcome letters generated yet.", variant: "destructive" });
            return;
        }
        setGeneratingId("bulk");
        try {
            const ids = targets.map(d => d.id);
            const blob = await generateDecalPdf(ids, province);
            downloadBlob(blob, `welcome_letters_${ids.length}_drivers.pdf`);
            toast({
                title: "Welcome letters downloaded",
                description: `Generated welcome letters for ${ids.length} driver(s).`,
            });
            await load();
        } catch (e: any) {
            toast({ title: "Failed to generate welcome letters", description: e?.message, variant: "destructive" });
        } finally {
            setGeneratingId(null);
            setSelectedIds(new Set());
        }
    };

    const toggleSelect = (id: string) => {
        setSelectedIds(prev => {
            const next = new Set(prev);
            if (next.has(id)) next.delete(id);
            else next.add(id);
            return next;
        });
    };

    const toggleSelectAll = () => {
        if (selectedIds.size === paged.length) {
            setSelectedIds(new Set());
        } else {
            setSelectedIds(new Set(paged.map(d => d.id)));
        }
    };

    const handleExport = () => {
        exportToCsv("welcome-letter-report", filtered, [
            { key: "driver_code", label: "Driver Code" },
            { key: "first_name", label: "First Name" },
            { key: "last_name", label: "Last Name" },
            { key: "phone", label: "Phone" },
            { key: "vehicle_make", label: "Vehicle Make" },
            { key: "vehicle_model", label: "Vehicle Model" },
            { key: "vehicle_year", label: "Vehicle Year" },
            { key: "vehicle_color", label: "Vehicle Color" },
            { key: "license_plate", label: "License Plate" },
            { key: "decal_number", label: "Ref Number" },
            { key: "decal_generated_at", label: "Generated At" },
            { key: "decals_sent", label: "Sent" },
            { key: "decals_sent_at", label: "Sent At" },
        ]);
    };

    const letterStatus = (d: any) => {
        if (d.decals_sent) return { label: "Sent", variant: "default" as const, icon: CheckCircle };
        if (d.decal_generated_at) return { label: "Generated", variant: "secondary" as const, icon: FileText };
        return { label: "Pending", variant: "outline" as const, icon: Clock };
    };

    if (!allowed) return null;

    return (
        <div className="p-4 sm:p-6 space-y-6 max-w-[1400px] mx-auto">
            <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
                <div>
                    <h1 className="text-2xl font-bold tracking-tight flex items-center gap-2">
                        <Mail className="h-6 w-6 text-primary" />
                        Welcome Letters
                    </h1>
                    <p className="text-sm text-muted-foreground mt-1">
                        Generate and track welcome letters for drivers
                    </p>
                </div>
                <div className="flex items-center gap-2">
                    <Button variant="outline" size="sm" onClick={load} disabled={refreshing}>
                        {refreshing ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
                        Refresh
                    </Button>
                    <Button variant="outline" size="sm" onClick={handleExport} disabled={filtered.length === 0}>
                        <Download className="h-4 w-4" /> Export
                    </Button>
                    {selectedIds.size > 0 && (
                        <Button size="sm" onClick={handleBulkGenerate} disabled={!!generatingId}>
                            {generatingId === "bulk" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
                            Download PDF ({selectedIds.size})
                        </Button>
                    )}
                </div>
            </div>

            {/* Summary cards */}
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                {FILTER_TABS.map(tab => (
                    <button
                        key={tab.value}
                        onClick={() => setFilter(tab.value)}
                        className={`flex items-center gap-3 p-4 rounded-xl border transition text-left ${
                            filter === tab.value
                                ? "bg-primary/10 border-primary/30 ring-1 ring-primary/20"
                                : "bg-card hover:bg-muted/50"
                        }`}
                    >
                        <tab.icon className={`h-5 w-5 ${filter === tab.value ? "text-primary" : "text-muted-foreground"}`} />
                        <div>
                            <p className="text-xl font-bold">{counts[tab.value]}</p>
                            <p className="text-xs text-muted-foreground">{tab.label}</p>
                        </div>
                    </button>
                ))}
            </div>

            {/* Filters row */}
            <div className="flex flex-wrap items-end gap-3">
                <div className="relative w-full sm:w-64">
                    <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                    <Input
                        placeholder="Search name, plate, code, ref #..."
                        value={search}
                        onChange={e => setSearch(e.target.value)}
                        className="pl-9 h-9 text-sm"
                    />
                </div>

                <Select value={driverStatus} onValueChange={v => setDriverStatus(v as DriverStatus)}>
                    <SelectTrigger className="w-40 h-9" aria-label="Filter by driver status">
                        <div className="flex items-center gap-1.5">
                            <Filter className="h-3.5 w-3.5 text-muted-foreground" />
                            <SelectValue placeholder="All Statuses" />
                        </div>
                    </SelectTrigger>
                    <SelectContent>
                        {STATUS_OPTIONS.map(opt => (
                            <SelectItem key={opt.value} value={opt.value}>{opt.label}</SelectItem>
                        ))}
                    </SelectContent>
                </Select>

                <Select value={serviceAreaId} onValueChange={setServiceAreaId}>
                    <SelectTrigger className="w-44 h-9" aria-label="Filter by service area">
                        <div className="flex items-center gap-1.5">
                            <MapPin className="h-3.5 w-3.5 text-muted-foreground" />
                            <SelectValue placeholder="All Areas" />
                        </div>
                    </SelectTrigger>
                    <SelectContent>
                        <SelectItem value="all">All Service Areas</SelectItem>
                        {serviceAreas.map(a => (
                            <SelectItem key={a.id} value={a.id}>{a.name}</SelectItem>
                        ))}
                    </SelectContent>
                </Select>

                <Select value={province} onValueChange={v => setProvince(v as "SK" | "AB")}>
                    <SelectTrigger className="w-44 h-9" aria-label="Letter province template">
                        <div className="flex items-center gap-1.5">
                            <FileText className="h-3.5 w-3.5 text-muted-foreground" />
                            <SelectValue placeholder="Province" />
                        </div>
                    </SelectTrigger>
                    <SelectContent>
                        <SelectItem value="SK">Saskatchewan</SelectItem>
                        <SelectItem value="AB">Alberta</SelectItem>
                    </SelectContent>
                </Select>

                <div className="flex items-center gap-2">
                    <div className="space-y-0.5">
                        <label className="text-[10px] font-medium text-muted-foreground uppercase tracking-wider">From</label>
                        <Input
                            type="date"
                            value={dateFrom}
                            onChange={e => setDateFrom(e.target.value)}
                            className="h-9 w-36 text-sm"
                        />
                    </div>
                    <div className="space-y-0.5">
                        <label className="text-[10px] font-medium text-muted-foreground uppercase tracking-wider">To</label>
                        <Input
                            type="date"
                            value={dateTo}
                            onChange={e => setDateTo(e.target.value)}
                            className="h-9 w-36 text-sm"
                        />
                    </div>
                    {(dateFrom || dateTo) && (
                        <Button variant="ghost" size="sm" className="h-9 mt-3.5 text-xs" onClick={() => { setDateFrom(""); setDateTo(""); }}>
                            Clear
                        </Button>
                    )}
                </div>
            </div>

            {/* Table */}
            <div className="bg-card border rounded-2xl overflow-hidden shadow-sm">
                <Table>
                    <TableHeader>
                        <TableRow className="bg-muted/50 hover:bg-muted/50 border-b-0">
                            <TableHead className="h-11 w-10 pl-4">
                                <input
                                    type="checkbox"
                                    checked={paged.length > 0 && selectedIds.size === paged.length}
                                    onChange={toggleSelectAll}
                                    className="rounded border-border"
                                />
                            </TableHead>
                            <TableHead className="h-11"><span className="text-[11px] font-semibold text-foreground/80 uppercase tracking-wider">Driver</span></TableHead>
                            <TableHead className="h-11"><span className="text-[11px] font-semibold text-foreground/80 uppercase tracking-wider">Vehicle</span></TableHead>
                            <TableHead className="h-11"><span className="text-[11px] font-semibold text-foreground/80 uppercase tracking-wider">License Plate</span></TableHead>
                            <TableHead className="h-11"><span className="text-[11px] font-semibold text-foreground/80 uppercase tracking-wider">Ref #</span></TableHead>
                            <TableHead className="h-11"><span className="text-[11px] font-semibold text-foreground/80 uppercase tracking-wider">Status</span></TableHead>
                            <TableHead className="h-11"><span className="text-[11px] font-semibold text-foreground/80 uppercase tracking-wider">Generated</span></TableHead>
                            <TableHead className="h-11"><span className="text-[11px] font-semibold text-foreground/80 uppercase tracking-wider">Sent</span></TableHead>
                            <TableHead className="h-11 pr-5"><span className="text-[11px] font-semibold text-foreground/80 uppercase tracking-wider">Action</span></TableHead>
                        </TableRow>
                    </TableHeader>
                    <TableBody>
                        {loading ? Array.from({ length: 5 }).map((_, i) => (
                            <TableRow key={i} className="animate-pulse">
                                <TableCell><div className="h-4 w-4 bg-muted rounded" /></TableCell>
                                <TableCell><div className="flex items-center gap-3"><div className="w-9 h-9 rounded-full bg-muted" /><div className="space-y-1.5"><div className="h-3 w-24 bg-muted rounded" /><div className="h-2 w-16 bg-muted rounded" /></div></div></TableCell>
                                <TableCell><div className="h-3 w-28 bg-muted rounded" /></TableCell>
                                <TableCell><div className="h-3 w-20 bg-muted rounded" /></TableCell>
                                <TableCell><div className="h-3 w-24 bg-muted rounded" /></TableCell>
                                <TableCell><div className="h-5 w-16 bg-muted rounded-full" /></TableCell>
                                <TableCell><div className="h-3 w-20 bg-muted rounded" /></TableCell>
                                <TableCell><div className="h-3 w-20 bg-muted rounded" /></TableCell>
                                <TableCell><div className="h-7 w-20 bg-muted rounded" /></TableCell>
                            </TableRow>
                        )) : paged.length === 0 ? (
                            <TableRow>
                                <TableCell colSpan={9} className="text-center py-16 text-muted-foreground">
                                    <Mail className="h-10 w-10 mx-auto mb-3 opacity-20" />
                                    <p className="text-base font-medium">No drivers found</p>
                                    <p className="text-sm mt-1">Try adjusting your search or filters</p>
                                </TableCell>
                            </TableRow>
                        ) : paged.map(driver => {
                            const st = letterStatus(driver);
                            const vehicle = [driver.vehicle_year, driver.vehicle_make, driver.vehicle_model].filter(Boolean).join(" ");
                            return (
                                <TableRow key={driver.id} className="group hover:bg-muted/40 transition-colors">
                                    <TableCell className="pl-4">
                                        <input
                                            type="checkbox"
                                            checked={selectedIds.has(driver.id)}
                                            onChange={() => toggleSelect(driver.id)}
                                            className="rounded border-border"
                                        />
                                    </TableCell>
                                    <TableCell className="py-3">
                                        <div className="flex items-center gap-3">
                                            <div className="w-9 h-9 rounded-full bg-gradient-to-br from-primary/20 to-primary/5 flex items-center justify-center text-xs font-bold text-primary ring-1 ring-border shadow-sm">
                                                {initials(driver.first_name, driver.last_name)}
                                            </div>
                                            <div className="min-w-0">
                                                <p className="text-sm font-semibold truncate">{driver.first_name} {driver.last_name}</p>
                                                {driver.driver_code && <p className="text-[11px] font-mono text-muted-foreground">{driver.driver_code}</p>}
                                            </div>
                                        </div>
                                    </TableCell>
                                    <TableCell>
                                        <div className="flex items-center gap-1.5 text-sm text-muted-foreground">
                                            <Car className="h-3.5 w-3.5 shrink-0" />
                                            <span className="truncate max-w-[200px]">{vehicle || "—"}</span>
                                        </div>
                                        {driver.vehicle_color && (
                                            <p className="text-[11px] text-muted-foreground/70 mt-0.5">{driver.vehicle_color}</p>
                                        )}
                                    </TableCell>
                                    <TableCell>
                                        <span className="font-mono text-sm">{driver.license_plate || "—"}</span>
                                    </TableCell>
                                    <TableCell>
                                        <span className="font-mono text-sm text-muted-foreground">{driver.decal_number || "—"}</span>
                                    </TableCell>
                                    <TableCell>
                                        <Badge variant={st.variant} className="gap-1 text-xs">
                                            <st.icon className="h-3 w-3" />{st.label}
                                        </Badge>
                                    </TableCell>
                                    <TableCell>
                                        <span className="text-sm text-muted-foreground">{fmtDate(driver.decal_generated_at)}</span>
                                    </TableCell>
                                    <TableCell>
                                        <span className="text-sm text-muted-foreground">{fmtDate(driver.decals_sent_at)}</span>
                                    </TableCell>
                                    <TableCell className="pr-5">
                                        {!driver.decal_generated_at ? (
                                            <Button
                                                size="sm"
                                                variant="default"
                                                className="h-7 text-xs"
                                                disabled={!!generatingId}
                                                onClick={() => handleGenerate(driver)}
                                            >
                                                {generatingId === driver.id ? (
                                                    <Loader2 className="h-3 w-3 animate-spin" />
                                                ) : (
                                                    <Download className="h-3 w-3" />
                                                )}
                                                Generate
                                            </Button>
                                        ) : (
                                            <span className="text-xs text-muted-foreground flex items-center gap-1">
                                                <CheckCircle className="h-3 w-3 text-emerald-500" /> Done
                                            </span>
                                        )}
                                    </TableCell>
                                </TableRow>
                            );
                        })}
                    </TableBody>
                </Table>
            </div>

            {/* Pagination */}
            {!loading && filtered.length > 0 && (
                <div className="flex items-center justify-between">
                    <p className="text-xs text-muted-foreground">
                        Showing {(page - 1) * PAGE_SIZE + 1}–{Math.min(page * PAGE_SIZE, filtered.length)} of {filtered.length} drivers
                    </p>
                    <div className="flex items-center gap-1">
                        <Button
                            variant="outline"
                            size="sm"
                            className="h-8 w-8 p-0"
                            disabled={page <= 1}
                            onClick={() => setPage(p => p - 1)}
                        >
                            <ChevronLeft className="h-4 w-4" />
                        </Button>
                        {Array.from({ length: Math.min(totalPages, 7) }, (_, i) => {
                            let p: number;
                            if (totalPages <= 7) {
                                p = i + 1;
                            } else if (page <= 4) {
                                p = i + 1;
                            } else if (page >= totalPages - 3) {
                                p = totalPages - 6 + i;
                            } else {
                                p = page - 3 + i;
                            }
                            return (
                                <Button
                                    key={p}
                                    variant={p === page ? "default" : "outline"}
                                    size="sm"
                                    className="h-8 w-8 p-0 text-xs"
                                    onClick={() => setPage(p)}
                                >
                                    {p}
                                </Button>
                            );
                        })}
                        <Button
                            variant="outline"
                            size="sm"
                            className="h-8 w-8 p-0"
                            disabled={page >= totalPages}
                            onClick={() => setPage(p => p + 1)}
                        >
                            <ChevronRight className="h-4 w-4" />
                        </Button>
                    </div>
                </div>
            )}
        </div>
    );
}
