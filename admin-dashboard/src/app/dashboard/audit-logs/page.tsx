"use client";

import { useEffect, useRef, useState } from "react";
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
} from "lucide-react";
import { formatDate } from "@/lib/utils";
import { getAuditLogs, getAuditLogTopActors } from "@/lib/api";
import { useRequireModule } from "@/hooks/useRequireModule";

const ENTITY_ICONS: Record<string, any> = {
    driver: Car,
    user: User,
    ride: Car,
    promotion: Ticket,
    service_area: MapPin,
    staff: User,
    setting: Settings,
    subscription: CreditCard,
};

const ACTION_CONFIG: Record<string, { label: string; color: string }> = {
    created: { label: "Created", color: "bg-emerald-500/15 text-emerald-600" },
    updated: { label: "Updated", color: "bg-blue-500/15 text-blue-600" },
    deleted: { label: "Deleted", color: "bg-red-500/15 text-red-600" },
    login: { label: "Login", color: "bg-purple-500/15 text-purple-600" },
    status_change: { label: "Status Change", color: "bg-amber-500/15 text-amber-600" },
};

const PAGE_SIZE = 50;

export default function AuditLogsPage() {
    const { allowed } = useRequireModule("audit");
    const [logs, setLogs] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [search, setSearch] = useState("");
    const [actionFilter, setActionFilter] = useState("all");
    const [entityFilter, setEntityFilter] = useState("all");
    const [page, setPage] = useState(0);
    const [hasNextPage, setHasNextPage] = useState(false);
    const reqIdRef = useRef(0);
    const { sorted, sort, toggle } = useTableSort(logs);

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
    }, [search, actionFilter, entityFilter]);

    // Re-fetch when page changes.
    useEffect(() => {
        fetchLogs();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [page]);

    const handleExport = () => {
        const headers = ["Time", "User", "Action", "Entity Type", "Entity ID", "Details"];
        const escapeCSV = (val: string) => `"${String(val || "").replace(/"/g, '""')}"`;
        const rows = logs.map((log) => [
            formatDate(log.created_at),
            escapeCSV(log.user_email || ""),
            log.action,
            log.entity_type,
            log.entity_id,
            escapeCSV(log.details || ""),
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
                    <SelectTrigger className="w-44" aria-label="Filter by action">
                        <SelectValue placeholder="Filter by action" />
                    </SelectTrigger>
                    <SelectContent>
                        <SelectItem value="all">All Actions</SelectItem>
                        <SelectItem value="created">Created</SelectItem>
                        <SelectItem value="updated">Updated</SelectItem>
                        <SelectItem value="deleted">Deleted</SelectItem>
                        <SelectItem value="login">Login</SelectItem>
                        <SelectItem value="status_change">Status Change</SelectItem>
                    </SelectContent>
                </Select>
                <Select value={entityFilter} onValueChange={setEntityFilter}>
                    <SelectTrigger className="w-44" aria-label="Filter by entity">
                        <SelectValue placeholder="Filter by entity" />
                    </SelectTrigger>
                    <SelectContent>
                        <SelectItem value="all">All Entities</SelectItem>
                        <SelectItem value="driver">Driver</SelectItem>
                        <SelectItem value="user">User</SelectItem>
                        <SelectItem value="ride">Ride</SelectItem>
                        <SelectItem value="promotion">Promotion</SelectItem>
                        <SelectItem value="service_area">Service Area</SelectItem>
                        <SelectItem value="staff">Staff</SelectItem>
                        <SelectItem value="setting">Setting</SelectItem>
                        <SelectItem value="subscription">Subscription</SelectItem>
                    </SelectContent>
                </Select>
                {(search || actionFilter !== "all" || entityFilter !== "all") && (
                    <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => {
                            setSearch("");
                            setActionFilter("all");
                            setEntityFilter("all");
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
                                {search || actionFilter !== "all" || entityFilter !== "all"
                                    ? "Try adjusting your filters."
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
                                        const Icon = ENTITY_ICONS[log.entity_type] || Shield;
                                        const actionCfg = ACTION_CONFIG[log.action];
                                        return (
                                            <TableRow key={log.id}>
                                                <TableCell className="text-sm text-muted-foreground whitespace-nowrap">
                                                    {formatDate(log.created_at)}
                                                </TableCell>
                                                <TableCell className="font-medium text-sm">
                                                    {log.user_email || "System"}
                                                </TableCell>
                                                <TableCell>
                                                    <Badge className={actionCfg?.color || "bg-zinc-500/15 text-zinc-600"}>
                                                        {actionCfg?.label || log.action}
                                                    </Badge>
                                                </TableCell>
                                                <TableCell>
                                                    <div className="flex items-center gap-2">
                                                        <Icon className="h-4 w-4 text-muted-foreground" />
                                                        <span className="text-sm capitalize">{log.entity_type?.replace("_", " ")}</span>
                                                        <span className="text-xs text-muted-foreground font-mono">
                                                            {log.entity_id?.slice(0, 8)}...
                                                        </span>
                                                    </div>
                                                </TableCell>
                                                <TableCell className="text-sm text-muted-foreground max-w-xs truncate">
                                                    {log.details || "—"}
                                                </TableCell>
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
