"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
    RefreshCw,
    Filter,
    Bell,
    Calendar,
    AlertTriangle,
    Inbox,
    Car,
    Loader2,
} from "lucide-react";
import {
    getExpiringDocs,
    getServiceAreas,
    nudgeDriverExpiry,
    type ExpiringDocItem,
} from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select";
import { useToast } from "@/components/ui/use-toast";
import { useRequireModule } from "@/hooks/useRequireModule";

type Window = 7 | 14 | 30;

const daysTone = (days: number) => {
    if (days < 7) return "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-300 border-red-200 dark:border-red-800";
    if (days < 14) return "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300 border-amber-200 dark:border-amber-800";
    return "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300 border-emerald-200 dark:border-emerald-800";
};

const initials = (name: string) => {
    const parts = name.trim().split(/\s+/).filter(Boolean);
    if (!parts.length) return "?";
    return (parts[0][0] + (parts[1]?.[0] || "")).toUpperCase();
};

const fmtDate = (iso: string) => {
    try { return new Date(iso).toLocaleDateString("en-CA", { month: "short", day: "numeric", year: "numeric" }); }
    catch { return iso; }
};

export default function ExpiringDocsPage() {
    const { allowed } = useRequireModule("drivers");
    const { toast } = useToast();
    const [items, setItems] = useState<ExpiringDocItem[]>([]);
    const [loading, setLoading] = useState(true);
    const [refreshing, setRefreshing] = useState(false);
    const [windowDays, setWindowDays] = useState<Window>(30);
    const [serviceAreaId, setServiceAreaId] = useState("all");
    const [serviceAreas, setServiceAreas] = useState<Array<{ id: string; name?: string }>>([]);
    const [nudgingDriverDoc, setNudgingDriverDoc] = useState<string | null>(null);

    const load = useCallback(async () => {
        try {
            setRefreshing(true);
            const data = await getExpiringDocs({
                window_days: windowDays,
                service_area_id: serviceAreaId !== "all" ? serviceAreaId : undefined,
            });
            setItems(data?.items || []);
        } catch (e) {
            toast({ title: "Failed to load expiring docs", description: String((e as Error).message || e), variant: "destructive" });
        } finally {
            setLoading(false);
            setRefreshing(false);
        }
    }, [windowDays, serviceAreaId, toast]);

    useEffect(() => { if (allowed) load(); }, [allowed, load]);
    useEffect(() => { getServiceAreas().then((rows) => setServiceAreas(Array.isArray(rows) ? rows : [])).catch(() => {}); }, []);

    const handleNudge = async (it: ExpiringDocItem) => {
        const key = `${it.driver_id}:${it.doc_type}`;
        setNudgingDriverDoc(key);
        try {
            await nudgeDriverExpiry(it.driver_id, { doc_type: it.doc_type, doc_label: it.doc_label });
            toast({ title: "Reminder sent", description: `${it.name} — ${it.doc_label}` });
            load();
        } catch (e) {
            toast({ title: "Nudge failed", description: String((e as Error).message || e), variant: "destructive" });
        } finally {
            setNudgingDriverDoc(null);
        }
    };

    const counts = useMemo(() => {
        const c = { lt7: 0, lt14: 0, all: items.length };
        for (const it of items) {
            if (it.days_remaining < 7) c.lt7++;
            if (it.days_remaining < 14) c.lt14++;
        }
        return c;
    }, [items]);

    if (!allowed) return null;
    if (loading) return null;

    return (
        <div className="space-y-4">
            <div className="flex items-start justify-between gap-3 flex-wrap">
                <div>
                    <h1 className="text-xl font-bold tracking-tight">Expiring Documents</h1>
                    <p className="text-sm text-muted-foreground mt-0.5">
                        Drivers whose licence, insurance, inspection, or background check expires soon. Nudge them before they get blocked at go-online.
                    </p>
                </div>
                <Button variant="outline" size="sm" onClick={load} disabled={refreshing}>
                    <RefreshCw className={`h-4 w-4 mr-1.5 ${refreshing ? "animate-spin" : ""}`} />
                    Refresh
                </Button>
            </div>

            <div className="flex items-center gap-3 flex-wrap">
                <div className="flex items-center gap-1.5 rounded-lg border border-border bg-card p-1">
                    {([7, 14, 30] as Window[]).map((w) => (
                        <button
                            key={w}
                            type="button"
                            onClick={() => setWindowDays(w)}
                            className={`text-xs font-medium px-3 py-1.5 rounded-md transition-colors ${
                                windowDays === w
                                    ? "bg-foreground text-background"
                                    : "text-muted-foreground hover:text-foreground"
                            }`}
                        >
                            {w}d
                        </button>
                    ))}
                </div>

                <div className="flex items-center gap-2">
                    <Filter className="h-4 w-4 text-muted-foreground" />
                    <Select value={serviceAreaId} onValueChange={setServiceAreaId}>
                        <SelectTrigger className="h-8 w-48 text-xs">
                            <SelectValue placeholder="All areas" />
                        </SelectTrigger>
                        <SelectContent>
                            <SelectItem value="all">All areas</SelectItem>
                            {serviceAreas.map((a) => (
                                <SelectItem key={a.id} value={a.id}>{a.name || a.id.slice(0, 8)}</SelectItem>
                            ))}
                        </SelectContent>
                    </Select>
                </div>

                <div className="ml-auto flex items-center gap-3 text-xs text-muted-foreground">
                    <span className="inline-flex items-center gap-1.5">
                        <AlertTriangle className="h-3.5 w-3.5 text-red-600" />
                        {counts.lt7} urgent
                    </span>
                    <span className="inline-flex items-center gap-1.5">
                        <Calendar className="h-3.5 w-3.5 text-amber-600" />
                        {counts.lt14} within 14d
                    </span>
                    <span className="inline-flex items-center gap-1.5">
                        <Car className="h-3.5 w-3.5" />
                        {counts.all} total
                    </span>
                </div>
            </div>

            <div className="rounded-xl border border-border overflow-hidden bg-card">
                <div className="grid grid-cols-[1fr_180px_140px_120px_100px_120px_120px] gap-4 px-4 py-3 bg-muted/30 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                    <div>Driver</div>
                    <div>Document</div>
                    <div>Expiry</div>
                    <div>Days left</div>
                    <div>Rides 30d</div>
                    <div>Last nudge</div>
                    <div className="text-right">Action</div>
                </div>

                {items.length === 0 ? (
                    <div className="px-6 py-16 text-center text-muted-foreground">
                        <Inbox className="h-10 w-10 mx-auto mb-3 opacity-30" />
                        <p className="text-sm font-medium">Nothing expiring in this window</p>
                        <p className="text-xs mt-1">All drivers are good for the next {windowDays} days.</p>
                    </div>
                ) : (
                    items.map((it) => {
                        const key = `${it.driver_id}:${it.doc_type}`;
                        const isBusy = nudgingDriverDoc === key;
                        return (
                            <div
                                key={key}
                                className="grid grid-cols-[1fr_180px_140px_120px_100px_120px_120px] gap-4 items-center px-4 py-3 border-t border-border hover:bg-muted/20 transition-colors"
                            >
                                <div className="flex items-center gap-3 min-w-0">
                                    {it.profile_photo_url ? (
                                        // eslint-disable-next-line @next/next/no-img-element
                                        <img src={it.profile_photo_url} alt="" className="h-9 w-9 rounded-full object-cover shrink-0" />
                                    ) : (
                                        <div className="h-9 w-9 rounded-full bg-muted flex items-center justify-center text-xs font-semibold text-muted-foreground shrink-0">
                                            {initials(it.name || it.email || "?")}
                                        </div>
                                    )}
                                    <div className="min-w-0">
                                        <p className="text-sm font-medium truncate">{it.name || "(no name)"}</p>
                                        <p className="text-xs text-muted-foreground truncate">
                                            {it.service_area_name || it.email || it.phone || "—"}
                                        </p>
                                    </div>
                                </div>
                                <div className="text-sm truncate" title={it.doc_label}>{it.doc_label}</div>
                                <div className="text-xs text-muted-foreground">{fmtDate(it.expiry_date)}</div>
                                <div>
                                    <span className={`inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium border ${daysTone(it.days_remaining)}`}>
                                        {it.days_remaining}d
                                    </span>
                                </div>
                                <div className="text-sm tabular-nums">{it.rides_last_30d}</div>
                                <div className="text-xs text-muted-foreground">
                                    {it.last_nudged_at ? fmtDate(it.last_nudged_at) : "—"}
                                </div>
                                <div className="text-right">
                                    <Button
                                        size="sm"
                                        variant="outline"
                                        className="h-8"
                                        disabled={isBusy}
                                        onClick={() => handleNudge(it)}
                                    >
                                        {isBusy ? <Loader2 className="h-3.5 w-3.5 animate-spin mr-1" /> : <Bell className="h-3.5 w-3.5 mr-1" />}
                                        Nudge
                                    </Button>
                                </div>
                            </div>
                        );
                    })
                )}
            </div>
        </div>
    );
}
