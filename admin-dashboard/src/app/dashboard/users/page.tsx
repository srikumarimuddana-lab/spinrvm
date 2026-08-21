"use client";

import { useEffect, useRef, useState } from "react";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from "@/components/ui/table";
import {
    Dialog,
    DialogContent,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog";
import {
    AlertDialog,
    AlertDialogAction,
    AlertDialogCancel,
    AlertDialogContent,
    AlertDialogDescription,
    AlertDialogFooter,
    AlertDialogHeader,
    AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select";
import { Pagination } from "@/components/ui/pagination";
import { useTableSort, SortableHead } from "@/components/ui/sortable-table";
import { Users, Search, Mail, Phone, Calendar, Car, ShieldCheck, Download, RefreshCw, Ban, CheckCircle, AlertTriangle, Wallet, Plus, Minus, Eye, EyeOff, CreditCard, MapPin, Gift } from "lucide-react";
import { exportToCsv } from "@/lib/export-csv";
import { formatDate } from "@/lib/utils";
import { getUsersPaginated, getUserDetails, updateUserStatus, updateUserFlags, getStats, getUserWallet, creditUserWallet, debitUserWallet, exportUsers, logPiiReveal, backfillStripeCustomerEmails } from "@/lib/api";
import { maskEmail, maskPhone } from "@/lib/pii";
import { useRequireModule } from "@/hooks/useRequireModule";
import { useToast } from "@/components/ui/use-toast";

const PAGE_SIZE = 50;

export default function UsersPage() {
    const { allowed } = useRequireModule("users");
    const { toast } = useToast();
    const [users, setUsers] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");
    const [search, setSearch] = useState("");
    const [statusUpdating, setStatusUpdating] = useState<string | null>(null);
    const [roleFilter, setRoleFilter] = useState<"all" | "rider" | "driver" | "both">("all");
    const [selectedUser, setSelectedUser] = useState<any>(null);
    // Full detail for the open user (real ride count, recent rides, saved cards),
    // loaded on demand — the list row carries none of this.
    const [userDetail, setUserDetail] = useState<any>(null);
    const [page, setPage] = useState(0);
    const [hasNextPage, setHasNextPage] = useState(false);
    const [stats, setStats] = useState<{ total_users: number; total_drivers: number } | null>(null);
    const reqIdRef = useRef(0);

    const [showPii, setShowPii] = useState(false);

    const [pendingStatusChange, setPendingStatusChange] = useState<{ id: string; status: "suspended" | "banned"; name: string } | null>(null);
    // Moderation note (required) + optional suspension length (days; "" = indefinite).
    const [moderationReason, setModerationReason] = useState("");
    const [moderationDays, setModerationDays] = useState<string>("");
    const [pendingWalletAction, setPendingWalletAction] = useState<{ action: "credit" | "debit"; amount: number; reason: string } | null>(null);

    // Wallet state for the user-details dialog
    const [walletData, setWalletData] = useState<any>(null);
    const [walletLoading, setWalletLoading] = useState(false);
    const [walletError, setWalletError] = useState("");
    const [walletAmount, setWalletAmount] = useState("");
    const [walletReason, setWalletReason] = useState("");
    const [walletSubmitting, setWalletSubmitting] = useState<"credit" | "debit" | null>(null);

    useEffect(() => {
        if (!selectedUser?.id) {
            setWalletData(null);
            setWalletError("");
            setWalletAmount("");
            setWalletReason("");
            return;
        }
        setWalletLoading(true);
        setWalletError("");
        getUserWallet(selectedUser.id)
            .then((d) => setWalletData(d))
            .catch((e) => setWalletError(e?.message || "Failed to load wallet"))
            .finally(() => setWalletLoading(false));
    }, [selectedUser?.id]);

    // Validation step — runs on button click. On pass, opens confirm dialog;
    // actual mutation runs in confirmWalletAction below.
    const requestWalletAction = (action: "credit" | "debit") => {
        if (!selectedUser?.id || !walletAmount || !/^\d+(\.\d{1,2})?$/.test(walletAmount.trim()) || parseFloat(walletAmount) <= 0) {
            setWalletError("Enter a positive amount");
            return;
        }
        if (!walletReason.trim() || walletReason.trim().length < 3) {
            setWalletError("Reason must be at least 3 characters");
            return;
        }
        setWalletError("");
        setPendingWalletAction({
            action,
            amount: parseFloat(walletAmount),
            reason: walletReason.trim(),
        });
    };

    const confirmWalletAction = async () => {
        if (!selectedUser?.id || !pendingWalletAction) return;
        const { action, amount, reason } = pendingWalletAction;
        setWalletSubmitting(action);
        setWalletError("");
        try {
            const fn = action === "credit" ? creditUserWallet : debitUserWallet;
            const result = await fn(selectedUser.id, amount, reason); // backend converts to Decimal
            const refreshed = await getUserWallet(selectedUser.id);
            setWalletData(refreshed);
            setWalletAmount("");
            setWalletReason("");
            toast({
                title: `Wallet ${action === "credit" ? "credited" : "debited"}`,
                description: result?.audit_log_id ? `Ref: ${result.audit_log_id}` : "Operation successful",
            });
        } catch (e: any) {
            setWalletError(e?.message || `Failed to ${action}`);
        } finally {
            setWalletSubmitting(null);
            setPendingWalletAction(null);
        }
    };

    const fetchUsers = async () => {
        setLoading(true);
        setError("");
        const reqId = ++reqIdRef.current;
        try {
            const data = await getUsersPaginated({
                role: roleFilter,
                search: search.trim() || undefined,
                limit: PAGE_SIZE + 1,
                offset: page * PAGE_SIZE,
            });
            if (reqId !== reqIdRef.current) return;
            const arr = Array.isArray(data) ? data : [];
            setHasNextPage(arr.length > PAGE_SIZE);
            const slice = arr.slice(0, PAGE_SIZE);
            const transformed = slice.map((u: any) => ({
                id: u.id,
                name: `${u.first_name || ""} ${u.last_name || ""}`.trim() || u.email || u.phone,
                first_name: u.first_name,
                last_name: u.last_name,
                email: u.email,
                phone: u.phone,
                role: u.role,
                created_at: u.created_at,
                is_rider: u.is_rider,
                is_driver: u.is_driver,
                status: u.status || "active",
                status_reason: u.status_reason ?? null,
                suspended_until: u.suspended_until ?? null,
                // Now present on the list endpoint (_USER_LIST_COLUMNS,
                // backend/routes/admin/users.py) -- carried through this
                // explicit per-field transform so the list-row "Imported"
                // badge below isn't silently dropped, the way it would be if
                // this object literal simply omitted the key.
                legacy_import_metadata: u.legacy_import_metadata ?? null,
            }));
            setUsers(transformed);
        } catch (err: any) {
            if (reqId !== reqIdRef.current) return;
            console.error("Failed to fetch users:", err);
            setError("Failed to load users. Please try again.");
            setUsers([]);
            setHasNextPage(false);
        } finally {
            if (reqId === reqIdRef.current) setLoading(false);
        }
    };

    // Initial stats fetch (independent of pagination).
    useEffect(() => {
        getStats().then((s) => setStats(s)).catch(() => setStats(null));
    }, []);

    // Fetch the open user's detail (real ride count, recent rides, saved cards).
    // Guarded against races when switching users.
    useEffect(() => {
        if (!selectedUser?.id) { setUserDetail(null); return; }
        const id = selectedUser.id;
        let active = true;
        setUserDetail(null);
        getUserDetails(id)
            .then((d) => { if (active) setUserDetail(d || {}); })
            .catch(() => { if (active) setUserDetail({}); });
        return () => { active = false; };
    }, [selectedUser?.id]);

    // Reset to page 0 when filters change (debounce search).
    useEffect(() => {
        const t = setTimeout(() => {
            if (page !== 0) setPage(0);
            else fetchUsers();
        }, 300);
        return () => clearTimeout(t);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [search, roleFilter]);

    // Re-fetch when page changes.
    useEffect(() => {
        fetchUsers();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [page]);

    const handleExport = async () => {
        try {
            const res = await exportUsers();
            exportToCsv('users', res.users || [], [
                { key: 'id', label: 'ID' },
                { key: 'name', label: 'Name' },
                { key: 'email', label: 'Email' },
                { key: 'phone', label: 'Phone' },
                { value: (u: any) => (u.is_rider ? 'Rider' : '') + (u.is_rider && u.is_driver ? ' / ' : '') + (u.is_driver ? 'Driver' : ''), label: 'Role' },
                { value: (u: any) => formatDate(u.created_at), label: 'Joined Date' },
            ]);
            toast({ title: "Export complete", description: `${res.count ?? 0} users exported.` });
        } catch {
            toast({ title: "Export failed", variant: "destructive" });
        }
    };

    const [stripeEmailSyncRunning, setStripeEmailSyncRunning] = useState(false);
    // The endpoint is bounded per call so one request can't run for minutes.
    // This walks the batches itself: telling the operator to "run again" is how
    // a partial sweep gets mistaken for a finished one, since a second bare
    // call restarts at page one and reports everything already correct.
    const STRIPE_SYNC_MAX_BATCHES = 40;
    const handleBackfillStripeEmails = async () => {
        // Preview FIRST, always. This sends rider email addresses to Stripe (a
        // US processor) in bulk, so the operator sees the count — and which
        // Stripe account it is — before anything leaves the country.
        setStripeEmailSyncRunning(true);
        try {
            const preview = await backfillStripeCustomerEmails({ apply: false });
            const stranded = preview.missing_on_key.length;
            const account = (preview.key_mode || "unknown").toUpperCase();
            if (preview.updated === 0 && !preview.has_more) {
                toast({
                    title: "Nothing to sync",
                    description:
                        `${preview.scanned} Stripe customer${preview.scanned === 1 ? "" : "s"} checked — all already carry the rider's email.` +
                        (stranded ? ` ${stranded} unreachable on the current key.` : ""),
                });
                return;
            }
            const newly = preview.changes.filter(c => !c.had_email).length;
            const corrected = preview.updated - newly;
            if (!window.confirm(
                `Sync rider emails to Stripe (${account} account)?\n\n` +
                `First batch: ${preview.updated} of ${preview.scanned} customer(s) need updating — ` +
                `${newly} have no email at all, ${corrected} carry a different address.\n\n` +
                "This sends those riders' email addresses to Stripe so they can be found by " +
                "address in the dashboard. Only the email field is written — no customer is " +
                "created, no saved card is touched. Riders who have requested deletion are skipped." +
                (stranded ? `\n\n${stranded} customer(s) are unreachable on the current Stripe key and will be skipped — those repair themselves on the rider's next visit to their own payment screen.` : "") +
                (preview.has_more ? "\n\nMore riders remain beyond this batch. Confirming processes ALL remaining batches, not just this one." : "")
            )) return;

            const totals = { updated: 0, unchanged: 0, stranded: 0, throttled: 0, failed: 0 };
            let cursor: string | undefined;
            let batches = 0;
            let stoppedEarly = false;
            for (;;) {
                const res = await backfillStripeCustomerEmails({ apply: true, cursor });
                batches += 1;
                totals.updated += res.updated;
                totals.unchanged += res.unchanged;
                totals.stranded += res.missing_on_key.length;
                totals.throttled += res.throttled.length;
                totals.failed += res.failed.length;
                if (!res.has_more) break;
                // No cursor with has_more set would loop on the same page —
                // stop and say so rather than spinning or silently truncating.
                if (!res.next_cursor || batches >= STRIPE_SYNC_MAX_BATCHES) {
                    stoppedEarly = true;
                    break;
                }
                cursor = res.next_cursor;
            }
            // Throttled riders were skipped, not written. Reporting this run as
            // a success would leave them permanently unsynced in the operator's
            // mind — the same silent-partial-success trap as stopping early.
            const incomplete = stoppedEarly || totals.throttled > 0 || totals.failed > 0;
            toast({
                title: incomplete ? "Stripe email sync incomplete" : "Stripe customer emails synced",
                description:
                    `${totals.updated} updated, ${totals.unchanged} already correct` +
                    (totals.stranded ? ` · ${totals.stranded} unreachable` : "") +
                    (totals.throttled ? ` · ${totals.throttled} rate-limited by Stripe` : "") +
                    (totals.failed ? ` · ${totals.failed} failed` : "") +
                    (stoppedEarly ? ` · stopped after ${batches} batches` : "") +
                    (incomplete ? " — run again to finish (safe to repeat)" : ""),
                variant: incomplete ? "destructive" : undefined,
            });
        } catch (e: any) {
            toast({ title: "Stripe email sync failed", description: e?.message || "Unknown error", variant: "destructive" });
        } finally {
            setStripeEmailSyncRunning(false);
        }
    };

    const totalForRoleStat = roleFilter === "driver"
        ? stats?.total_drivers
        : roleFilter === "rider"
            ? (stats && (stats.total_users - (stats.total_drivers || 0)))
            : stats?.total_users;

    const { sorted: sortedUsers, sort: usersSort, toggle: usersToggle } = useTableSort(users);

    if (!allowed) return null;

    return (
        <div className="space-y-6">
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-3xl font-bold tracking-tight flex items-center gap-2">
                        <Users className="h-8 w-8 text-sky-500" />
                        Users
                    </h1>
                    <p className="text-muted-foreground mt-1">
                        View and manage registered riders.
                    </p>
                </div>
                <div className="flex gap-2">
                    <Button variant="outline" size="icon" onClick={fetchUsers} disabled={loading} aria-label="Refresh">
                        <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
                    </Button>
                    <Button variant="outline" size="sm" onClick={() => {
                        const next = !showPii;
                        setShowPii(next);
                        if (next) logPiiReveal("users", "page_toggle").catch(() => {});
                    }}>
                        {showPii ? <EyeOff className="mr-2 h-4 w-4" /> : <Eye className="mr-2 h-4 w-4" />}
                        {showPii ? "Hide PII" : "Show PII"}
                    </Button>
                    <Button
                        variant="outline"
                        size="sm"
                        onClick={handleBackfillStripeEmails}
                        disabled={stripeEmailSyncRunning}
                        title="Attach riders' emails to their Stripe customers so they can be found by address in the Stripe dashboard. Previews the count before writing (super admin)."
                    >
                        <CreditCard className="mr-2 h-4 w-4" />
                        {stripeEmailSyncRunning ? "Syncing…" : "Sync Stripe emails"}
                    </Button>
                    <Button variant="outline" onClick={handleExport} disabled={users.length === 0}>
                        <Download className="mr-2 h-4 w-4" /> Export
                    </Button>
                </div>
            </div>

            {/* Stats */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                <Card>
                    <CardContent className="pt-4 pb-3">
                        <div className="flex items-center gap-2">
                            <Users className="h-5 w-5 text-sky-500" />
                            <div>
                                <p className="text-xs text-muted-foreground">
                                    {roleFilter === "all" ? "Total Users" : roleFilter === "driver" ? "Total Drivers" : "Total Riders"}
                                </p>
                                <p className="text-2xl font-bold">{totalForRoleStat ?? "—"}</p>
                            </div>
                        </div>
                    </CardContent>
                </Card>
                <Card>
                    <CardContent className="pt-4 pb-3">
                        <div className="flex items-center gap-2">
                            <Users className="h-5 w-5 text-emerald-500" />
                            <div>
                                <p className="text-xs text-muted-foreground">Riders (page)</p>
                                <p className="text-2xl font-bold">{users.filter(u => u.is_rider).length}</p>
                            </div>
                        </div>
                    </CardContent>
                </Card>
                <Card>
                    <CardContent className="pt-4 pb-3">
                        <div className="flex items-center gap-2">
                            <Car className="h-5 w-5 text-violet-500" />
                            <div>
                                <p className="text-xs text-muted-foreground">Drivers (page)</p>
                                <p className="text-2xl font-bold">{users.filter(u => u.is_driver).length}</p>
                            </div>
                        </div>
                    </CardContent>
                </Card>
                <Card>
                    <CardContent className="pt-4 pb-3">
                        <div className="flex items-center gap-2">
                            <ShieldCheck className="h-5 w-5 text-sky-500" />
                            <div>
                                <p className="text-xs text-muted-foreground">Dual-role (page)</p>
                                <p className="text-2xl font-bold">{users.filter(u => u.is_rider && u.is_driver).length}</p>
                            </div>
                        </div>
                    </CardContent>
                </Card>
            </div>

            {/* Error State */}
            {error && (
                <Card className="border-red-200 dark:border-red-900/50">
                    <CardContent className="pt-4 pb-4">
                        <div className="flex items-center justify-between">
                            <p className="text-sm text-red-600 dark:text-red-400">{error}</p>
                            <Button variant="outline" size="sm" onClick={fetchUsers}>Retry</Button>
                        </div>
                    </CardContent>
                </Card>
            )}

            {/* Filters */}
            <div className="flex flex-wrap items-center gap-3">
                <div className="relative flex-1 max-w-sm">
                    <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                    <Input
                        placeholder="Search by name, email, phone..."
                        aria-label="Search users"
                        value={search}
                        onChange={(e) => setSearch(e.target.value)}
                        className="pl-9"
                    />
                </div>
                <Select value={roleFilter} onValueChange={(v) => setRoleFilter(v as "all" | "rider" | "driver" | "both")}>
                    <SelectTrigger className="w-36" aria-label="Filter by role">
                        <SelectValue placeholder="Role" />
                    </SelectTrigger>
                    <SelectContent>
                        <SelectItem value="all">All Roles</SelectItem>
                        <SelectItem value="rider">Riders only</SelectItem>
                        <SelectItem value="driver">Drivers only</SelectItem>
                        <SelectItem value="both">Dual-role</SelectItem>
                    </SelectContent>
                </Select>
                {(search || roleFilter !== "all") && (
                    <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => {
                            setSearch("");
                            setRoleFilter("all");
                        }}
                    >
                        Clear filters
                    </Button>
                )}
            </div>

            {/* Table */}
            <Card className="border-border/50">
                <CardContent className="p-0">
                    {loading ? (
                        <div className="flex items-center justify-center p-12">
                            <div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" />
                        </div>
                    ) : (
                        <>
                            <Table>
                                <TableHeader>
                                    <TableRow>
                                        <SortableHead column="name" sort={usersSort} onSort={usersToggle}>Name</SortableHead>
                                        <SortableHead column="email" sort={usersSort} onSort={usersToggle}>Contact</SortableHead>
                                        <SortableHead column="role" sort={usersSort} onSort={usersToggle}>Role</SortableHead>
                                        <SortableHead column="status" sort={usersSort} onSort={usersToggle}>Status</SortableHead>
                                        <SortableHead column="created_at" sort={usersSort} onSort={usersToggle}>Joined</SortableHead>
                                        <TableHead className="text-right">Actions</TableHead>
                                    </TableRow>
                                </TableHeader>
                                <TableBody>
                                    {users.length === 0 ? (
                                        <TableRow>
                                            <TableCell colSpan={6} className="text-center text-muted-foreground py-12">
                                                No users found.
                                            </TableCell>
                                        </TableRow>
                                    ) : (
                                        sortedUsers.map((user) => (
                                            <TableRow key={user.id} className="cursor-pointer hover:bg-muted/50" onClick={() => setSelectedUser(user)}>
                                                <TableCell>
                                                    <div>
                                                        <p className="font-medium flex items-center gap-1.5">
                                                            {user.name}
                                                            {user.legacy_import_metadata && Object.keys(user.legacy_import_metadata).length > 0 && (
                                                                <span className="inline-block text-[10px] font-medium text-muted-foreground bg-muted rounded px-1.5 py-0.5 shrink-0">
                                                                    Imported
                                                                </span>
                                                            )}
                                                        </p>
                                                        <p className="text-xs text-muted-foreground font-mono">{user.id?.slice(0, 8)}</p>
                                                    </div>
                                                </TableCell>
                                                <TableCell>
                                                    <div className="space-y-1 text-sm">
                                                        <div className="flex items-center gap-1 text-muted-foreground">
                                                            <Mail className="h-3 w-3" />
                                                            {showPii ? (user.email || "—") : maskEmail(user.email)}
                                                        </div>
                                                        <div className="flex items-center gap-1 text-muted-foreground">
                                                            <Phone className="h-3 w-3" />
                                                            {showPii ? (user.phone || "—") : maskPhone(user.phone)}
                                                        </div>
                                                    </div>
                                                </TableCell>
                                                <TableCell>
                                                    <div className="flex flex-wrap items-center gap-1">
                                                        {user.is_rider && (
                                                            <Badge variant="secondary" className="bg-sky-500/15 text-sky-600">Rider</Badge>
                                                        )}
                                                        {user.is_driver && (
                                                            <Badge variant="secondary" className="bg-violet-500/15 text-violet-600">Driver</Badge>
                                                        )}
                                                        {!user.is_rider && !user.is_driver && (
                                                            <span className="text-muted-foreground">—</span>
                                                        )}
                                                    </div>
                                                </TableCell>
                                                <TableCell>
                                                    <Badge className={
                                                        user.status === "banned" ? "bg-destructive/15 text-destructive"
                                                        : user.status === "suspended" ? "bg-warning/15 text-warning"
                                                        // eslint-disable-next-line no-restricted-syntax -- pending_deletion has no semantic-token equivalent (#2816)
                                                        : user.status === "pending_deletion" ? "bg-orange-500/15 text-orange-600"
                                                        : "bg-success/15 text-success"
                                                    }>
                                                        {user.status === "banned" ? "Banned"
                                                        : user.status === "suspended" ? "Suspended"
                                                        : user.status === "pending_deletion" ? "Pending deletion"
                                                        : "Active"}
                                                    </Badge>
                                                </TableCell>
                                                <TableCell className="text-xs text-muted-foreground">
                                                    {formatDate(user.created_at)}
                                                </TableCell>
                                                <TableCell className="text-right">
                                                    <Button variant="ghost" size="sm" onClick={(e) => { e.stopPropagation(); setSelectedUser(user); }}>
                                                        Details
                                                    </Button>
                                                </TableCell>
                                            </TableRow>
                                        ))
                                    )}
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

            {/* User Details Dialog */}
            <Dialog open={!!selectedUser} onOpenChange={(open) => { if (!open) setSelectedUser(null); }}>
                <DialogContent className="sm:max-w-2xl max-h-[90vh] overflow-y-auto">
                    <DialogHeader>
                        <DialogTitle className="flex items-center gap-2">
                            <Users className="h-5 w-5 text-sky-500" />
                            User Details
                        </DialogTitle>
                    </DialogHeader>
                    {selectedUser && (
                        <div className="space-y-4">
                            <div className="flex items-center gap-4">
                                <div className="h-16 w-16 rounded-full bg-gradient-to-br from-sky-500 to-blue-600 flex items-center justify-center text-white text-xl font-bold">
                                    {selectedUser.name?.charAt(0)?.toUpperCase() || "?"}
                                </div>
                                <div>
                                    <p className="text-lg font-semibold">{selectedUser.name}</p>
                                    <div className="flex flex-wrap items-center gap-1 mt-0.5">
                                        {selectedUser.is_rider && (
                                            <Badge variant="secondary" className="bg-sky-500/15 text-sky-600">Rider</Badge>
                                        )}
                                        {selectedUser.is_driver && (
                                            <Badge variant="secondary" className="bg-violet-500/15 text-violet-600">Driver</Badge>
                                        )}
                                        {/* legacy_import_metadata is only present on the DETAIL fetch
                                            (admin_get_user_details selects the full users row) -- the
                                            list endpoint's _USER_LIST_COLUMNS projection omits it, so
                                            this reads from userDetail, not selectedUser. */}
                                        {userDetail?.legacy_import_metadata && Object.keys(userDetail.legacy_import_metadata).length > 0 && (
                                            <span className="inline-block text-[10px] font-medium text-muted-foreground bg-muted rounded px-1.5 py-0.5">
                                                Imported
                                            </span>
                                        )}
                                    </div>
                                </div>
                            </div>

                            <div className="grid grid-cols-2 sm:grid-cols-3 gap-4">
                                <div className="space-y-1">
                                    <Label className="text-xs text-muted-foreground flex items-center gap-1">
                                        <Mail className="h-3 w-3" /> Email
                                    </Label>
                                    <p className="text-sm break-all">{showPii ? selectedUser.email : maskEmail(selectedUser.email)}</p>
                                </div>
                                <div className="space-y-1">
                                    <Label className="text-xs text-muted-foreground flex items-center gap-1">
                                        <Phone className="h-3 w-3" /> Phone
                                    </Label>
                                    <p className="text-sm">{showPii ? selectedUser.phone : maskPhone(selectedUser.phone)}</p>
                                </div>
                                <div className="space-y-1">
                                    <Label className="text-xs text-muted-foreground flex items-center gap-1">
                                        <Calendar className="h-3 w-3" /> Joined
                                    </Label>
                                    <p className="text-sm">{formatDate(selectedUser.created_at)}</p>
                                </div>
                                <div className="space-y-1">
                                    <Label className="text-xs text-muted-foreground flex items-center gap-1">
                                        <Car className="h-3 w-3" /> Total rides
                                    </Label>
                                    <p className="text-sm">{userDetail == null ? "…" : (userDetail.total_rides ?? 0).toLocaleString()}</p>
                                </div>
                                <div className="space-y-1">
                                    <Label className="text-xs text-muted-foreground flex items-center gap-1">
                                        <Users className="h-3 w-3" /> Gender
                                    </Label>
                                    <p className="text-sm">{userDetail?.gender || selectedUser.gender || "—"}</p>
                                </div>
                                <div className="space-y-1">
                                    <Label className="text-xs text-muted-foreground flex items-center gap-1">
                                        <MapPin className="h-3 w-3" /> City
                                    </Label>
                                    <p className="text-sm">{userDetail?.city || selectedUser.city || "—"}</p>
                                </div>
                                <div className="space-y-1">
                                    <Label className="text-xs text-muted-foreground flex items-center gap-1">
                                        <Gift className="h-3 w-3" /> Referred by
                                    </Label>
                                    <p className="text-sm font-mono">{userDetail?.referral_code_used || "—"}</p>
                                </div>
                                <div className="space-y-1">
                                    <Label className="text-xs text-muted-foreground flex items-center gap-1">
                                        <CreditCard className="h-3 w-3" /> Cards on file
                                    </Label>
                                    <p className="text-sm">{userDetail == null ? "…" : (userDetail.cards?.length ?? 0)}</p>
                                </div>
                                <div className="space-y-1">
                                    <Label className="text-xs text-muted-foreground flex items-center gap-1">
                                        <ShieldCheck className="h-3 w-3" /> Account
                                    </Label>
                                    <p className="text-sm">{selectedUser.is_driver && selectedUser.is_rider ? "Rider + Driver" : selectedUser.is_driver ? "Driver" : "Rider"}</p>
                                </div>
                            </div>

                            {/* Status Management */}
                            <div className="border-t pt-4">
                                <Label className="text-xs text-muted-foreground mb-2 block">Account Status</Label>
                                <div className="flex items-center gap-2 mb-3">
                                    <Badge className={
                                        selectedUser.status === "banned" ? "bg-destructive/15 text-destructive"
                                        : selectedUser.status === "suspended" ? "bg-warning/15 text-warning"
                                        // eslint-disable-next-line no-restricted-syntax -- pending_deletion has no semantic-token equivalent (#2816)
                                        : selectedUser.status === "pending_deletion" ? "bg-orange-500/15 text-orange-600"
                                        : "bg-success/15 text-success"
                                    }>
                                        {selectedUser.status === "banned" ? "Banned"
                                        : selectedUser.status === "suspended" ? "Suspended"
                                        : selectedUser.status === "pending_deletion" ? "Pending deletion"
                                        : "Active"}
                                    </Badge>
                                </div>
                                {selectedUser.status !== "active" && (selectedUser.status_reason || selectedUser.suspended_until) && (
                                    <div className="mb-3 rounded-md bg-muted/50 p-2 text-xs space-y-0.5">
                                        {selectedUser.status_reason && (
                                            <p><span className="text-muted-foreground">Reason: </span>{selectedUser.status_reason}</p>
                                        )}
                                        {selectedUser.status === "suspended" && (
                                            <p className="text-muted-foreground">
                                                {selectedUser.suspended_until
                                                    ? `Auto-lifts ${formatDate(selectedUser.suspended_until)}`
                                                    : "Indefinite — until reactivated"}
                                            </p>
                                        )}
                                    </div>
                                )}
                                <div className="flex gap-2">
                                    {selectedUser.status !== "active" && (
                                        <Button
                                            className="flex-1"
                                            variant="outline"
                                            disabled={statusUpdating === selectedUser.id}
                                            onClick={async () => {
                                                setStatusUpdating(selectedUser.id);
                                                try {
                                                    await updateUserStatus(selectedUser.id, { status: "active" });
                                                    setSelectedUser({ ...selectedUser, status: "active" });
                                                    setUsers(prev => prev.map(u => u.id === selectedUser.id ? { ...u, status: "active" } : u));
                                                    toast({ title: "User activated" });
                                                } catch (err: any) {
                                                    console.error('[UsersPage] Failed to activate user:', err);
                                                    toast({ title: "Failed to activate user", description: err?.message, variant: "destructive" });
                                                } finally { setStatusUpdating(null); }
                                            }}
                                        >
                                            <CheckCircle className="h-4 w-4 mr-2 text-success" /> Activate
                                        </Button>
                                    )}
                                    {selectedUser.status !== "suspended" && (
                                        <Button
                                            className="flex-1"
                                            variant="outline"
                                            disabled={statusUpdating === selectedUser.id}
                                            onClick={() => setPendingStatusChange({
                                                id: selectedUser.id,
                                                status: "suspended",
                                                name: `${selectedUser.first_name || ''} ${selectedUser.last_name || ''}`.trim() || selectedUser.phone,
                                            })}
                                        >
                                            <AlertTriangle className="h-4 w-4 mr-2 text-warning" /> Suspend
                                        </Button>
                                    )}
                                    {selectedUser.status !== "banned" && (
                                        <Button
                                            className="flex-1"
                                            variant="destructive"
                                            disabled={statusUpdating === selectedUser.id}
                                            onClick={() => setPendingStatusChange({
                                                id: selectedUser.id,
                                                status: "banned",
                                                name: `${selectedUser.first_name || ''} ${selectedUser.last_name || ''}`.trim() || selectedUser.phone,
                                            })}
                                        >
                                            <Ban className="h-4 w-4 mr-2" /> Ban
                                        </Button>
                                    )}
                                </div>
                            </div>

                            {/* Role Flags (independent toggles — dual-role supported) */}
                            <div className="border-t pt-4">
                                <Label className="text-xs text-muted-foreground mb-2 block">Role Flags</Label>
                                <p className="text-xs text-muted-foreground mb-2">
                                    Each flag is independent. A user can be both a rider and a driver.
                                </p>
                                <div className="flex gap-2">
                                    <Button
                                        className="flex-1"
                                        variant={selectedUser.is_rider ? "default" : "outline"}
                                        size="sm"
                                        disabled={statusUpdating === selectedUser.id}
                                        onClick={async () => {
                                            setStatusUpdating(selectedUser.id);
                                            const next = !selectedUser.is_rider;
                                            try {
                                                await updateUserFlags(selectedUser.id, { is_rider: next });
                                                setSelectedUser({ ...selectedUser, is_rider: next });
                                                setUsers(prev => prev.map(u => u.id === selectedUser.id ? { ...u, is_rider: next } : u));
                                                toast({ title: next ? "Rider flag enabled" : "Rider flag disabled" });
                                            } catch (err: any) {
                                                toast({ title: "Failed to update flag", description: err?.message, variant: "destructive" });
                                            } finally { setStatusUpdating(null); }
                                        }}
                                    >
                                        {selectedUser.is_rider ? "✓ Rider" : "Rider off"}
                                    </Button>
                                    <Button
                                        className="flex-1"
                                        variant={selectedUser.is_driver ? "default" : "outline"}
                                        size="sm"
                                        disabled={statusUpdating === selectedUser.id}
                                        onClick={async () => {
                                            setStatusUpdating(selectedUser.id);
                                            const next = !selectedUser.is_driver;
                                            try {
                                                await updateUserFlags(selectedUser.id, { is_driver: next });
                                                setSelectedUser({ ...selectedUser, is_driver: next });
                                                setUsers(prev => prev.map(u => u.id === selectedUser.id ? { ...u, is_driver: next } : u));
                                                toast({ title: next ? "Driver flag enabled" : "Driver flag disabled" });
                                            } catch (err: any) {
                                                toast({ title: "Failed to update flag", description: err?.message, variant: "destructive" });
                                            } finally { setStatusUpdating(null); }
                                        }}
                                    >
                                        {selectedUser.is_driver ? "✓ Driver" : "Driver off"}
                                    </Button>
                                </div>
                            </div>

                            {/* Wallet Management */}
                            <div className="border-t pt-4">
                                <Label className="text-xs text-muted-foreground mb-2 flex items-center gap-1">
                                    <Wallet className="h-3 w-3" /> Wallet
                                </Label>

                                {walletLoading ? (
                                    <div className="flex items-center justify-center py-6">
                                        <div className="h-5 w-5 animate-spin rounded-full border-2 border-primary border-t-transparent" />
                                    </div>
                                ) : walletData ? (
                                    <div className="space-y-3">
                                        <div className="rounded-lg bg-gradient-to-br from-sky-500/10 to-blue-600/10 border border-sky-500/20 p-4">
                                            <p className="text-xs text-muted-foreground">Current Balance</p>
                                            <p className="text-3xl font-bold">
                                                {walletData.wallet.currency === "CAD" ? "$" : ""}
                                                {Number(walletData.wallet.balance).toFixed(2)}
                                                <span className="text-sm font-normal text-muted-foreground ml-1">{walletData.wallet.currency}</span>
                                            </p>
                                            {!walletData.wallet.is_active && (
                                                <Badge variant="destructive" className="mt-1">Suspended</Badge>
                                            )}
                                        </div>

                                        <div className="grid grid-cols-2 gap-2">
                                            <Input
                                                type="number"
                                                step="0.01"
                                                min="0.01"
                                                placeholder="Amount (CAD)"
                                                aria-label="Adjustment amount in CAD"
                                                value={walletAmount}
                                                onChange={(e) => setWalletAmount(e.target.value)}
                                                disabled={walletSubmitting !== null}
                                            />
                                            <Input
                                                placeholder="Reason (min 3 chars)"
                                                aria-label="Reason for adjustment"
                                                value={walletReason}
                                                onChange={(e) => setWalletReason(e.target.value)}
                                                disabled={walletSubmitting !== null}
                                                maxLength={200}
                                            />
                                        </div>

                                        <div className="flex gap-2">
                                            <Button
                                                className="flex-1 bg-emerald-600 hover:bg-emerald-700"
                                                disabled={walletSubmitting !== null}
                                                onClick={() => requestWalletAction("credit")}
                                            >
                                                <Plus className="h-4 w-4 mr-1" />
                                                {walletSubmitting === "credit" ? "Crediting..." : "Credit"}
                                            </Button>
                                            <Button
                                                className="flex-1"
                                                variant="outline"
                                                disabled={walletSubmitting !== null}
                                                onClick={() => requestWalletAction("debit")}
                                            >
                                                <Minus className="h-4 w-4 mr-1" />
                                                {walletSubmitting === "debit" ? "Debiting..." : "Debit"}
                                            </Button>
                                        </div>

                                        {walletError && (
                                            <p className="text-sm text-red-600 dark:text-red-400 bg-red-500/10 border border-red-500/20 rounded-md px-3 py-2">
                                                {walletError}
                                            </p>
                                        )}

                                        <div>
                                            <Label className="text-xs text-muted-foreground mb-2 block">
                                                Transaction Ledger ({walletData.transactions?.length || 0})
                                            </Label>
                                            {(!walletData.transactions || walletData.transactions.length === 0) ? (
                                                <p className="text-sm text-muted-foreground text-center py-4 bg-muted/30 rounded-md">
                                                    No transactions yet
                                                </p>
                                            ) : (
                                                <div className="space-y-1 max-h-64 overflow-y-auto border rounded-md">
                                                    {walletData.transactions.map((t: any) => {
                                                        const amt = Number(t.amount);
                                                        const isCredit = amt >= 0;
                                                        return (
                                                            <div key={t.id} className="flex items-start justify-between gap-2 px-3 py-2 border-b last:border-b-0 hover:bg-muted/30">
                                                                <div className="flex-1 min-w-0">
                                                                    <div className="flex items-center gap-2">
                                                                        <Badge variant="outline" className="text-[10px] font-mono">
                                                                            {t.type}
                                                                        </Badge>
                                                                        <span className="text-xs text-muted-foreground">
                                                                            {formatDate(t.created_at)}
                                                                        </span>
                                                                    </div>
                                                                    {t.description && (
                                                                        <p className="text-xs mt-1 truncate" title={t.description}>
                                                                            {t.description}
                                                                        </p>
                                                                    )}
                                                                    {t.metadata?.reason && (
                                                                        <p className="text-[11px] text-muted-foreground mt-0.5">
                                                                            Reason: {t.metadata.reason}
                                                                        </p>
                                                                    )}
                                                                </div>
                                                                <div className="text-right shrink-0">
                                                                    <p className={`text-sm font-semibold ${isCredit ? "text-success" : "text-destructive"}`}>
                                                                        {isCredit ? "+" : ""}${amt.toFixed(2)}
                                                                    </p>
                                                                    <p className="text-[10px] text-muted-foreground">
                                                                        bal ${Number(t.balance_after).toFixed(2)}
                                                                    </p>
                                                                </div>
                                                            </div>
                                                        );
                                                    })}
                                                </div>
                                            )}
                                        </div>
                                    </div>
                                ) : walletError ? (
                                    <p className="text-sm text-red-600 dark:text-red-400">{walletError}</p>
                                ) : (
                                    <p className="text-sm text-muted-foreground">Loading wallet...</p>
                                )}
                            </div>

                            {/* Saved cards (from Stripe — masked last-4 only) */}
                            <div className="border-t pt-4">
                                <Label className="text-xs text-muted-foreground mb-2 flex items-center gap-1">
                                    <CreditCard className="h-3 w-3" /> Saved cards
                                </Label>
                                {userDetail == null ? (
                                    <p className="text-sm text-muted-foreground">Loading…</p>
                                ) : userDetail.cards_error ? (
                                    <p className="text-sm text-red-600 dark:text-red-400">{userDetail.cards_error}</p>
                                ) : !userDetail.cards || userDetail.cards.length === 0 ? (
                                    <p className="text-sm text-muted-foreground">No cards on file.</p>
                                ) : (
                                    <div className="space-y-1.5">
                                        {userDetail.cards.map((c: any) => (
                                            <div key={c.id} className="flex items-center justify-between rounded-md border px-3 py-2 text-sm">
                                                <span className="flex items-center gap-2">
                                                    <span className="font-medium">{c.brand || "Card"}</span>
                                                    <span className="font-mono text-muted-foreground">•••• {c.last4}</span>
                                                    {c.is_default && <Badge variant="secondary" className="text-[10px]">Default</Badge>}
                                                </span>
                                                {c.exp_month != null && c.exp_year != null && (
                                                    <span className="text-xs text-muted-foreground">
                                                        {String(c.exp_month).padStart(2, "0")}/{String(c.exp_year).slice(-2)}
                                                    </span>
                                                )}
                                            </div>
                                        ))}
                                    </div>
                                )}
                            </div>

                            {/* Recent rides */}
                            <div className="border-t pt-4">
                                <Label className="text-xs text-muted-foreground mb-2 flex items-center gap-1">
                                    <Car className="h-3 w-3" /> Recent rides
                                </Label>
                                {userDetail == null ? (
                                    <p className="text-sm text-muted-foreground">Loading…</p>
                                ) : !userDetail.recent_rides || userDetail.recent_rides.length === 0 ? (
                                    <p className="text-sm text-muted-foreground">No rides yet.</p>
                                ) : (
                                    <>
                                        {/* This panel only ever fetches the 10 most recent rides
                                            (backend/routes/admin/users.py); older rows -- including
                                            any legacy-imported ones -- won't appear here. Surface the
                                            true total so that reads as "not on this page", not "never
                                            imported" (A30 Finding 2,
                                            docs/audit/2026-08-13-migrated-data-visibility-audit.md). */}
                                        {typeof userDetail.total_rides === "number" && userDetail.total_rides > userDetail.recent_rides.length && (
                                            <p className="text-[11px] text-muted-foreground mb-1.5">
                                                Showing {userDetail.recent_rides.length} most recent of {userDetail.total_rides} total rides —{" "}
                                                <a href={`/dashboard/rides?search=${encodeURIComponent(userDetail.phone || userDetail.email || "")}`} className="underline hover:text-foreground">
                                                    view all
                                                </a>
                                            </p>
                                        )}
                                        <div className="space-y-1 max-h-72 overflow-y-auto border rounded-md">
                                            {userDetail.recent_rides.map((r: any) => (
                                                <a
                                                    key={r.id}
                                                    href={`/dashboard/rides?id=${r.id}`}
                                                    className="block px-3 py-2 border-b last:border-b-0 hover:bg-muted/30 text-xs"
                                                >
                                                    <div className="flex items-center justify-between gap-2">
                                                        <span className="font-medium truncate">
                                                            {(r.pickup_address || "Pickup").split(",")[0]} → {(r.dropoff_address || "Dropoff").split(",")[0]}
                                                        </span>
                                                        <Badge variant="outline" className={`text-[10px] shrink-0 ${
                                                            r.status === "completed" ? "text-success"
                                                            : r.status === "cancelled" ? "text-destructive" : "text-warning"}`}>
                                                            {r.status}
                                                        </Badge>
                                                    </div>
                                                    <div className="mt-0.5 flex items-center justify-between text-muted-foreground">
                                                        <span className="flex items-center gap-1">
                                                            {formatDate(r.created_at)}
                                                            {r.legacy_import_metadata && Object.keys(r.legacy_import_metadata).length > 0 && (
                                                                <span className="text-[9px] font-medium bg-muted rounded px-1 py-0.5">Imported</span>
                                                            )}
                                                        </span>
                                                        <span>{r.total_fare != null ? `$${Number(r.total_fare).toFixed(2)}` : "—"}</span>
                                                    </div>
                                                </a>
                                            ))}
                                        </div>
                                    </>
                                )}
                            </div>
                        </div>
                    )}
                </DialogContent>
            </Dialog>

            <AlertDialog open={!!pendingWalletAction} onOpenChange={(open) => !open && setPendingWalletAction(null)}>
                <AlertDialogContent>
                    <AlertDialogHeader>
                        <AlertDialogTitle>
                            {pendingWalletAction?.action === "credit" ? "Credit this wallet?" : "Debit this wallet?"}
                        </AlertDialogTitle>
                        <AlertDialogDescription>
                            {pendingWalletAction && selectedUser ? (
                                <>
                                    {pendingWalletAction.action === "credit" ? "Add" : "Remove"}{" "}
                                    <span className="font-semibold">${pendingWalletAction.amount.toFixed(2)} CAD</span>{" "}
                                    {pendingWalletAction.action === "credit" ? "to" : "from"}{" "}
                                    <span className="font-semibold">{selectedUser.name}</span>&rsquo;s wallet.
                                    <br />
                                    Reason: <span className="italic">{pendingWalletAction.reason}</span>
                                    <br />
                                    This will be recorded in the audit log and cannot be undone without a reverse adjustment.
                                </>
                            ) : null}
                        </AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                        <AlertDialogCancel disabled={walletSubmitting !== null}>Cancel</AlertDialogCancel>
                        <AlertDialogAction
                            className={pendingWalletAction?.action === "credit" ? "bg-emerald-600 hover:bg-emerald-700" : "bg-destructive text-destructive-foreground hover:bg-destructive/90"}
                            disabled={walletSubmitting !== null}
                            onClick={(e) => {
                                e.preventDefault();
                                void confirmWalletAction();
                            }}
                        >
                            {pendingWalletAction?.action === "credit"
                                ? (walletSubmitting === "credit" ? "Crediting…" : "Confirm credit")
                                : (walletSubmitting === "debit" ? "Debiting…" : "Confirm debit")}
                        </AlertDialogAction>
                    </AlertDialogFooter>
                </AlertDialogContent>
            </AlertDialog>

            <AlertDialog open={!!pendingStatusChange} onOpenChange={(open) => { if (!open) { setPendingStatusChange(null); setModerationReason(""); setModerationDays(""); } }}>
                <AlertDialogContent>
                    <AlertDialogHeader>
                        <AlertDialogTitle>
                            {pendingStatusChange?.status === "banned" ? "Ban this user?" : "Suspend this user?"}
                        </AlertDialogTitle>
                        <AlertDialogDescription>
                            {pendingStatusChange?.status === "banned"
                                ? `${pendingStatusChange?.name} will be deactivated and unable to book rides until an admin reactivates the account.`
                                : `${pendingStatusChange?.name} will be unable to book rides for the chosen period.`}
                        </AlertDialogDescription>
                    </AlertDialogHeader>

                    <div className="space-y-3 py-1">
                        <div className="space-y-1.5">
                            <Label className="text-xs">Reason <span className="text-destructive">*</span></Label>
                            <div className="flex flex-wrap gap-1.5">
                                {["Payment / chargeback", "Safety report", "Policy violation", "Fraud / abuse"].map((r) => (
                                    <button
                                        key={r}
                                        type="button"
                                        onClick={() => setModerationReason(r)}
                                        className="text-[11px] rounded-full border px-2 py-0.5 hover:bg-muted"
                                    >
                                        {r}
                                    </button>
                                ))}
                            </div>
                            <Textarea
                                value={moderationReason}
                                onChange={(e) => setModerationReason(e.target.value)}
                                placeholder="Shown to the rider and recorded in the audit log."
                                className="min-h-[72px] text-sm"
                            />
                        </div>
                        {pendingStatusChange?.status === "suspended" && (
                            <div className="space-y-1.5">
                                <Label className="text-xs">Duration</Label>
                                <Select value={moderationDays || "0"} onValueChange={(v) => setModerationDays(v === "0" ? "" : v)}>
                                    <SelectTrigger className="h-9 text-sm"><SelectValue /></SelectTrigger>
                                    <SelectContent>
                                        <SelectItem value="0">Indefinite (until reactivated)</SelectItem>
                                        <SelectItem value="1">24 hours</SelectItem>
                                        <SelectItem value="7">7 days</SelectItem>
                                        <SelectItem value="30">30 days</SelectItem>
                                    </SelectContent>
                                </Select>
                            </div>
                        )}
                    </div>

                    <AlertDialogFooter>
                        <AlertDialogCancel>Cancel</AlertDialogCancel>
                        <AlertDialogAction
                            className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
                            disabled={!moderationReason.trim() || statusUpdating === pendingStatusChange?.id}
                            onClick={async (e) => {
                                if (!pendingStatusChange) return;
                                if (!moderationReason.trim()) { e.preventDefault(); return; }
                                const change = pendingStatusChange;
                                const reason = moderationReason.trim();
                                const suspended_until =
                                    change.status === "suspended" && moderationDays
                                        ? new Date(Date.now() + Number(moderationDays) * 86400000).toISOString()
                                        : undefined;
                                setStatusUpdating(change.id);
                                try {
                                    await updateUserStatus(change.id, { status: change.status, reason, suspended_until });
                                    const patch = { status: change.status, status_reason: reason, suspended_until: suspended_until ?? null };
                                    setSelectedUser((prev: any) => prev ? { ...prev, ...patch } : prev);
                                    setUsers(prev => prev.map(u => u.id === change.id ? { ...u, ...patch } : u));
                                    toast({ title: `User ${change.status === "banned" ? "banned" : "suspended"}` });
                                } catch (err: any) {
                                    console.error('[UsersPage] Failed to update user status:', err);
                                    toast({ title: "Failed to update user status", description: err?.message, variant: "destructive" });
                                } finally {
                                    setStatusUpdating(null);
                                }
                                setPendingStatusChange(null);
                                setModerationReason("");
                                setModerationDays("");
                            }}
                        >
                            {pendingStatusChange?.status === "banned" ? "Ban user" : "Suspend user"}
                        </AlertDialogAction>
                    </AlertDialogFooter>
                </AlertDialogContent>
            </AlertDialog>
        </div>
    );
}
