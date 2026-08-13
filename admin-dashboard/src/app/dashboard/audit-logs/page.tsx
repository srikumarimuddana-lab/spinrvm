"use client";

import { Fragment, useEffect, useRef, useState } from "react";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
    Table,
    TableBody,
    TableCell,
    TableHeader,
    TableRow,
} from "@/components/ui/table";
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select";
import { Pagination } from "@/components/ui/pagination";
import { useTableSort, SortableHead } from "@/components/ui/sortable-table";
import {
    Shield,
    Search,
    User,
    Car,
    MapPin,
    CreditCard,
    Settings,
    Ticket,
    RefreshCw,
    Download,
    Activity,
    TrendingUp,
    FileText,
    Building2,
    AlertTriangle,
    Bot,
    Server,
    ChevronRight,
    ChevronDown,
} from "lucide-react";
import { formatDate } from "@/lib/utils";
import { getAuditLogs, getAuditLogTopActors, getAuditLogFacets } from "@/lib/api";
import { useRequireModule } from "@/hooks/useRequireModule";

/** Entity icons are matched on a normalised key so both the singular and
 *  plural spellings writers actually use ("driver"/"drivers",
 *  "ride"/"rides") land on the same icon. */
const ENTITY_ICONS: Record<string, any> = {
    driver: Car,
    driver_document: FileText,
    user: User,
    ride: Car,
    promotion: Ticket,
    service_area: MapPin,
    staff: User,
    admin_staff: User,
    setting: Settings,
    subscription_plan: CreditCard,
    wallet: CreditCard,
    corporate_account: Building2,
    safety_incident: AlertTriangle,
    zoho_desk_ticket: Ticket,
    zoho_desk_config: Settings,
    venue: MapPin,
    ai_console: Bot,
    system: Server,
};

export function entityIcon(entityType?: string) {
    if (!entityType) return Shield;
    const key = entityType.replace(/s$/, "");
    return ENTITY_ICONS[entityType] || ENTITY_ICONS[key] || Shield;
}

/** Colour is derived from the *shape* of the action string rather than a
 *  whitelist. Writers emit specific verbs ("driver_approve", "otp_sent",
 *  "zoho_desk_config_updated"), so the previous fixed map of five generic
 *  actions matched nothing and every badge rendered the same grey. */
const ACTION_CLASSES: Array<{ test: RegExp; color: string }> = [
    // Security-relevant first — these must never be visually buried.
    { test: /(reveal|pii|sin_reveal|breach|lockout|reuse_detected|escalat|ban|suspend)/, color: "bg-red-500/15 text-red-600" },
    { test: /(delete|deleted|reject|declin|cancel|revoke)/, color: "bg-rose-500/15 text-rose-600" },
    { test: /(login|logout|otp|mfa|auth|token)/, color: "bg-purple-500/15 text-purple-600" },
    { test: /(creat|approv|reactivat|enabled|import|generat)/, color: "bg-emerald-500/15 text-emerald-600" },
    { test: /(updat|chang|override|set|sync|recompute|requeu)/, color: "bg-blue-500/15 text-blue-600" },
    { test: /(view|read|export|download|search|suggest)/, color: "bg-amber-500/15 text-amber-600" },
];

export function actionColor(action?: string) {
    if (!action) return "bg-zinc-500/15 text-zinc-600";
    const a = action.toLowerCase();
    return ACTION_CLASSES.find((c) => c.test.test(a))?.color || "bg-zinc-500/15 text-zinc-600";
}

/** "zoho_desk_config_updated" -> "Zoho desk config updated" */
export function humanizeAction(action?: string) {
    if (!action) return "—";
    const s = action.replace(/_/g, " ").trim();
    return s.charAt(0).toUpperCase() + s.slice(1);
}

/** `details` is stored as a JSON string. Pretty-print it when it parses so an
 *  investigation can read fields_changed etc. without copying it into a
 *  JSON formatter; fall back to the raw text when it isn't JSON. */
export function formatDetails(details: unknown): string {
    if (details == null || details === "") return "—";
    if (typeof details === "object") return JSON.stringify(details, null, 2);
    const raw = String(details);
    try {
        return JSON.stringify(JSON.parse(raw), null, 2);
    } catch {
        return raw;
    }
}

/** One-line gist for the collapsed row: the most investigation-relevant key
 *  if the details blob is JSON, else the raw string. */
export function detailsSummary(details: unknown): string {
    if (details == null || details === "") return "—";
    let obj: any = details;
    if (typeof details === "string") {
        try {
            obj = JSON.parse(details);
        } catch {
            return details;
        }
    }
    if (obj && typeof obj === "object") {
        for (const key of ["fields_changed", "updated_fields", "changed_fields", "fields", "note", "reason"]) {
            const v = obj[key];
            if (Array.isArray(v) && v.length) return `${key}: ${v.join(", ")}`;
            if (typeof v === "string" && v) return `${key}: ${v}`;
        }
        const keys = Object.keys(obj).filter((k) => k !== "actor_id" && k !== "actor_role");
        if (keys.length) return keys.join(", ");
    }
    return String(details);
}

const PAGE_SIZE = 50;

export default function AuditLogsPage() {
    const { allowed } = useRequireModule("audit");
    const [logs, setLogs] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [search, setSearch] = useState("");
    const [actionFilter, setActionFilter] = useState("all");
    const [entityFilter, setEntityFilter] = useState("all");
    const [startDate, setStartDate] = useState("");
    const [endDate, setEndDate] = useState("");
    const [page, setPage] = useState(0);
    const [hasNextPage, setHasNextPage] = useState(false);
    const [expanded, setExpanded] = useState<Record<string, boolean>>({});
    const reqIdRef = useRef(0);
    const { sorted, sort, toggle } = useTableSort(logs);

    // Filter options come from the data. The previous hardcoded lists
    // ("created"/"updated"/… and "driver"/"promotion"/…) matched almost
    // nothing that writers actually emit, and an unmatched filter returns an
    // empty result rather than an error — so the filters silently blanked
    // the table instead of narrowing it.
    const [facets, setFacets] = useState<{
        actions: Array<{ value: string; count: number }>;
        entity_types: Array<{ value: string; count: number }>;
        rows_scanned_capped: boolean;
    }>({ actions: [], entity_types: [], rows_scanned_capped: false });

    useEffect(() => {
        let cancelled = false;
        getAuditLogFacets({ days: 90 })
            .then((res) => {
                if (cancelled) return;
                setFacets({
                    actions: res.actions ?? [],
                    entity_types: res.entity_types ?? [],
                    rows_scanned_capped: !!res.rows_scanned_capped,
                });
            })
            .catch(() => {
                if (!cancelled) setFacets({ actions: [], entity_types: [], rows_scanned_capped: false });
            });
        return () => {
            cancelled = true;
        };
    }, []);

    /** A date input gives a bare day; widen it to cover the whole local day. */
    const toIsoStart = (d: string) => (d ? new Date(`${d}T00:00:00`).toISOString() : undefined);
    const toIsoEnd = (d: string) => (d ? new Date(`${d}T23:59:59.999`).toISOString() : undefined);

    // Corporate + admin portal review, round 2: "no 'who touched the most'
    // rollup views — every threat hunt needs raw SQL."
    const [topActors, setTopActors] = useState<Awaited<ReturnType<typeof getAuditLogTopActors>>["actors"]>([]);
    const [topActorsDays, setTopActorsDays] = useState(7);
    const [topActorsLoading, setTopActorsLoading] = useState(true);

    useEffect(() => {
        let cancelled = false;
        setTopActorsLoading(true);
        getAuditLogTopActors({ days: topActorsDays, limit: 10 })
            .then((res) => {
                if (!cancelled) setTopActors(res.actors);
            })
            .catch(() => {
                if (!cancelled) setTopActors([]);
            })
            .finally(() => {
                if (!cancelled) setTopActorsLoading(false);
            });
        return () => {
            cancelled = true;
        };
    }, [topActorsDays]);

    const fetchLogs = async () => {
        setLoading(true);
        const reqId = ++reqIdRef.current;
        try {
            const data = await getAuditLogs({
                limit: PAGE_SIZE + 1,
                offset: page * PAGE_SIZE,
                action: actionFilter !== "all" ? actionFilter : undefined,
                entity_type: entityFilter !== "all" ? entityFilter : undefined,
                search: search.trim() || undefined,
                start: toIsoStart(startDate),
                end: toIsoEnd(endDate),
            });
            if (reqId !== reqIdRef.current) return;
            const arr = Array.isArray(data) ? data : [];
            setHasNextPage(arr.length > PAGE_SIZE);
            setLogs(arr.slice(0, PAGE_SIZE));
        } catch {
            if (reqId !== reqIdRef.current) return;
            setLogs([]);
            setHasNextPage(false);
        } finally {
            if (reqId === reqIdRef.current) setLoading(false);
        }
    };

    // Reset to page 0 when filters change (search debounced).
    useEffect(() => {
        const t = setTimeout(() => {
            if (page !== 0) setPage(0);
            else fetchLogs();
        }, 300);
        return () => clearTimeout(t);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [search, actionFilter, entityFilter, startDate, endDate]);

    // Re-fetch when page changes.
    useEffect(() => {
        fetchLogs();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [page]);

    const handleExport = () => {
        // actor_id / actor_role / request_id are what an investigation
        // actually joins on — exporting without them meant re-querying the DB.
        const headers = [
            "Time", "User", "Actor ID", "Actor Role", "Action",
            "Entity Type", "Entity ID", "Request ID", "Details",
        ];
        const escapeCSV = (val: string) => `"${String(val ?? "").replace(/"/g, '""')}"`;
        const rows = logs.map((log) => [
            formatDate(log.created_at),
            escapeCSV(log.user_email || ""),
            escapeCSV(log.actor_id || ""),
            escapeCSV(log.actor_role || ""),
            log.action,
            log.entity_type,
            log.entity_id,
            escapeCSV(log.request_id || ""),
            escapeCSV(typeof log.details === "string" ? log.details : JSON.stringify(log.details ?? "")),
        ]);
        const csv = [headers.join(","), ...rows.map((r) => r.join(","))].join("\n");
        const blob = new Blob([csv], { type: "text/csv" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `audit-logs-${new Date().toISOString().split("T")[0]}.csv`;
        a.click();
        URL.revokeObjectURL(url);
    };

    if (!allowed) return null;

    return (
        <div className="space-y-6">
            {/* Header */}
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-3xl font-bold tracking-tight flex items-center gap-2">
                        <Shield className="h-8 w-8 text-violet-500" />
                        Audit Logs
                    </h1>
                    <p className="text-muted-foreground mt-1">
                        Track all admin actions and changes across the system.
                    </p>
                </div>
                <div className="flex gap-2">
                    <Button variant="outline" size="sm" onClick={fetchLogs} disabled={loading}>
                        <RefreshCw className={`mr-2 h-4 w-4 ${loading ? "animate-spin" : ""}`} /> Refresh
                    </Button>
                    <Button variant="outline" size="sm" onClick={handleExport} disabled={logs.length === 0}>
                        <Download className="mr-2 h-4 w-4" /> Export Page
                    </Button>
                </div>
            </div>

            {/* Filters */}
            <div className="flex flex-wrap items-center gap-3">
                <div className="relative flex-1 max-w-sm">
                    <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                    <Input
                        placeholder="Search by user, entity, or details..."
                        value={search}
                        onChange={(e) => setSearch(e.target.value)}
                        className="pl-9"
                    />
                </div>
                <Select value={actionFilter} onValueChange={setActionFilter}>
                    <SelectTrigger className="w-56" aria-label="Filter by action">
                        <SelectValue placeholder="Filter by action" />
                    </SelectTrigger>
                    <SelectContent className="max-h-80">
                        <SelectItem value="all">All Actions</SelectItem>
                        {facets.actions.map((a) => (
                            <SelectItem key={a.value} value={a.value}>
                                {humanizeAction(a.value)} ({a.count})
                            </SelectItem>
                        ))}
                    </SelectContent>
                </Select>
                <Select value={entityFilter} onValueChange={setEntityFilter}>
                    <SelectTrigger className="w-56" aria-label="Filter by entity">
                        <SelectValue placeholder="Filter by entity" />
                    </SelectTrigger>
                    <SelectContent className="max-h-80">
                        <SelectItem value="all">All Entities</SelectItem>
                        {facets.entity_types.map((e) => (
                            <SelectItem key={e.value} value={e.value}>
                                {humanizeAction(e.value)} ({e.count})
                            </SelectItem>
                        ))}
                    </SelectContent>
                </Select>
                <div className="flex items-center gap-2">
                    <Input
                        type="date"
                        aria-label="From date"
                        className="w-40"
                        value={startDate}
                        max={endDate || undefined}
                        onChange={(e) => setStartDate(e.target.value)}
                    />
                    <span className="text-muted-foreground text-sm">to</span>
                    <Input
                        type="date"
                        aria-label="To date"
                        className="w-40"
                        value={endDate}
                        min={startDate || undefined}
                        onChange={(e) => setEndDate(e.target.value)}
                    />
                </div>
                {(search || actionFilter !== "all" || entityFilter !== "all" || startDate || endDate) && (
                    <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => {
                            setSearch("");
                            setActionFilter("all");
                            setEntityFilter("all");
                            setStartDate("");
                            setEndDate("");
                        }}
                    >
                        Clear filters
                    </Button>
                )}
            </div>

            {/* Top actors — threat-hunting rollup, no raw SQL needed */}
            <Card className="border-border/50">
                <CardContent className="p-4">
                    <div className="flex items-center justify-between mb-3">
                        <div className="flex items-center gap-2 text-sm font-semibold">
                            <TrendingUp className="h-4 w-4 text-violet-500" />
                            Most active actors
                        </div>
                        <Select value={String(topActorsDays)} onValueChange={(v) => setTopActorsDays(Number(v))}>
                            <SelectTrigger className="w-32" aria-label="Rollup window">
                                <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                                <SelectItem value="1">Last 24h</SelectItem>
                                <SelectItem value="7">Last 7 days</SelectItem>
                                <SelectItem value="30">Last 30 days</SelectItem>
                                <SelectItem value="90">Last 90 days</SelectItem>
                            </SelectContent>
                        </Select>
                    </div>
                    {topActorsLoading ? (
                        <div className="text-sm text-muted-foreground py-2">Loading…</div>
                    ) : topActors.length === 0 ? (
                        <div className="text-sm text-muted-foreground py-2">No activity in this window.</div>
                    ) : (
                        <div className="flex flex-wrap gap-2">
                            {topActors.map((a) => (
                                <div
                                    key={a.actor_id}
                                    className="flex items-center gap-2 rounded-md border border-border/50 px-3 py-1.5 text-sm"
                                    title={a.top_actions.map((t) => `${t.action}: ${t.count}`).join(", ")}
                                >
                                    <span className="font-mono text-xs text-muted-foreground">
                                        {a.actor_id.slice(0, 8)}
                                    </span>
                                    <Badge variant="secondary">{a.action_count}</Badge>
                                </div>
                            ))}
                        </div>
                    )}
                </CardContent>
            </Card>

            {/* Table */}
            <Card className="border-border/50">
                <CardContent className="p-0">
                    {loading ? (
                        <div className="flex items-center justify-center p-12">
                            <div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" />
                        </div>
                    ) : logs.length === 0 ? (
                        <div className="text-center py-16">
                            <Shield className="h-12 w-12 text-muted-foreground/30 mx-auto mb-4" />
                            <h3 className="text-lg font-semibold">No audit logs found</h3>
                            <p className="text-muted-foreground mt-1">
                                {search || actionFilter !== "all" || entityFilter !== "all" || startDate || endDate
                                    ? "Try adjusting your filters or widening the date range."
                                    : "Admin actions will be recorded here."}
                            </p>
                        </div>
                    ) : (
                        <>
                            <Table>
                                <TableHeader>
                                    <TableRow>
                                        <SortableHead column="created_at" sort={sort} onSort={toggle}>Time</SortableHead>
                                        <SortableHead column="user_email" sort={sort} onSort={toggle}>User</SortableHead>
                                        <SortableHead column="action" sort={sort} onSort={toggle}>Action</SortableHead>
                                        <SortableHead column="entity_type" sort={sort} onSort={toggle}>Entity</SortableHead>
                                        <SortableHead column="details" sort={sort} onSort={toggle}>Details</SortableHead>
                                    </TableRow>
                                </TableHeader>
                                <TableBody>
                                    {sorted.map((log) => {
                                        const Icon = entityIcon(log.entity_type);
                                        const isOpen = !!expanded[log.id];
                                        return (
                                            <Fragment key={log.id}>
                                                <TableRow
                                                    className="cursor-pointer"
                                                    onClick={() =>
                                                        setExpanded((p) => ({ ...p, [log.id]: !p[log.id] }))
                                                    }
                                                >
                                                    <TableCell className="text-sm text-muted-foreground whitespace-nowrap">
                                                        <span className="flex items-center gap-1.5">
                                                            <button
                                                                type="button"
                                                                aria-label={isOpen ? "Collapse details" : "Expand details"}
                                                                aria-expanded={isOpen}
                                                                className="text-muted-foreground"
                                                                onClick={(e) => {
                                                                    e.stopPropagation();
                                                                    setExpanded((p) => ({ ...p, [log.id]: !p[log.id] }));
                                                                }}
                                                            >
                                                                {isOpen ? (
                                                                    <ChevronDown className="h-4 w-4" />
                                                                ) : (
                                                                    <ChevronRight className="h-4 w-4" />
                                                                )}
                                                            </button>
                                                            {formatDate(log.created_at)}
                                                        </span>
                                                    </TableCell>
                                                    <TableCell className="font-medium text-sm">
                                                        {log.user_email || log.actor_id || "System"}
                                                    </TableCell>
                                                    <TableCell>
                                                        <Badge className={actionColor(log.action)}>
                                                            {humanizeAction(log.action)}
                                                        </Badge>
                                                    </TableCell>
                                                    <TableCell>
                                                        <div className="flex items-center gap-2">
                                                            <Icon className="h-4 w-4 text-muted-foreground" />
                                                            <span className="text-sm">
                                                                {humanizeAction(log.entity_type)}
                                                            </span>
                                                            {log.entity_id && (
                                                                <span className="text-xs text-muted-foreground font-mono">
                                                                    {log.entity_id.slice(0, 8)}
                                                                    {log.entity_id.length > 8 ? "…" : ""}
                                                                </span>
                                                            )}
                                                        </div>
                                                    </TableCell>
                                                    <TableCell className="text-sm text-muted-foreground max-w-xs truncate">
                                                        {detailsSummary(log.details)}
                                                    </TableCell>
                                                </TableRow>
                                                {isOpen && (
                                                    <TableRow className="bg-muted/30 hover:bg-muted/30">
                                                        <TableCell colSpan={5} className="p-4">
                                                            <div className="grid gap-4 sm:grid-cols-3">
                                                                <div className="space-y-1">
                                                                    <p className="text-xs font-medium text-muted-foreground">Actor</p>
                                                                    <p className="font-mono text-xs break-all">
                                                                        {log.actor_id || "—"}
                                                                    </p>
                                                                    {log.actor_role && (
                                                                        <Badge variant="secondary" className="text-xs">
                                                                            {log.actor_role}
                                                                        </Badge>
                                                                    )}
                                                                </div>
                                                                <div className="space-y-1">
                                                                    <p className="text-xs font-medium text-muted-foreground">Entity ID</p>
                                                                    <p className="font-mono text-xs break-all">
                                                                        {log.entity_id || "—"}
                                                                    </p>
                                                                </div>
                                                                <div className="space-y-1">
                                                                    {/* The join key to backend logs and Sentry events —
                                                                        see utils/audit_logger.py on why it's stored. */}
                                                                    <p className="text-xs font-medium text-muted-foreground">
                                                                        Request ID
                                                                    </p>
                                                                    <p className="font-mono text-xs break-all">
                                                                        {log.request_id || "— (background job)"}
                                                                    </p>
                                                                </div>
                                                            </div>
                                                            <div className="mt-4 space-y-1">
                                                                <p className="text-xs font-medium text-muted-foreground">Details</p>
                                                                <pre className="overflow-x-auto rounded-md border bg-background p-3 text-xs">
                                                                    {formatDetails(log.details)}
                                                                </pre>
                                                            </div>
                                                        </TableCell>
                                                    </TableRow>
                                                )}
                                            </Fragment>
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
                        </>
                    )}
                </CardContent>
            </Card>

            {/* Activity hint */}
            {!loading && logs.length > 0 && (
                <p className="text-xs text-muted-foreground flex items-center gap-1.5">
                    <Activity className="h-3 w-3" />
                    Showing {logs.length} log{logs.length === 1 ? "" : "s"} on this page.
                </p>
            )}
        </div>
    );
}
