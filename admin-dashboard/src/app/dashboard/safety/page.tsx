"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
    AlertTriangle,
    Bell,
    Car,
    CheckCircle,
    Clock,
    Copy,
    ExternalLink,
    Filter,
    Loader2,
    MapPin,
    Mail,
    Phone,
    RefreshCw,
    Search,
    Shield,
    User,
    UserCheck,
    X,
    XCircle,
    Plus,
    GitMerge,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHeader, TableRow } from "@/components/ui/table";
import { useTableSort, SortableHead } from "@/components/ui/sortable-table";
import { Pagination } from "@/components/ui/pagination";
import { Sheet, SheetContent, SheetTitle, SheetDescription } from "@/components/ui/sheet";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import {
    Dialog,
    DialogContent,
    DialogHeader,
    DialogTitle,
    DialogFooter,
} from "@/components/ui/dialog";
import { useToast } from "@/components/ui/use-toast";
import { useRequireModule } from "@/hooks/useRequireModule";
import { IncidentEvidencePhotos } from "./_components/incident-evidence-photos";
import {
    getSafetyIncident,
    getSafetyIncidents,
    updateSafetyIncident,
    createSafetyIncident,
    mergeSafetyIncident,
    type SafetyIncident,
    type SafetyIncidentDetail,
    type SafetyRole,
    type SafetySeverity,
    type SafetyStatus,
} from "@/lib/api";

const STATUS_OPTIONS: Array<{ value: SafetyStatus | "all"; label: string }> = [
    { value: "all", label: "Open (default)" },
    { value: "open", label: "Open" },
    { value: "in_progress", label: "In progress" },
    { value: "resolved", label: "Resolved" },
    { value: "closed", label: "Closed" },
    { value: "duplicate", label: "Duplicate" },
];

const SEVERITY_OPTIONS: Array<{ value: SafetySeverity | "all"; label: string }> = [
    { value: "all", label: "Any severity" },
    { value: "sev1", label: "Sev 1 · immediate" },
    { value: "sev2", label: "Sev 2 · non-safety abuse" },
    { value: "sev3", label: "Sev 3 · info" },
];

const ROLE_OPTIONS: Array<{ value: SafetyRole | "all"; label: string }> = [
    { value: "all", label: "Any role" },
    { value: "rider", label: "Rider" },
    { value: "driver", label: "Driver" },
    { value: "system", label: "System (auto)" },
];

function statusTone(s: SafetyStatus): { bg: string; text: string; label: string } {
    switch (s) {
        case "open":
            return { bg: "bg-destructive/15", text: "text-destructive", label: "Open" };
        case "in_progress":
            return { bg: "bg-warning/15", text: "text-warning", label: "In progress" };
        case "resolved":
            return { bg: "bg-success/15", text: "text-success", label: "Resolved" };
        case "closed":
            return { bg: "bg-muted", text: "text-muted-foreground", label: "Closed" };
        case "duplicate":
            return { bg: "bg-muted", text: "text-muted-foreground", label: "Duplicate" };
    }
}

function severityTone(s: SafetySeverity | null): { bg: string; text: string; label: string } {
    if (s === "sev1") return { bg: "bg-destructive/15", text: "text-destructive", label: "SEV 1" };
    if (s === "sev2") return { bg: "bg-warning/15", text: "text-warning", label: "SEV 2" };
    // eslint-disable-next-line no-restricted-syntax -- "SEV 3" (lowest severity) has no semantic-token equivalent; must stay visually distinct from SEV 1/SEV 2 (#2816)
    if (s === "sev3") return { bg: "bg-blue-100 dark:bg-blue-900/30", text: "text-blue-700 dark:text-blue-300", label: "SEV 3" };
    return { bg: "bg-muted/60", text: "text-muted-foreground", label: "Unset" };
}

function relativeTime(iso?: string | null) {
    if (!iso) return "—";
    const t = new Date(iso).getTime();
    if (Number.isNaN(t)) return iso;
    const diff = Date.now() - t;
    const s = Math.floor(diff / 1000);
    if (s < 60) return `${s}s ago`;
    if (s < 3600) return `${Math.floor(s / 60)}m ago`;
    if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
    return `${Math.floor(s / 86400)}d ago`;
}

function fmtDateTime(iso?: string | null) {
    if (!iso) return "—";
    try {
        const d = new Date(iso);
        return `${d.toLocaleDateString("en-CA", { month: "short", day: "numeric", year: "numeric" })} · ${d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })}`;
    } catch {
        return iso;
    }
}

const PAGE_SIZE = 50;

export default function SafetyPage() {
    const { allowed } = useRequireModule("support");
    const { toast } = useToast();
    const router = useRouter();

    const [items, setItems] = useState<SafetyIncident[]>([]);
    const [openCount, setOpenCount] = useState<number | null>(null);
    const [total, setTotal] = useState(0);
    const [page, setPage] = useState(0);
    const [loading, setLoading] = useState(true);
    const [refreshing, setRefreshing] = useState(false);

    const [statusFilter, setStatusFilter] = useState<SafetyStatus | "all">("all");
    const [severityFilter, setSeverityFilter] = useState<SafetySeverity | "all">("all");
    const [roleFilter, setRoleFilter] = useState<SafetyRole | "all">("all");
    const [search, setSearch] = useState("");

    const [selectedId, setSelectedId] = useState<string | null>(null);
    const [detail, setDetail] = useState<SafetyIncidentDetail | null>(null);
    const [detailLoading, setDetailLoading] = useState(false);

    // Corporate + admin portal review, round 2: "safety incidents can't be
    // created ... from the admin side" — e.g. a phone-in report that never
    // went through the app's own SOS flow.
    const [createOpen, setCreateOpen] = useState(false);

    const reqIdRef = useRef(0);

    const load = useCallback(async () => {
        const id = ++reqIdRef.current;
        setRefreshing(true);
        try {
            const res = await getSafetyIncidents({
                status: statusFilter !== "all" ? statusFilter : undefined,
                severity: severityFilter !== "all" ? severityFilter : undefined,
                role: roleFilter !== "all" ? roleFilter : undefined,
                search: search.trim() || undefined,
                limit: PAGE_SIZE,
                offset: page * PAGE_SIZE,
            });
            // Drop stale responses if the user re-filtered while a fetch was in flight.
            if (id !== reqIdRef.current) return;
            setItems(res.items || []);
            setTotal(res.total ?? 0);
            setOpenCount(res.open_count ?? null);
        } catch (e: any) {
            toast({
                title: "Could not load safety queue",
                description: e?.message || "Unknown error",
                variant: "destructive",
            });
        } finally {
            setLoading(false);
            setRefreshing(false);
        }
    }, [statusFilter, severityFilter, roleFilter, search, page, toast]);

    useEffect(() => {
        if (!allowed) return;
        load();
    }, [allowed, load]);

    // Any filter/search change resets to the first page so the user never lands
    // on an out-of-range offset (e.g. page 3 of a now-shorter result set).
    useEffect(() => {
        setPage(0);
    }, [statusFilter, severityFilter, roleFilter, search]);

    // Live updates: when an admin WS broadcast announces a new incident,
    // refresh the list. We don't optimistically prepend because the
    // backend enriches with reporter_name and we'd otherwise show a
    // bare row until the next manual refresh.
    useEffect(() => {
        if (!allowed) return;
        function onMessage(ev: MessageEvent) {
            try {
                const data = JSON.parse(ev.data);
                if (data?.type === "safety_incident_opened" || data?.type === "emergency_alert") {
                    load();
                }
            } catch {}
        }
        const ws = (window as any).__adminWs as WebSocket | undefined;
        if (!ws) return;
        ws.addEventListener("message", onMessage);
        return () => ws.removeEventListener("message", onMessage);
    }, [allowed, load]);

    const openSelected = useCallback(async (id: string) => {
        setSelectedId(id);
        setDetail(null);
        setDetailLoading(true);
        try {
            const res = await getSafetyIncident(id);
            setDetail(res);
        } catch (e: any) {
            toast({ title: "Could not load incident", description: e?.message || "Unknown error", variant: "destructive" });
        } finally {
            setDetailLoading(false);
        }
    }, [toast]);

    const onPatched = useCallback((updated: SafetyIncident) => {
        // Reflect the change locally so the queue updates without a full reload.
        setItems((prev) => prev.map((it) => (it.id === updated.id ? updated : it)));
        setDetail((prev) => (prev ? { ...prev, incident: updated } : prev));
    }, []);

    const filteredCounts = useMemo(() => {
        const c = { sev1: 0, sev2: 0, sev3: 0, unset: 0 };
        for (const it of items) {
            if (it.severity === "sev1") c.sev1++;
            else if (it.severity === "sev2") c.sev2++;
            else if (it.severity === "sev3") c.sev3++;
            else c.unset++;
        }
        return c;
    }, [items]);

    const { sorted, sort, toggle } = useTableSort(items);

    if (!allowed) return null;

    return (
        <div className="space-y-4">
            {/* Header */}
            <div className="flex items-start justify-between gap-3 flex-wrap">
                <div>
                    <h1 className="text-xl font-bold tracking-tight flex items-center gap-2">
                        <Shield className="h-5 w-5 text-destructive" />
                        Safety queue
                    </h1>
                    <p className="text-sm text-muted-foreground mt-0.5">
                        Rider SOS, driver safety reports, and auto-escalations from the in-trip check-in loop.
                    </p>
                </div>
                <div className="flex items-center gap-3 text-xs text-muted-foreground">
                    {openCount != null && (
                        <span className="inline-flex items-center gap-1.5">
                            <AlertTriangle className="h-3.5 w-3.5 text-destructive" />
                            <span className="font-semibold text-foreground">{openCount}</span> open
                        </span>
                    )}
                    <Button variant="outline" size="sm" onClick={() => setCreateOpen(true)}>
                        <Plus className="h-4 w-4 mr-1.5" />
                        Log Incident
                    </Button>
                    <Button variant="outline" size="sm" onClick={load} disabled={refreshing}>
                        <RefreshCw className={`h-4 w-4 mr-1.5 ${refreshing ? "animate-spin" : ""}`} />
                        Refresh
                    </Button>
                </div>
            </div>

            {/* Filters */}
            <div className="flex items-center gap-2 flex-wrap">
                <div className="flex items-center gap-1.5">
                    <Search className="h-4 w-4 text-muted-foreground" />
                    <Input
                        value={search}
                        onChange={(e) => setSearch(e.target.value)}
                        placeholder="Search description"
                        className="h-8 text-xs w-[240px]"
                    />
                </div>
                <Filter className="h-4 w-4 text-muted-foreground ml-1" />
                <Select value={statusFilter} onValueChange={(v) => setStatusFilter(v as SafetyStatus | "all")}>
                    <SelectTrigger className="h-8 text-xs w-[170px]" aria-label="Filter by status"><SelectValue /></SelectTrigger>
                    <SelectContent>
                        {STATUS_OPTIONS.map((o) => (
                            <SelectItem key={o.value} value={o.value} className="text-xs">{o.label}</SelectItem>
                        ))}
                    </SelectContent>
                </Select>
                <Select value={severityFilter} onValueChange={(v) => setSeverityFilter(v as SafetySeverity | "all")}>
                    <SelectTrigger className="h-8 text-xs w-[180px]" aria-label="Filter by severity"><SelectValue /></SelectTrigger>
                    <SelectContent>
                        {SEVERITY_OPTIONS.map((o) => (
                            <SelectItem key={o.value} value={o.value} className="text-xs">{o.label}</SelectItem>
                        ))}
                    </SelectContent>
                </Select>
                <Select value={roleFilter} onValueChange={(v) => setRoleFilter(v as SafetyRole | "all")}>
                    <SelectTrigger className="h-8 text-xs w-[140px]" aria-label="Filter by role"><SelectValue /></SelectTrigger>
                    <SelectContent>
                        {ROLE_OPTIONS.map((o) => (
                            <SelectItem key={o.value} value={o.value} className="text-xs">{o.label}</SelectItem>
                        ))}
                    </SelectContent>
                </Select>
                <span className="ml-auto text-[11px] text-muted-foreground tabular-nums">
                    {items.length} shown · sev1:{filteredCounts.sev1} sev2:{filteredCounts.sev2} sev3:{filteredCounts.sev3} unset:{filteredCounts.unset}
                </span>
            </div>

            {/* Queue table */}
            <div className="rounded-xl border border-border overflow-hidden bg-card">
                {loading ? (
                    <div className="px-6 py-16 text-center text-muted-foreground">
                        <Loader2 className="h-6 w-6 mx-auto mb-3 animate-spin opacity-60" />
                        Loading queue…
                    </div>
                ) : items.length === 0 ? (
                    <div className="px-6 py-16 text-center text-muted-foreground">
                        <CheckCircle className="h-10 w-10 mx-auto mb-3 opacity-30" />
                        <p className="text-sm font-medium">No incidents match this filter</p>
                        <p className="text-xs mt-1">If you expected open incidents to show, clear filters above.</p>
                    </div>
                ) : (
                    <Table>
                        <TableHeader>
                            <TableRow className="bg-muted/30 hover:bg-muted/30">
                                <SortableHead column="reported_at" sort={sort} onSort={toggle} className="h-9 text-[11px] uppercase tracking-wider">Reported</SortableHead>
                                <SortableHead column="severity" sort={sort} onSort={toggle} className="h-9 text-[11px] uppercase tracking-wider">Severity</SortableHead>
                                <SortableHead column="category" sort={sort} onSort={toggle} className="h-9 text-[11px] uppercase tracking-wider">Category</SortableHead>
                                <SortableHead column="reporter_name" sort={sort} onSort={toggle} className="h-9 text-[11px] uppercase tracking-wider">Reporter</SortableHead>
                                <SortableHead column="ride_id" sort={sort} onSort={toggle} className="h-9 text-[11px] uppercase tracking-wider">Ride</SortableHead>
                                <SortableHead column="status" sort={sort} onSort={toggle} className="h-9 text-[11px] uppercase tracking-wider">Status</SortableHead>
                                <SortableHead column="assigned_to_admin_id" sort={sort} onSort={toggle} className="h-9 text-[11px] uppercase tracking-wider">Assigned</SortableHead>
                            </TableRow>
                        </TableHeader>
                        <TableBody>
                            {sorted.map((it) => {
                                const st = statusTone(it.status);
                                const sv = severityTone(it.severity);
                                return (
                                    <TableRow
                                        key={it.id}
                                        className="cursor-pointer hover:bg-muted/20"
                                        onClick={() => openSelected(it.id)}
                                    >
                                        <TableCell className="text-xs whitespace-nowrap">
                                            <div className="font-medium">{relativeTime(it.reported_at)}</div>
                                            <div className="text-[10px] text-muted-foreground">{fmtDateTime(it.reported_at)}</div>
                                        </TableCell>
                                        <TableCell>
                                            <span className={`inline-flex items-center px-2 py-0.5 rounded-md text-[10px] font-semibold ${sv.bg} ${sv.text}`}>
                                                {sv.label}
                                            </span>
                                        </TableCell>
                                        <TableCell className="text-xs">
                                            <div className="font-mono font-medium truncate max-w-[180px]" title={it.category}>{it.category}</div>
                                            <div className="text-[10px] text-muted-foreground capitalize">{it.role}</div>
                                        </TableCell>
                                        <TableCell className="text-xs">
                                            {it.reporter_name ? (
                                                <span className="truncate max-w-[150px] block">{it.reporter_name}</span>
                                            ) : it.reported_by_user_id ? (
                                                <span className="font-mono text-muted-foreground">{it.reported_by_user_id.slice(0, 8)}…</span>
                                            ) : (
                                                <span className="text-muted-foreground italic">system</span>
                                            )}
                                        </TableCell>
                                        <TableCell className="text-xs">
                                            {it.ride_id ? (
                                                <button
                                                    type="button"
                                                    onClick={(e) => { e.stopPropagation(); router.push(`/dashboard/rides?id=${it.ride_id}`); }}
                                                    className="inline-flex items-center gap-1 font-mono text-primary hover:underline"
                                                >
                                                    <Car className="h-3 w-3" />
                                                    {it.ride_id.slice(0, 8)}
                                                </button>
                                            ) : (
                                                <span className="text-muted-foreground">—</span>
                                            )}
                                        </TableCell>
                                        <TableCell>
                                            <span className={`inline-flex items-center px-2 py-0.5 rounded-md text-[10px] font-semibold ${st.bg} ${st.text}`}>
                                                {st.label}
                                            </span>
                                        </TableCell>
                                        <TableCell className="text-xs text-muted-foreground">
                                            {it.assigned_to_admin_id ? (
                                                <span className="font-mono">{it.assigned_to_admin_id.slice(0, 8)}…</span>
                                            ) : (
                                                <span className="italic">unassigned</span>
                                            )}
                                        </TableCell>
                                    </TableRow>
                                );
                            })}
                        </TableBody>
                    </Table>
                )}
            </div>

            {/* Pagination — server-side offset; only the current page is fetched. */}
            {!loading && total > 0 && (
                <Pagination
                    page={page}
                    pageSize={PAGE_SIZE}
                    hasNextPage={(page + 1) * PAGE_SIZE < total}
                    totalCount={total}
                    onPageChange={setPage}
                />
            )}

            {/* Detail / triage drawer */}
            <Sheet open={!!selectedId} onOpenChange={(open) => { if (!open) { setSelectedId(null); setDetail(null); } }}>
                <SheetContent
                    side="right"
                    showCloseButton={false}
                    className="w-full sm:max-w-none sm:w-[640px] p-0 overflow-y-auto"
                    aria-describedby={undefined}
                >
                    <SheetTitle className="sr-only">Safety incident</SheetTitle>
                    <SheetDescription className="sr-only">Triage a safety incident</SheetDescription>
                    {detailLoading || !detail ? (
                        <div className="p-12 text-center text-muted-foreground">
                            <Loader2 className="h-6 w-6 mx-auto mb-3 animate-spin opacity-60" />
                            Loading incident…
                        </div>
                    ) : (
                        <IncidentDetailDrawer
                            detail={detail}
                            onClose={() => { setSelectedId(null); setDetail(null); }}
                            onPatched={onPatched}
                        />
                    )}
                </SheetContent>
            </Sheet>

            <CreateIncidentDialog
                open={createOpen}
                onOpenChange={setCreateOpen}
                onCreated={() => {
                    setCreateOpen(false);
                    load();
                }}
            />
        </div>
    );
}

function CreateIncidentDialog({
    open,
    onOpenChange,
    onCreated,
}: {
    open: boolean;
    onOpenChange: (open: boolean) => void;
    onCreated: () => void;
}) {
    const { toast } = useToast();
    const [category, setCategory] = useState("");
    const [description, setDescription] = useState("");
    const [role, setRole] = useState<SafetyRole>("rider");
    const [severity, setSeverity] = useState<SafetySeverity | "unset">("unset");
    const [reportedByUserId, setReportedByUserId] = useState("");
    const [rideId, setRideId] = useState("");
    const [saving, setSaving] = useState(false);

    const reset = () => {
        setCategory("");
        setDescription("");
        setRole("rider");
        setSeverity("unset");
        setReportedByUserId("");
        setRideId("");
    };

    const handleSubmit = async () => {
        if (!category.trim() || !description.trim()) {
            toast({ title: "Category and description are required", variant: "destructive" });
            return;
        }
        setSaving(true);
        try {
            await createSafetyIncident({
                category: category.trim(),
                description: description.trim(),
                role,
                severity: severity === "unset" ? undefined : severity,
                reported_by_user_id: reportedByUserId.trim() || undefined,
                ride_id: rideId.trim() || undefined,
            });
            toast({ title: "Incident logged" });
            reset();
            onCreated();
        } catch (e: any) {
            toast({ title: "Could not log incident", description: e?.message || "Unknown error", variant: "destructive" });
        } finally {
            setSaving(false);
        }
    };

    return (
        <Dialog open={open} onOpenChange={(o) => { if (!o) reset(); onOpenChange(o); }}>
            <DialogContent className="sm:max-w-lg">
                <DialogHeader>
                    <DialogTitle>Log a safety incident</DialogTitle>
                </DialogHeader>
                <div className="space-y-3 py-1">
                    <p className="text-xs text-muted-foreground">
                        For a report that came in by phone or in person and never went through the app&apos;s own SOS flow.
                    </p>
                    <div>
                        <Label className="text-xs mb-1 block">Category</Label>
                        <Input value={category} onChange={(e) => setCategory(e.target.value)} placeholder="e.g. unsafe_driving" />
                    </div>
                    <div>
                        <Label className="text-xs mb-1 block">Description</Label>
                        <Textarea value={description} onChange={(e) => setDescription(e.target.value)} className="min-h-[90px]" />
                    </div>
                    <div className="grid grid-cols-2 gap-3">
                        <div>
                            <Label className="text-xs mb-1 block">Reported by</Label>
                            <Select value={role} onValueChange={(v) => setRole(v as SafetyRole)}>
                                <SelectTrigger className="h-9 text-sm"><SelectValue /></SelectTrigger>
                                <SelectContent>
                                    <SelectItem value="rider" className="text-sm">Rider</SelectItem>
                                    <SelectItem value="driver" className="text-sm">Driver</SelectItem>
                                    <SelectItem value="system" className="text-sm">System</SelectItem>
                                </SelectContent>
                            </Select>
                        </div>
                        <div>
                            <Label className="text-xs mb-1 block">Severity (optional)</Label>
                            <Select value={severity} onValueChange={(v) => setSeverity(v as SafetySeverity | "unset")}>
                                <SelectTrigger className="h-9 text-sm"><SelectValue /></SelectTrigger>
                                <SelectContent>
                                    <SelectItem value="unset" className="text-sm">Unset</SelectItem>
                                    <SelectItem value="sev1" className="text-sm">Sev 1 · immediate</SelectItem>
                                    <SelectItem value="sev2" className="text-sm">Sev 2 · non-safety abuse</SelectItem>
                                    <SelectItem value="sev3" className="text-sm">Sev 3 · info</SelectItem>
                                </SelectContent>
                            </Select>
                        </div>
                    </div>
                    <div className="grid grid-cols-2 gap-3">
                        <div>
                            <Label className="text-xs mb-1 block">Reporter user ID (optional)</Label>
                            <Input value={reportedByUserId} onChange={(e) => setReportedByUserId(e.target.value)} className="font-mono text-xs" />
                        </div>
                        <div>
                            <Label className="text-xs mb-1 block">Ride ID (optional)</Label>
                            <Input value={rideId} onChange={(e) => setRideId(e.target.value)} className="font-mono text-xs" />
                        </div>
                    </div>
                </div>
                <DialogFooter>
                    <Button variant="outline" size="sm" onClick={() => onOpenChange(false)}>Cancel</Button>
                    <Button size="sm" onClick={handleSubmit} disabled={saving}>
                        {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin mr-1.5" /> : <Plus className="h-3.5 w-3.5 mr-1.5" />}
                        Log Incident
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}

function IncidentDetailDrawer({
    detail,
    onClose,
    onPatched,
}: {
    detail: SafetyIncidentDetail;
    onClose: () => void;
    onPatched: (updated: SafetyIncident) => void;
}) {
    const { incident, reporter, ride } = detail;
    // Optional: a backend deployed before migration 340 omits the key entirely.
    const photos = detail.photos ?? [];
    const { toast } = useToast();

    const [status, setStatus] = useState<SafetyStatus>(incident.status);
    const [severity, setSeverity] = useState<SafetySeverity | "unset">(incident.severity ?? "unset");
    const [resolutionNotes, setResolutionNotes] = useState(incident.resolution_notes ?? "");
    const [saving, setSaving] = useState(false);
    const st = statusTone(incident.status);
    const sv = severityTone(incident.severity);

    // Corporate + admin portal review, round 2: "safety incidents can't be
    // ... merged from the admin side" — link a duplicate report (e.g. rider
    // and driver both SOS the same event) to the canonical incident.
    const [mergeTargetId, setMergeTargetId] = useState("");
    const [merging, setMerging] = useState(false);

    const handleMerge = async () => {
        const targetId = mergeTargetId.trim();
        if (!targetId) {
            toast({ title: "Enter the canonical incident ID to merge into", variant: "destructive" });
            return;
        }
        if (targetId === incident.id) {
            toast({ title: "Cannot merge an incident into itself", variant: "destructive" });
            return;
        }
        setMerging(true);
        try {
            const res = await mergeSafetyIncident(incident.id, targetId);
            onPatched(res.incident);
            setMergeTargetId("");
            toast({ title: "Marked as duplicate", description: `Merged into ${targetId.slice(0, 8)}…` });
        } catch (e: any) {
            toast({ title: "Merge failed", description: e?.message || "Unknown error", variant: "destructive" });
        } finally {
            setMerging(false);
        }
    };

    const handleSave = async () => {
        const body: Parameters<typeof updateSafetyIncident>[1] = {};
        if (status !== incident.status) body.status = status;
        if (severity !== (incident.severity ?? "unset")) {
            body.severity = severity === "unset" ? undefined : severity;
        }
        if (resolutionNotes !== (incident.resolution_notes ?? "")) {
            body.resolution_notes = resolutionNotes;
        }
        if (Object.keys(body).length === 0) {
            toast({ title: "Nothing to save" });
            return;
        }
        setSaving(true);
        try {
            const res = await updateSafetyIncident(incident.id, body);
            onPatched(res.incident);
            toast({ title: "Incident updated" });
        } catch (e: any) {
            toast({ title: "Update failed", description: e?.message || "Unknown error", variant: "destructive" });
        } finally {
            setSaving(false);
        }
    };

    const copy = (s: string | null | undefined) => {
        if (!s) return;
        navigator.clipboard.writeText(s);
        toast({ description: "Copied" });
    };

    return (
        <div className="flex flex-col h-full">
            {/* Header */}
            {/* eslint-disable-next-line no-restricted-syntax -- decorative header accent gradient, not a status signal (#2816) */}
            <div className="px-6 py-4 border-b border-border bg-gradient-to-r from-red-50 dark:from-red-900/10 to-transparent">
                <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                        <div className="flex items-center gap-2 flex-wrap">
                            <span className={`inline-flex items-center px-2 py-0.5 rounded-md text-[10px] font-semibold ${sv.bg} ${sv.text}`}>{sv.label}</span>
                            <span className={`inline-flex items-center px-2 py-0.5 rounded-md text-[10px] font-semibold ${st.bg} ${st.text}`}>{st.label}</span>
                            <Badge variant="outline" className="text-[10px] capitalize">{incident.role}</Badge>
                        </div>
                        <h2 className="text-lg font-bold mt-2 font-mono truncate">{incident.category}</h2>
                        <button onClick={() => copy(incident.id)} className="flex items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground font-mono mt-1" title="Copy ID">
                            {incident.id.slice(0, 16)}…
                            <Copy className="h-3 w-3" />
                        </button>
                    </div>
                    <Button variant="ghost" size="icon-sm" aria-label="Close" onClick={onClose}>
                        <X className="h-4 w-4" />
                    </Button>
                </div>
            </div>

            <div className="flex-1 overflow-y-auto px-6 py-5 space-y-5">
                {/* Description */}
                <section>
                    <Label className="text-[10px] uppercase tracking-wide text-muted-foreground font-semibold">Description</Label>
                    <p className="text-sm mt-1.5 whitespace-pre-wrap leading-relaxed">{incident.description || "(no description)"}</p>
                </section>

                {/* Evidence photos (backend migration 340). Sits directly under
                    the description because it is the reporter's own account of
                    the same event. Renders nothing when there are no photos. */}
                <IncidentEvidencePhotos photos={photos} formatDateTime={fmtDateTime} />

                {/* Reporter */}
                <section>
                    <Label className="text-[10px] uppercase tracking-wide text-muted-foreground font-semibold">Reporter</Label>
                    {reporter ? (
                        <div className="mt-2 rounded-lg border border-border/60 bg-muted/10 p-3 space-y-1.5">
                            <div className="flex items-center gap-2">
                                <User className="h-4 w-4 text-muted-foreground" />
                                <span className="text-sm font-medium">{reporter.name || "(no name)"}</span>
                                {reporter.role && <Badge variant="outline" className="text-[10px] capitalize">{reporter.role}</Badge>}
                            </div>
                            {reporter.email && (
                                <button onClick={() => copy(reporter.email)} className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground">
                                    <Mail className="h-3 w-3" />
                                    <span>{reporter.email}</span>
                                </button>
                            )}
                            {reporter.phone && (
                                <button onClick={() => copy(reporter.phone)} className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground">
                                    <Phone className="h-3 w-3" />
                                    <span>{reporter.phone}</span>
                                </button>
                            )}
                        </div>
                    ) : (
                        <p className="text-xs text-muted-foreground italic mt-1.5">System-generated (no human reporter).</p>
                    )}
                </section>

                {/* Ride */}
                {ride && (
                    <section>
                        <Label className="text-[10px] uppercase tracking-wide text-muted-foreground font-semibold">Related ride</Label>
                        <div className="mt-2 rounded-lg border border-border/60 bg-muted/10 p-3 space-y-1.5">
                            <div className="flex items-center justify-between gap-2">
                                <div className="flex items-center gap-2">
                                    <Car className="h-4 w-4 text-muted-foreground" />
                                    {ride.ride_code && <span className="text-sm font-mono">{ride.ride_code.toLowerCase()}</span>}
                                    {ride.status && <Badge variant="outline" className="text-[10px] capitalize">{ride.status.replace(/_/g, " ")}</Badge>}
                                </div>
                                {ride.id && (
                                    <a
                                        href={`/dashboard/rides?id=${ride.id}`}
                                        target="_blank"
                                        rel="noreferrer"
                                        className="text-[11px] text-primary hover:underline inline-flex items-center gap-1"
                                    >
                                        Open ride
                                        <ExternalLink className="h-3 w-3" />
                                    </a>
                                )}
                            </div>
                            <div className="text-xs space-y-1 pt-1">
                                <div className="flex items-start gap-2">
                                    {/* eslint-disable-next-line no-restricted-syntax -- pickup/dropoff marker convention, not a status signal (#2816) */}
                                    <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 mt-1.5 shrink-0" />
                                    <span className="text-foreground truncate" title={ride.pickup_address || undefined}>{ride.pickup_address || "—"}</span>
                                </div>
                                <div className="flex items-start gap-2">
                                    {/* eslint-disable-next-line no-restricted-syntax -- pickup/dropoff marker convention, not a status signal (#2816) */}
                                    <span className="w-1.5 h-1.5 rounded-full bg-red-500 mt-1.5 shrink-0" />
                                    <span className="text-muted-foreground truncate" title={ride.dropoff_address || undefined}>{ride.dropoff_address || "—"}</span>
                                </div>
                            </div>
                        </div>
                    </section>
                )}

                {/* Location */}
                {(incident.latitude != null && incident.longitude != null) && (
                    <section>
                        <Label className="text-[10px] uppercase tracking-wide text-muted-foreground font-semibold">Location at report</Label>
                        <div className="mt-2 flex items-center gap-2 text-xs">
                            <MapPin className="h-4 w-4 text-muted-foreground" />
                            <span className="font-mono">{incident.latitude.toFixed(5)}, {incident.longitude.toFixed(5)}</span>
                            <a
                                href={`https://www.google.com/maps/search/?api=1&query=${incident.latitude},${incident.longitude}`}
                                target="_blank"
                                rel="noreferrer"
                                className="text-primary hover:underline inline-flex items-center gap-1"
                            >
                                Open map
                                <ExternalLink className="h-3 w-3" />
                            </a>
                            {incident.location_accuracy != null && (
                                <span className="text-muted-foreground ml-2">±{Math.round(incident.location_accuracy)}m</span>
                            )}
                        </div>
                    </section>
                )}

                {/* Timeline */}
                <section>
                    <Label className="text-[10px] uppercase tracking-wide text-muted-foreground font-semibold">Timeline</Label>
                    <div className="mt-2 text-xs space-y-1">
                        <div className="flex items-center gap-2 text-muted-foreground">
                            <Bell className="h-3 w-3" />
                            Reported {fmtDateTime(incident.reported_at)} · {relativeTime(incident.reported_at)}
                        </div>
                        {incident.assigned_to_admin_id && (
                            <div className="flex items-center gap-2 text-muted-foreground">
                                <UserCheck className="h-3 w-3" />
                                Assigned to <span className="font-mono">{incident.assigned_to_admin_id.slice(0, 8)}…</span>
                            </div>
                        )}
                        {incident.resolved_at && (
                            <div className="flex items-center gap-2 text-success">
                                <CheckCircle className="h-3 w-3" />
                                Resolved {fmtDateTime(incident.resolved_at)}
                                {incident.resolved_by && <span className="text-muted-foreground"> by <span className="font-mono">{incident.resolved_by.slice(0, 8)}…</span></span>}
                            </div>
                        )}
                        <div className="flex items-center gap-2 text-muted-foreground">
                            <Clock className="h-3 w-3" />
                            Last updated {fmtDateTime(incident.updated_at)}
                        </div>
                    </div>
                </section>

                {/* Triage controls */}
                <Card className="border-border/60">
                    <CardHeader className="pb-2">
                        <CardTitle className="text-sm flex items-center gap-2">
                            <Shield className="h-4 w-4 text-muted-foreground" />
                            Triage
                        </CardTitle>
                    </CardHeader>
                    <CardContent className="space-y-3">
                        <div className="grid grid-cols-2 gap-3">
                            <div>
                                <Label className="text-[10px] uppercase tracking-wide text-muted-foreground font-semibold mb-1.5 block">Status</Label>
                                <Select value={status} onValueChange={(v) => setStatus(v as SafetyStatus)}>
                                    <SelectTrigger className="h-9 text-sm"><SelectValue /></SelectTrigger>
                                    <SelectContent>
                                        {STATUS_OPTIONS.filter((o) => o.value !== "all").map((o) => (
                                            <SelectItem key={o.value} value={o.value} className="text-sm">{o.label}</SelectItem>
                                        ))}
                                    </SelectContent>
                                </Select>
                            </div>
                            <div>
                                <Label className="text-[10px] uppercase tracking-wide text-muted-foreground font-semibold mb-1.5 block">Severity</Label>
                                <Select value={severity} onValueChange={(v) => setSeverity(v as SafetySeverity | "unset")}>
                                    <SelectTrigger className="h-9 text-sm"><SelectValue /></SelectTrigger>
                                    <SelectContent>
                                        <SelectItem value="unset" className="text-sm">Unset</SelectItem>
                                        <SelectItem value="sev1" className="text-sm">Sev 1 · immediate</SelectItem>
                                        <SelectItem value="sev2" className="text-sm">Sev 2 · non-safety abuse</SelectItem>
                                        <SelectItem value="sev3" className="text-sm">Sev 3 · info</SelectItem>
                                    </SelectContent>
                                </Select>
                            </div>
                        </div>
                        <div>
                            <Label className="text-[10px] uppercase tracking-wide text-muted-foreground font-semibold mb-1.5 block">
                                Resolution notes
                            </Label>
                            <Textarea
                                value={resolutionNotes}
                                onChange={(e) => setResolutionNotes(e.target.value)}
                                placeholder="What did you do? Visible to other admins and to the audit trail."
                                className="text-sm min-h-[100px]"
                            />
                        </div>

                        {/* Merge into another incident — same-event duplicate reports
                            (e.g. rider + driver both SOS'd the same ride). */}
                        {incident.merged_into_incident_id ? (
                            <div className="rounded-lg border border-border/60 bg-muted/10 p-3 text-xs text-muted-foreground flex items-center gap-2">
                                <GitMerge className="h-3.5 w-3.5" />
                                Already merged into{" "}
                                <span className="font-mono">{incident.merged_into_incident_id.slice(0, 16)}…</span>
                            </div>
                        ) : (
                            <div>
                                <Label className="text-[10px] uppercase tracking-wide text-muted-foreground font-semibold mb-1.5 block">
                                    Duplicate of (merge)
                                </Label>
                                <div className="flex items-center gap-2">
                                    <Input
                                        value={mergeTargetId}
                                        onChange={(e) => setMergeTargetId(e.target.value)}
                                        placeholder="Canonical incident ID"
                                        className="font-mono text-xs h-9"
                                    />
                                    <Button
                                        variant="outline"
                                        size="sm"
                                        onClick={handleMerge}
                                        disabled={merging || !mergeTargetId.trim()}
                                    >
                                        {merging ? <Loader2 className="h-3.5 w-3.5 animate-spin mr-1.5" /> : <GitMerge className="h-3.5 w-3.5 mr-1.5" />}
                                        Merge
                                    </Button>
                                </div>
                            </div>
                        )}

                        <div className="flex items-center justify-end gap-2 pt-1">
                            <Button variant="outline" size="sm" onClick={onClose}>Close</Button>
                            <Button
                                size="sm"
                                onClick={handleSave}
                                disabled={saving}
                                // eslint-disable-next-line no-restricted-syntax -- solid-fill white-text button; dark-mode --success (2.02:1) fails WCAG AA against white text (#2816)
                                className="bg-emerald-600 hover:bg-emerald-700 text-white"
                            >
                                {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin mr-1.5" /> : <CheckCircle className="h-3.5 w-3.5 mr-1.5" />}
                                Save
                            </Button>
                        </div>
                    </CardContent>
                </Card>
            </div>
        </div>
    );
}
