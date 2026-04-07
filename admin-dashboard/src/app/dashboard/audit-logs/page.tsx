"use client";

import { useEffect, useState, useMemo } from "react";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
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
    ChevronLeft,
    ChevronRight,
    Activity,
    Clock,
} from "lucide-react";
import { formatDate } from "@/lib/utils";
import { getAuditLogs } from "@/lib/api";

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

const PER_PAGE = 25;

export default function AuditLogsPage() {
    const [logs, setLogs] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [search, setSearch] = useState("");
    const [actionFilter, setActionFilter] = useState("all");
    const [entityFilter, setEntityFilter] = useState("all");
    const [page, setPage] = useState(1);

    const fetchLogs = async () => {
        setLoading(true);
        try {
            const data = await getAuditLogs(500);
            setLogs(Array.isArray(data) ? data : []);
        } catch {
            setLogs([]);
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        fetchLogs();
    }, []);

    const filtered = useMemo(() => {
        return logs.filter((log) => {
            const matchSearch =
                !search ||
                log.user_email?.toLowerCase().includes(search.toLowerCase()) ||
                log.entity_type?.toLowerCase().includes(search.toLowerCase()) ||
                log.entity_id?.toLowerCase().includes(search.toLowerCase()) ||
                log.details?.toLowerCase().includes(search.toLowerCase());
            const matchAction = actionFilter === "all" || log.action === actionFilter;
            const matchEntity = entityFilter === "all" || log.entity_type === entityFilter;
            return matchSearch && matchAction && matchEntity;
        });
    }, [logs, search, actionFilter, entityFilter]);

    const totalPages = Math.max(1, Math.ceil(filtered.length / PER_PAGE));
    const paginated = filtered.slice((page - 1) * PER_PAGE, page * PER_PAGE);

    // Reset to page 1 when filters change
    useEffect(() => {
        setPage(1);
    }, [search, actionFilter, entityFilter]);

    const stats = useMemo(() => {
        const today = new Date().toISOString().split("T")[0];
        return {
            total: logs.length,
            today: logs.filter((l) => l.created_at?.startsWith(today)).length,
            uniqueUsers: new Set(logs.map((l) => l.user_email).filter(Boolean)).size,
            latestAction: logs.length > 0 ? logs[0].action : "—",
        };
    }, [logs]);

    const handleExport = () => {
        const headers = ["Time", "User", "Action", "Entity Type", "Entity ID", "Details"];
        const escapeCSV = (val: string) => `"${String(val || "").replace(/"/g, '""')}"`;
        const rows = filtered.map((log) => [
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
                    <Button variant="outline" size="sm" onClick={handleExport} disabled={filtered.length === 0}>
                        <Download className="mr-2 h-4 w-4" /> Export
                    </Button>
                </div>
            </div>

            {/* Stats */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                <Card>
                    <CardContent className="pt-4 pb-3">
                        <div className="flex items-center gap-2">
                            <Shield className="h-5 w-5 text-violet-500" />
                            <div>
                                <p className="text-xs text-muted-foreground">Total Logs</p>
                                <p className="text-2xl font-bold">{stats.total}</p>
                            </div>
                        </div>
                    </CardContent>
                </Card>
                <Card>
                    <CardContent className="pt-4 pb-3">
                        <div className="flex items-center gap-2">
                            <Clock className="h-5 w-5 text-blue-500" />
                            <div>
                                <p className="text-xs text-muted-foreground">Today</p>
                                <p className="text-2xl font-bold">{stats.today}</p>
                            </div>
                        </div>
                    </CardContent>
                </Card>
                <Card>
                    <CardContent className="pt-4 pb-3">
                        <div className="flex items-center gap-2">
                            <User className="h-5 w-5 text-emerald-500" />
                            <div>
                                <p className="text-xs text-muted-foreground">Unique Users</p>
                                <p className="text-2xl font-bold">{stats.uniqueUsers}</p>
                            </div>
                        </div>
                    </CardContent>
                </Card>
                <Card>
                    <CardContent className="pt-4 pb-3">
                        <div className="flex items-center gap-2">
                            <Activity className="h-5 w-5 text-amber-500" />
                            <div>
                                <p className="text-xs text-muted-foreground">Latest Action</p>
                                <p className="text-2xl font-bold capitalize">{stats.latestAction}</p>
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
                        placeholder="Search by user, entity, or details..."
                        value={search}
                        onChange={(e) => setSearch(e.target.value)}
                        className="pl-9"
                    />
                </div>
                <Select value={actionFilter} onValueChange={setActionFilter}>
                    <SelectTrigger className="w-44">
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
                    <SelectTrigger className="w-44">
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
                            <Shield className="h-12 w-12 text-muted-foreground/30 mx-auto mb-4" />
                            <h3 className="text-lg font-semibold">No audit logs found</h3>
                            <p className="text-muted-foreground mt-1">Admin actions will be recorded here.</p>
                        </div>
                    ) : (
                        <>
                            <Table>
                                <TableHeader>
                                    <TableRow>
                                        <TableHead>Time</TableHead>
                                        <TableHead>User</TableHead>
                                        <TableHead>Action</TableHead>
                                        <TableHead>Entity</TableHead>
                                        <TableHead>Details</TableHead>
                                    </TableRow>
                                </TableHeader>
                                <TableBody>
                                    {paginated.map((log) => {
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

                            {/* Pagination */}
                            <div className="flex items-center justify-between px-4 py-3 border-t">
                                <p className="text-sm text-muted-foreground">
                                    Showing {(page - 1) * PER_PAGE + 1}–{Math.min(page * PER_PAGE, filtered.length)} of {filtered.length} logs
                                </p>
                                <div className="flex items-center gap-2">
                                    <Button
                                        variant="outline"
                                        size="sm"
                                        onClick={() => setPage((p) => Math.max(1, p - 1))}
                                        disabled={page === 1}
                                    >
                                        <ChevronLeft className="h-4 w-4" />
                                    </Button>
                                    <span className="text-sm text-muted-foreground">
                                        Page {page} of {totalPages}
                                    </span>
                                    <Button
                                        variant="outline"
                                        size="sm"
                                        onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                                        disabled={page === totalPages}
                                    >
                                        <ChevronRight className="h-4 w-4" />
                                    </Button>
                                </div>
                            </div>
                        </>
                    )}
                </CardContent>
            </Card>
        </div>
    );
}
