"use client";

import { useEffect, useRef, useState, useCallback } from "react";
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
    SelectGroup,
    SelectItem,
    SelectLabel,
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
    ChevronDown,
    ChevronRight,
    Building2,
    FileText,
    Calendar,
    X,
} from "lucide-react";
import { formatDate } from "@/lib/utils";
import { getAuditLogs, getAuditLogTopActors } from "@/lib/api";
import type { AuditLogEntry } from "@/lib/api";
import { useRequireModule } from "@/hooks/useRequireModule";

const ENTITY_ICONS: Record<string, any> = {
    driver: Car,
    drivers: Car,
    user: User,
    users: User,
    ride: Car,
    rides: Car,
    promotion: Ticket,
    promotions: Ticket,
    service_area: MapPin,
    staff: User,
    setting: Settings,
    settings: Settings,
    subscription: CreditCard,
    subscriptions: CreditCard,
    corporate_accounts: Building2,
    corporate_account: Building2,
    corporate_subscriptions: Building2,
    corporate_wallet: CreditCard,
    corporate_bookings: FileText,
    dispute: FileText,
    disputes: FileText,
};

const ACTION_CONFIG: Record<string, { label: string; color: string }> = {
    // Access / Auth
    login: { label: "Login", color: "bg-purple-500/15 text-purple-600" },
    break_glass_access: { label: "Break Glass", color: "bg-red-500/15 text-red-600" },
    pii_revealed: { label: "PII Revealed", color: "bg-orange-500/15 text-orange-600" },
    settings_credential_revealed: { label: "Credential Revealed", color: "bg-orange-500/15 text-orange-600" },
    // Staff
    staff_created: { label: "Staff Created", color: "bg-emerald-500/15 text-emerald-600" },
    staff_updated: { label: "Staff Updated", color: "bg-blue-500/15 text-blue-600" },
    staff_deleted: { label: "Staff Deleted", color: "bg-red-500/15 text-red-600" },
    staff_mfa_reset: { label: "MFA Reset", color: "bg-amber-500/15 text-amber-600" },
    // Users
    user_status_change: { label: "User Status", color: "bg-amber-500/15 text-amber-600" },
    flags_change: { label: "Flags Change", color: "bg-amber-500/15 text-amber-600" },
    // Rides
    ride_created: { label: "Ride Created", color: "bg-emerald-500/15 text-emerald-600" },
    ride_cancelled: { label: "Ride Cancelled", color: "bg-red-500/15 text-red-600" },
    ride_cancelled_by_admin: { label: "Admin Cancelled", color: "bg-red-500/15 text-red-600" },
    force_complete_ride: { label: "Force Complete", color: "bg-amber-500/15 text-amber-600" },
    admin_create_ride: { label: "Admin Ride", color: "bg-emerald-500/15 text-emerald-600" },
    send_ride_receipt: { label: "Receipt Sent", color: "bg-blue-500/15 text-blue-600" },
    send_ride_invoice: { label: "Invoice Sent", color: "bg-blue-500/15 text-blue-600" },
    resend_ride_invoice: { label: "Invoice Resent", color: "bg-blue-500/15 text-blue-600" },
    // Wallet
    wallet_credit: { label: "Wallet Credit", color: "bg-emerald-500/15 text-emerald-600" },
    wallet_debit: { label: "Wallet Debit", color: "bg-red-500/15 text-red-600" },
    // Exports
    export_rides: { label: "Export Rides", color: "bg-cyan-500/15 text-cyan-600" },
    export_drivers: { label: "Export Drivers", color: "bg-cyan-500/15 text-cyan-600" },
    export_users: { label: "Export Users", color: "bg-cyan-500/15 text-cyan-600" },
    // Corporate
    create_corporate_account: { label: "Corp Created", color: "bg-emerald-500/15 text-emerald-600" },
    update_corporate_account: { label: "Corp Updated", color: "bg-blue-500/15 text-blue-600" },
    delete_corporate_account: { label: "Corp Deleted", color: "bg-red-500/15 text-red-600" },
    change_company_status: { label: "Corp Status", color: "bg-amber-500/15 text-amber-600" },
    corporate_self_serve_signup: { label: "Corp Signup", color: "bg-emerald-500/15 text-emerald-600" },
    corporate_kyb_submitted: { label: "KYB Submitted", color: "bg-blue-500/15 text-blue-600" },
    kyb_document_confirmed: { label: "KYB Confirmed", color: "bg-emerald-500/15 text-emerald-600" },
    kyb_review: { label: "KYB Review", color: "bg-amber-500/15 text-amber-600" },
    corporate_member_invited: { label: "Member Invited", color: "bg-blue-500/15 text-blue-600" },
    corporate_member_status_changed: { label: "Member Status", color: "bg-amber-500/15 text-amber-600" },
    corporate_wallet_manual_topup: { label: "Corp Top-up", color: "bg-emerald-500/15 text-emerald-600" },
    corporate_wallet_manual_adjust: { label: "Corp Adjust", color: "bg-amber-500/15 text-amber-600" },
    corporate_subscription_assigned: { label: "Sub Assigned", color: "bg-emerald-500/15 text-emerald-600" },
    corporate_subscription_cancelled: { label: "Sub Cancelled", color: "bg-red-500/15 text-red-600" },
    // Settings
    settings_updated: { label: "Settings Updated", color: "bg-blue-500/15 text-blue-600" },
    ride_offer_sound_uploaded: { label: "Sound Uploaded", color: "bg-blue-500/15 text-blue-600" },
    // DSAR / Safety
    dsar_deletion_requested: { label: "Deletion Request", color: "bg-orange-500/15 text-orange-600" },
    dsar_deletion_executed: { label: "Deletion Done", color: "bg-red-500/15 text-red-600" },
    safety_incident_auto_escalated: { label: "Safety Escalated", color: "bg-red-500/15 text-red-600" },
    // Payouts
    payouts_period_closed: { label: "Payout Closed", color: "bg-blue-500/15 text-blue-600" },
    // Legacy / generic
    created: { label: "Created", color: "bg-emerald-500/15 text-emerald-600" },
    updated: { label: "Updated", color: "bg-blue-500/15 text-blue-600" },
    deleted: { label: "Deleted", color: "bg-red-500/15 text-red-600" },
    status_change: { label: "Status Change", color: "bg-amber-500/15 text-amber-600" },
};

function formatDetails(details: string | null | undefined): string {
    if (!details) return "";
    if (typeof details !== "string") {
        try {
            return JSON.stringify(details, null, 2);
        } catch {
            return String(details);
        }
    }
    try {
        const parsed = JSON.parse(details);
        if (typeof parsed === "object" && parsed !== null) {
            const { actor_id: _a, actor_role: _r, ...rest } = parsed;
            const entries = Object.entries(rest);
            if (entries.length === 0) return "";
            return entries
                .map(([k, v]) => `${k}: ${typeof v === "object" ? JSON.stringify(v) : v}`)
                .join(", ");
        }
        return details;
    } catch {
        return details;
    }
}

function formatEntityType(raw: string | undefined): string {
    if (!raw) return "";
    return raw.replace(/_/g, " ");
}

const PAGE_SIZE = 50;

export default function AuditLogsPage() {
    const { allowed } = useRequireModule("audit");
    const [logs, setLogs] = useState<AuditLogEntry[]>([]);
    const [loading, setLoading] = useState(true);
    const [search, setSearch] = useState("");
    const [actionFilter, setActionFilter] = useState("all");
    const [entityFilter, setEntityFilter] = useState("all");
    const [startDate, setStartDate] = useState("");
    const [endDate, setEndDate] = useState("");
    const [page, setPage] = useState(0);
    const [hasNextPage, setHasNextPage] = useState(false);
    const [expandedRow, setExpandedRow] = useState<string | null>(null);
    const reqIdRef = useRef(0);
    const mountedRef = useRef(false);
    const { sorted, sort, toggle } = useTableSort(logs);

    const [topActors, setTopActors] = useState<Awaited<ReturnType<typeof getAuditLogTopActors>>["actors"]>([]);
    const [topActorsDays, setTopActorsDays] = useState(7);
    const [topActorsLoading, setTopActorsLoading] = useState(true);

    useEffect(() => {
        let cancelled = false;
        setTopActorsLoading(true);
        getAuditLogTopActors({ days: topActorsDays, limit: 10 })
            .then((res) => {
                if (!cancelled) setTopActors(res.actors ?? []);
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

    const fetchLogs = useCallback(async () => {
        setLoading(true);
        const reqId = ++reqIdRef.current;
        try {
            const data = await getAuditLogs({
                limit: PAGE_SIZE + 1,
                offset: page * PAGE_SIZE,
                action: actionFilter !== "all" ? actionFilter : undefined,
                entity_type: entityFilter !== "all" ? entityFilter : undefined,
                search: search.trim() || undefined,
                start_date: startDate || undefined,
                end_date: endDate ? `${endDate}T23:59:59Z` : undefined,
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
    }, [page, actionFilter, entityFilter, search, startDate, endDate]);

    useEffect(() => {
        if (!mountedRef.current) {
            mountedRef.current = true;
            fetchLogs();
            return;
        }
        const t = setTimeout(() => {
            if (page !== 0) setPage(0);
            else fetchLogs();
        }, 300);
        return () => clearTimeout(t);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [search, actionFilter, entityFilter, startDate, endDate]);

    useEffect(() => {
        if (mountedRef.current) fetchLogs();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [page]);

    const hasFilters =
        search !== "" || actionFilter !== "all" || entityFilter !== "all" || startDate !== "" || endDate !== "";

    const handleClearFilters = () => {
        setSearch("");
        setActionFilter("all");
        setEntityFilter("all");
        setStartDate("");
        setEndDate("");
    };

    const handleExport = () => {
        const headers = ["Time", "User", "Action", "Entity Type", "Entity ID", "Details"];
        const esc = (val: unknown) => `"${String(val ?? "").replace(/"/g, '""')}"`;
        const rows = logs.map((log) => [
            esc(formatDate(log.created_at)),
            esc(log.actor_email || log.actor_id || ""),
            esc(log.action),
            esc(log.entity_type),
            esc(log.entity_id),
            esc(formatDetails(log.details)),
        ]);
        const csv = [headers.map(esc).join(","), ...rows.map((r) => r.join(","))].join("\n");
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

            {/* Filters — row 1: search + action + entity */}
            <div className="flex flex-wrap items-center gap-3">
                <div className="relative flex-1 max-w-sm">
                    <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                    <Input
                        placeholder="Search by email, entity ID, or details..."
                        value={search}
                        onChange={(e) => setSearch(e.target.value)}
                        className="pl-9"
                    />
                </div>
                <Select value={actionFilter} onValueChange={setActionFilter}>
                    <SelectTrigger className="w-48" aria-label="Filter by action">
                        <SelectValue placeholder="Filter by action" />
                    </SelectTrigger>
                    <SelectContent>
                        <SelectItem value="all">All Actions</SelectItem>
                        <SelectGroup>
                            <SelectLabel>Access / Auth</SelectLabel>
                            <SelectItem value="login">Login</SelectItem>
                            <SelectItem value="break_glass_access">Break Glass Access</SelectItem>
                            <SelectItem value="pii_revealed">PII Revealed</SelectItem>
                            <SelectItem value="settings_credential_revealed">Credential Revealed</SelectItem>
                        </SelectGroup>
                        <SelectGroup>
                            <SelectLabel>Staff</SelectLabel>
                            <SelectItem value="staff_created">Staff Created</SelectItem>
                            <SelectItem value="staff_updated">Staff Updated</SelectItem>
                            <SelectItem value="staff_deleted">Staff Deleted</SelectItem>
                            <SelectItem value="staff_mfa_reset">MFA Reset</SelectItem>
                        </SelectGroup>
                        <SelectGroup>
                            <SelectLabel>Users</SelectLabel>
                            <SelectItem value="user_status_change">User Status Change</SelectItem>
                            <SelectItem value="flags_change">Flags Change</SelectItem>
                        </SelectGroup>
                        <SelectGroup>
                            <SelectLabel>Rides</SelectLabel>
                            <SelectItem value="ride_cancelled_by_admin">Admin Cancelled</SelectItem>
                            <SelectItem value="force_complete_ride">Force Complete</SelectItem>
                            <SelectItem value="admin_create_ride">Admin Create Ride</SelectItem>
                        </SelectGroup>
                        <SelectGroup>
                            <SelectLabel>Payments</SelectLabel>
                            <SelectItem value="wallet_credit">Wallet Credit</SelectItem>
                            <SelectItem value="wallet_debit">Wallet Debit</SelectItem>
                            <SelectItem value="payouts_period_closed">Payout Closed</SelectItem>
                        </SelectGroup>
                        <SelectGroup>
                            <SelectLabel>Corporate</SelectLabel>
                            <SelectItem value="create_corporate_account">Account Created</SelectItem>
                            <SelectItem value="change_company_status">Status Change</SelectItem>
                            <SelectItem value="corporate_wallet_manual_topup">Manual Top-up</SelectItem>
                            <SelectItem value="corporate_wallet_manual_adjust">Manual Adjust</SelectItem>
                        </SelectGroup>
                        <SelectGroup>
                            <SelectLabel>Exports</SelectLabel>
                            <SelectItem value="export_rides">Export Rides</SelectItem>
                            <SelectItem value="export_drivers">Export Drivers</SelectItem>
                            <SelectItem value="export_users">Export Users</SelectItem>
                        </SelectGroup>
                        <SelectGroup>
                            <SelectLabel>Data / Safety</SelectLabel>
                            <SelectItem value="dsar_deletion_requested">Deletion Requested</SelectItem>
                            <SelectItem value="dsar_deletion_executed">Deletion Executed</SelectItem>
                            <SelectItem value="safety_incident_auto_escalated">Safety Escalated</SelectItem>
                        </SelectGroup>
                        <SelectGroup>
                            <SelectLabel>Settings</SelectLabel>
                            <SelectItem value="settings_updated">Settings Updated</SelectItem>
                        </SelectGroup>
                    </SelectContent>
                </Select>
                <Select value={entityFilter} onValueChange={setEntityFilter}>
                    <SelectTrigger className="w-48" aria-label="Filter by entity">
                        <SelectValue placeholder="Filter by entity" />
                    </SelectTrigger>
                    <SelectContent>
                        <SelectItem value="all">All Entities</SelectItem>
                        <SelectGroup>
                            <SelectLabel>Core</SelectLabel>
                            <SelectItem value="users">Users</SelectItem>
                            <SelectItem value="rides">Rides</SelectItem>
                            <SelectItem value="staff">Staff</SelectItem>
                            <SelectItem value="drivers">Drivers</SelectItem>
                        </SelectGroup>
                        <SelectGroup>
                            <SelectLabel>Corporate</SelectLabel>
                            <SelectItem value="corporate_accounts">Accounts</SelectItem>
                            <SelectItem value="corporate_wallet">Wallet</SelectItem>
                            <SelectItem value="corporate_subscriptions">Subscriptions</SelectItem>
                        </SelectGroup>
                        <SelectGroup>
                            <SelectLabel>System</SelectLabel>
                            <SelectItem value="settings">Settings</SelectItem>
                            <SelectItem value="promotion">Promotions</SelectItem>
                            <SelectItem value="service_area">Service Areas</SelectItem>
                        </SelectGroup>
                    </SelectContent>
                </Select>
                {hasFilters && (
                    <Button variant="ghost" size="sm" onClick={handleClearFilters}>
                        <X className="mr-1 h-3 w-3" />
                        Clear filters
                    </Button>
                )}
            </div>

            {/* Filters — row 2: date range */}
            <div className="flex flex-wrap items-center gap-3">
                <div className="flex items-center gap-2 text-sm text-muted-foreground">
                    <Calendar className="h-4 w-4" />
                    Date range
                </div>
                <Input
                    type="date"
                    value={startDate}
                    onChange={(e) => setStartDate(e.target.value)}
                    className="w-40"
                    aria-label="Start date"
                />
                <span className="text-sm text-muted-foreground">to</span>
                <Input
                    type="date"
                    value={endDate}
                    onChange={(e) => setEndDate(e.target.value)}
                    className="w-40"
                    aria-label="End date"
                />
            </div>

            {/* Top actors — threat-hunting rollup */}
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
                        <div className="text-sm text-muted-foreground py-2">Loading...</div>
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
                                    <span className="text-xs truncate max-w-[180px]">
                                        {a.actor_email || a.actor_id.slice(0, 8)}
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
                                {hasFilters
                                    ? "Try adjusting your filters."
                                    : "Admin actions will be recorded here."}
                            </p>
                        </div>
                    ) : (
                        <>
                            <div className="overflow-x-auto">
                                <Table>
                                    <TableHeader>
                                        <TableRow>
                                            <SortableHead column="created_at" sort={sort} onSort={toggle}>Time</SortableHead>
                                            <SortableHead column="actor_email" sort={sort} onSort={toggle}>User</SortableHead>
                                            <SortableHead column="action" sort={sort} onSort={toggle}>Action</SortableHead>
                                            <SortableHead column="entity_type" sort={sort} onSort={toggle}>Entity</SortableHead>
                                            <SortableHead column="details" sort={sort} onSort={toggle}>Details</SortableHead>
                                        </TableRow>
                                    </TableHeader>
                                    <TableBody>
                                        {sorted.map((log) => {
                                            const Icon = ENTITY_ICONS[log.entity_type] || Shield;
                                            const actionCfg = ACTION_CONFIG[log.action];
                                            const detailStr = formatDetails(log.details);
                                            const isExpanded = expandedRow === log.id;
                                            const isLong = detailStr.length > 80;
                                            return (
                                                <TableRow
                                                    key={log.id}
                                                    className={isLong ? "cursor-pointer" : ""}
                                                    onClick={isLong ? () => setExpandedRow(isExpanded ? null : log.id) : undefined}
                                                >
                                                    <TableCell className="text-sm text-muted-foreground whitespace-nowrap">
                                                        {formatDate(log.created_at)}
                                                    </TableCell>
                                                    <TableCell className="font-medium text-sm max-w-[200px] truncate" title={log.actor_email || log.actor_id || ""}>
                                                        {log.actor_email || (log.actor_id ? log.actor_id.slice(0, 8) + "..." : "System")}
                                                    </TableCell>
                                                    <TableCell>
                                                        <Badge className={actionCfg?.color || "bg-zinc-500/15 text-zinc-600"}>
                                                            {actionCfg?.label || log.action.replace(/_/g, " ")}
                                                        </Badge>
                                                    </TableCell>
                                                    <TableCell>
                                                        <div className="flex items-center gap-2">
                                                            <Icon className="h-4 w-4 text-muted-foreground shrink-0" />
                                                            <span className="text-sm capitalize">{formatEntityType(log.entity_type)}</span>
                                                            <span
                                                                className="text-xs text-muted-foreground font-mono cursor-pointer hover:text-foreground"
                                                                title={`Click to copy: ${log.entity_id}`}
                                                                onClick={(e) => {
                                                                    e.stopPropagation();
                                                                    if (log.entity_id) navigator.clipboard.writeText(log.entity_id);
                                                                }}
                                                            >
                                                                {log.entity_id?.slice(0, 8)}...
                                                            </span>
                                                        </div>
                                                    </TableCell>
                                                    <TableCell className="text-sm text-muted-foreground max-w-xs">
                                                        {isLong && !isExpanded ? (
                                                            <span className="flex items-center gap-1">
                                                                <ChevronRight className="h-3 w-3 shrink-0" />
                                                                <span className="truncate">{detailStr}</span>
                                                            </span>
                                                        ) : isLong && isExpanded ? (
                                                            <span className="flex items-start gap-1">
                                                                <ChevronDown className="h-3 w-3 shrink-0 mt-0.5" />
                                                                <span className="whitespace-pre-wrap break-all">{detailStr}</span>
                                                            </span>
                                                        ) : (
                                                            detailStr || "—"
                                                        )}
                                                    </TableCell>
                                                </TableRow>
                                            );
                                        })}
                                    </TableBody>
                                </Table>
                            </div>

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
                    Sorting applies to this page only.
                </p>
            )}
        </div>
    );
}
