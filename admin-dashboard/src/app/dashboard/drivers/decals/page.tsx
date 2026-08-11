"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
    Search, Users, Loader2, CheckCircle, Clock, AlertTriangle, FileText,
    Download, Car, Phone, Sticker, RefreshCw,
} from "lucide-react";
import { getDrivers, updateDriver } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useToast } from "@/components/ui/use-toast";
import { useRequireModule } from "@/hooks/useRequireModule";
import { exportToCsv } from "@/lib/export-csv";

type DecalFilter = "all" | "needs_decal" | "generated" | "sent";

const FILTER_TABS: { value: DecalFilter; label: string; icon: any }[] = [
    { value: "all", label: "All Drivers", icon: Users },
    { value: "needs_decal", label: "Needs Decal", icon: AlertTriangle },
    { value: "generated", label: "Generated", icon: FileText },
    { value: "sent", label: "Sent", icon: CheckCircle },
];

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

function generateDecalNumber(): string {
    const year = new Date().getFullYear();
    const seq = String(Math.floor(Math.random() * 99999) + 1).padStart(5, "0");
    return `SPR-${year}-${seq}`;
}

export default function DecalsPage() {
    const { allowed } = useRequireModule("drivers");
    const { toast } = useToast();
    const [drivers, setDrivers] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [refreshing, setRefreshing] = useState(false);
    const [search, setSearch] = useState("");
    const [filter, setFilter] = useState<DecalFilter>("all");
    const [generatingId, setGeneratingId] = useState<string | null>(null);
    const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());

    const load = useCallback(async () => {
        try {
            setRefreshing(true);
            const res = await getDrivers({ limit: 500, status: "active" });
            const list = Array.isArray(res) ? res : (res as any)?.drivers || [];
            setDrivers(list);
        } catch (e: any) {
            toast({ title: "Failed to load drivers", description: e?.message, variant: "destructive" });
        } finally {
            setLoading(false);
            setRefreshing(false);
        }
    }, [toast]);

    useEffect(() => { if (allowed) load(); }, [allowed, load]);

    const filtered = useMemo(() => {
        let list = drivers;

        if (filter === "needs_decal") {
            list = list.filter(d => !d.decal_generated_at && !d.decals_sent);
        } else if (filter === "generated") {
            list = list.filter(d => d.decal_generated_at && !d.decals_sent);
        } else if (filter === "sent") {
            list = list.filter(d => d.decals_sent);
        }

        if (search.trim()) {
            const q = search.toLowerCase().trim();
            list = list.filter(d => {
                const name = `${d.first_name || ""} ${d.last_name || ""}`.toLowerCase();
                const plate = (d.license_plate || "").toLowerCase();
                const code = (d.driver_code || "").toLowerCase();
                const decalNum = (d.decal_number || "").toLowerCase();
                return name.includes(q) || plate.includes(q) || code.includes(q) || decalNum.includes(q);
            });
        }

        return list;
    }, [drivers, filter, search]);

    const counts = useMemo(() => ({
        all: drivers.length,
        needs_decal: drivers.filter(d => !d.decal_generated_at && !d.decals_sent).length,
        generated: drivers.filter(d => d.decal_generated_at && !d.decals_sent).length,
        sent: drivers.filter(d => d.decals_sent).length,
    }), [drivers]);

    const handleGenerate = async (driver: any) => {
        setGeneratingId(driver.id);
        try {
            const decalNumber = driver.decal_number || generateDecalNumber();
            const now = new Date().toISOString();
            await updateDriver(driver.id, {
                decal_generated_at: now,
                decal_number: decalNumber,
            });
            setDrivers(prev => prev.map(d =>
                d.id === driver.id
                    ? { ...d, decal_generated_at: now, decal_number: decalNumber }
                    : d
            ));
            toast({
                title: "Decal generated",
                description: `Decal ${decalNumber} generated for ${driver.first_name} ${driver.last_name}. Upload your Word template to fill and download.`,
            });
        } catch (e: any) {
            toast({ title: "Failed to generate decal", description: e?.message, variant: "destructive" });
        } finally {
            setGeneratingId(null);
        }
    };

    const handleBulkGenerate = async () => {
        const targets = filtered.filter(d => selectedIds.has(d.id) && !d.decal_generated_at);
        if (!targets.length) {
            toast({ title: "No eligible drivers selected", description: "Select drivers that haven't had decals generated yet.", variant: "destructive" });
            return;
        }
        for (const driver of targets) {
            await handleGenerate(driver);
        }
        setSelectedIds(new Set());
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
        if (selectedIds.size === filtered.length) {
            setSelectedIds(new Set());
        } else {
            setSelectedIds(new Set(filtered.map(d => d.id)));
        }
    };

    const handleExport = () => {
        exportToCsv("decal-report", filtered, [
            { key: "driver_code", label: "Driver Code" },
            { key: "first_name", label: "First Name" },
            { key: "last_name", label: "Last Name" },
            { key: "phone", label: "Phone" },
            { key: "vehicle_make", label: "Vehicle Make" },
            { key: "vehicle_model", label: "Vehicle Model" },
            { key: "vehicle_year", label: "Vehicle Year" },
            { key: "vehicle_color", label: "Vehicle Color" },
            { key: "license_plate", label: "License Plate" },
            { key: "decal_number", label: "Decal Number" },
            { key: "decal_generated_at", label: "Decal Generated At" },
            { key: "decals_sent", label: "Decal Sent" },
            { key: "decals_sent_at", label: "Decal Sent At" },
        ]);
    };

    const decalStatus = (d: any) => {
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
                        <Sticker className="h-6 w-6 text-primary" />
                        Decal Generation
                    </h1>
                    <p className="text-sm text-muted-foreground mt-1">
                        Generate and track vehicle decals for active drivers
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
                            <FileText className="h-4 w-4" />
                            Generate ({selectedIds.size})
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

            {/* Search */}
            <div className="relative w-full sm:w-80">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                <Input
                    placeholder="Search by name, plate, driver code, decal #..."
                    value={search}
                    onChange={e => setSearch(e.target.value)}
                    className="pl-9 h-9 text-sm"
                />
            </div>

            {/* Table */}
            <div className="bg-card border rounded-2xl overflow-hidden shadow-sm">
                <Table>
                    <TableHeader>
                        <TableRow className="bg-muted/50 hover:bg-muted/50 border-b-0">
                            <TableHead className="h-11 w-10 pl-4">
                                <input
                                    type="checkbox"
                                    checked={filtered.length > 0 && selectedIds.size === filtered.length}
                                    onChange={toggleSelectAll}
                                    className="rounded border-border"
                                />
                            </TableHead>
                            <TableHead className="h-11"><span className="text-[11px] font-semibold text-foreground/80 uppercase tracking-wider">Driver</span></TableHead>
                            <TableHead className="h-11"><span className="text-[11px] font-semibold text-foreground/80 uppercase tracking-wider">Vehicle</span></TableHead>
                            <TableHead className="h-11"><span className="text-[11px] font-semibold text-foreground/80 uppercase tracking-wider">License Plate</span></TableHead>
                            <TableHead className="h-11"><span className="text-[11px] font-semibold text-foreground/80 uppercase tracking-wider">Decal #</span></TableHead>
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
                        )) : filtered.length === 0 ? (
                            <TableRow>
                                <TableCell colSpan={9} className="text-center py-16 text-muted-foreground">
                                    <Sticker className="h-10 w-10 mx-auto mb-3 opacity-20" />
                                    <p className="text-base font-medium">No drivers found</p>
                                    <p className="text-sm mt-1">Try adjusting your search or filter</p>
                                </TableCell>
                            </TableRow>
                        ) : filtered.map(driver => {
                            const st = decalStatus(driver);
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
                                                disabled={generatingId === driver.id}
                                                onClick={() => handleGenerate(driver)}
                                            >
                                                {generatingId === driver.id ? (
                                                    <Loader2 className="h-3 w-3 animate-spin" />
                                                ) : (
                                                    <FileText className="h-3 w-3" />
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

            {!loading && filtered.length > 0 && (
                <p className="text-xs text-muted-foreground text-right">
                    Showing {filtered.length} of {drivers.length} active drivers
                </p>
            )}
        </div>
    );
}
