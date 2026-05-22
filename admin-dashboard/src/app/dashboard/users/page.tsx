"use client";

import { useEffect, useRef, useState } from "react";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
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
import { Users, Search, Mail, Phone, MapPin, Star, Calendar, Car, ShieldCheck, Download, RefreshCw, Ban, CheckCircle, AlertTriangle, Wallet, Plus, Minus, Eye, EyeOff } from "lucide-react";
import { formatDate } from "@/lib/utils";
import { getUsersPaginated, updateUserStatus, getStats, getUserWallet, creditUserWallet, debitUserWallet, exportUsers, logPiiReveal } from "@/lib/api";
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
    const [roleFilter, setRoleFilter] = useState<"all" | "rider" | "driver">("all");
    const [selectedUser, setSelectedUser] = useState<any>(null);
    const [page, setPage] = useState(0);
    const [hasNextPage, setHasNextPage] = useState(false);
    const [stats, setStats] = useState<{ total_users: number; total_drivers: number } | null>(null);
    const reqIdRef = useRef(0);

    const [showPii, setShowPii] = useState(false);

    const [pendingStatusChange, setPendingStatusChange] = useState<{ id: string; status: "suspended" | "banned"; name: string } | null>(null);

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

    const handleWalletAction = async (action: "credit" | "debit") => {
        if (!selectedUser?.id || !walletAmount || !/^\d+(\.\d{1,2})?$/.test(walletAmount.trim()) || parseFloat(walletAmount) <= 0) {
            setWalletError("Enter a positive amount");
            return;
        }
        if (!walletReason.trim() || walletReason.trim().length < 3) {
            setWalletError("Reason must be at least 3 characters");
            return;
        }
        setWalletSubmitting(action);
        setWalletError("");
        try {
            const fn = action === "credit" ? creditUserWallet : debitUserWallet;
            const result = await fn(selectedUser.id, parseFloat(walletAmount), walletReason.trim()); // backend converts to Decimal
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
                email: u.email,
                phone: u.phone,
                role: u.role,
                created_at: u.created_at,
                total_rides: u.total_rides || 0,
                rating: u.rating || null,
                is_verified: u.is_verified ?? true,
                city: u.city,
                status: u.status || "active",
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
            const headers = ["ID", "Name", "Email", "Phone", "City", "Total Rides", "Rating", "Verified", "Joined Date"];
            const rows = (res.users || []).map((u: any) => [
                u.id,
                u.name,
                u.email,
                u.phone,
                u.city || "N/A",
                u.total_rides || 0,
                u.rating || "N/A",
                u.is_verified ? "Yes" : "No",
                formatDate(u.created_at),
            ]);
            const csv = [headers, ...rows].map(row => row.join(",")).join("\n");
            const blob = new Blob([csv], { type: "text/csv" });
            const url = URL.createObjectURL(blob);
            const a = document.createElement("a");
            a.href = url;
            a.download = `users-${new Date().toISOString().split("T")[0]}.csv`;
            a.click();
            URL.revokeObjectURL(url);
            toast({ title: "Export complete", description: `${res.count ?? 0} users exported.` });
        } catch {
            toast({ title: "Export failed", variant: "destructive" });
        }
    };

    const totalForRoleStat = roleFilter === "driver"
        ? stats?.total_drivers
        : roleFilter === "rider"
            ? (stats && (stats.total_users - (stats.total_drivers || 0)))
            : stats?.total_users;

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
                    <Button variant="outline" size="icon" onClick={fetchUsers} disabled={loading}>
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
                            <ShieldCheck className="h-5 w-5 text-emerald-500" />
                            <div>
                                <p className="text-xs text-muted-foreground">Verified (page)</p>
                                <p className="text-2xl font-bold">{users.filter(u => u.is_verified).length}</p>
                            </div>
                        </div>
                    </CardContent>
                </Card>
                <Card>
                    <CardContent className="pt-4 pb-3">
                        <div className="flex items-center gap-2">
                            <Star className="h-5 w-5 text-amber-400 fill-amber-400" />
                            <div>
                                <p className="text-xs text-muted-foreground">Avg Rating (page)</p>
                                <p className="text-2xl font-bold">
                                    {(() => {
                                        const rated = users.filter(u => u.rating != null);
                                        return rated.length > 0
                                            ? (rated.reduce((s, u) => s + u.rating, 0) / rated.length).toFixed(1)
                                            : "N/A";
                                    })()}
                                </p>
                            </div>
                        </div>
                    </CardContent>
                </Card>
                <Card>
                    <CardContent className="pt-4 pb-3">
                        <div className="flex items-center gap-2">
                            <Car className="h-5 w-5 text-violet-500" />
                            <div>
                                <p className="text-xs text-muted-foreground">Total Rides (page)</p>
                                <p className="text-2xl font-bold">
                                    {users.reduce((s, u) => s + (u.total_rides || 0), 0)}
                                </p>
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
                <Select value={roleFilter} onValueChange={(v) => setRoleFilter(v as "all" | "rider" | "driver")}>
                    <SelectTrigger className="w-36" aria-label="Filter by role">
                        <SelectValue placeholder="Role" />
                    </SelectTrigger>
                    <SelectContent>
                        <SelectItem value="all">All Roles</SelectItem>
                        <SelectItem value="rider">Riders</SelectItem>
                        <SelectItem value="driver">Drivers</SelectItem>
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
                                        <TableHead>Name</TableHead>
                                        <TableHead>Contact</TableHead>
                                        <TableHead>City</TableHead>
                                        <TableHead>Rides</TableHead>
                                        <TableHead>Rating</TableHead>
                                        <TableHead>Status</TableHead>
                                        <TableHead>Joined</TableHead>
                                        <TableHead className="text-right">Actions</TableHead>
                                    </TableRow>
                                </TableHeader>
                                <TableBody>
                                    {users.length === 0 ? (
                                        <TableRow>
                                            <TableCell colSpan={8} className="text-center text-muted-foreground py-12">
                                                No users found.
                                            </TableCell>
                                        </TableRow>
                                    ) : (
                                        users.map((user) => (
                                            <TableRow key={user.id} className="cursor-pointer hover:bg-muted/50" onClick={() => setSelectedUser(user)}>
                                                <TableCell>
                                                    <div>
                                                        <p className="font-medium">{user.name}</p>
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
                                                    <div className="flex items-center gap-1">
                                                        <MapPin className="h-3 w-3 text-muted-foreground" />
                                                        {user.city || "N/A"}
                                                    </div>
                                                </TableCell>
                                                <TableCell>
                                                    <div className="flex items-center gap-1">
                                                        <Car className="h-3 w-3 text-muted-foreground" />
                                                        {user.total_rides || 0}
                                                    </div>
                                                </TableCell>
                                                <TableCell>
                                                    <div className="flex items-center gap-1">
                                                        <Star className="h-3.5 w-3.5 fill-amber-400 text-amber-400" />
                                                        {user.rating != null ? user.rating.toFixed(1) : "N/A"}
                                                    </div>
                                                </TableCell>
                                                <TableCell>
                                                    <Badge className={
                                                        user.status === "banned" ? "bg-red-500/15 text-red-600"
                                                        : user.status === "suspended" ? "bg-amber-500/15 text-amber-600"
                                                        : "bg-emerald-500/15 text-emerald-600"
                                                    }>
                                                        {user.status === "banned" ? "Banned"
                                                        : user.status === "suspended" ? "Suspended"
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
                <DialogContent className="sm:max-w-lg max-h-[90vh] overflow-y-auto">
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
                                    {selectedUser.name.charAt(0).toUpperCase()}
                                </div>
                                <div>
                                    <p className="text-lg font-semibold">{selectedUser.name}</p>
                                    <Badge variant={selectedUser.is_verified ? "default" : "secondary"} className={selectedUser.is_verified ? "bg-emerald-500" : ""}>
                                        {selectedUser.is_verified ? "Verified" : "Unverified"}
                                    </Badge>
                                </div>
                            </div>

                            <div className="grid grid-cols-2 gap-4">
                                <div className="space-y-1">
                                    <Label className="text-xs text-muted-foreground flex items-center gap-1">
                                        <Mail className="h-3 w-3" /> Email
                                    </Label>
                                    <p className="text-sm">{showPii ? selectedUser.email : maskEmail(selectedUser.email)}</p>
                                </div>
                                <div className="space-y-1">
                                    <Label className="text-xs text-muted-foreground flex items-center gap-1">
                                        <Phone className="h-3 w-3" /> Phone
                                    </Label>
                                    <p className="text-sm">{showPii ? selectedUser.phone : maskPhone(selectedUser.phone)}</p>
                                </div>
                                <div className="space-y-1">
                                    <Label className="text-xs text-muted-foreground flex items-center gap-1">
                                        <MapPin className="h-3 w-3" /> City
                                    </Label>
                                    <p className="text-sm">{selectedUser.city || "N/A"}</p>
                                </div>
                                <div className="space-y-1">
                                    <Label className="text-xs text-muted-foreground flex items-center gap-1">
                                        <Calendar className="h-3 w-3" /> Joined
                                    </Label>
                                    <p className="text-sm">{formatDate(selectedUser.created_at)}</p>
                                </div>
                            </div>

                            <div className="border-t pt-4">
                                <div className="grid grid-cols-2 gap-4">
                                    <div className="rounded-lg bg-muted/50 p-3 text-center">
                                        <p className="text-xs text-muted-foreground">Total Rides</p>
                                        <p className="text-2xl font-bold">{selectedUser.total_rides || 0}</p>
                                    </div>
                                    <div className="rounded-lg bg-muted/50 p-3 text-center">
                                        <p className="text-xs text-muted-foreground">Rating</p>
                                        <p className="text-2xl font-bold flex items-center justify-center gap-1">
                                            <Star className="h-4 w-4 fill-amber-400 text-amber-400" />
                                            {selectedUser.rating != null ? selectedUser.rating.toFixed(1) : "N/A"}
                                        </p>
                                    </div>
                                </div>
                            </div>

                            {/* Status Management */}
                            <div className="border-t pt-4">
                                <Label className="text-xs text-muted-foreground mb-2 block">Account Status</Label>
                                <div className="flex items-center gap-2 mb-3">
                                    <Badge className={
                                        selectedUser.status === "banned" ? "bg-red-500/15 text-red-600"
                                        : selectedUser.status === "suspended" ? "bg-amber-500/15 text-amber-600"
                                        : "bg-emerald-500/15 text-emerald-600"
                                    }>
                                        {selectedUser.status === "banned" ? "Banned"
                                        : selectedUser.status === "suspended" ? "Suspended"
                                        : "Active"}
                                    </Badge>
                                </div>
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
                                            <CheckCircle className="h-4 w-4 mr-2 text-green-600" /> Activate
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
                                            <AlertTriangle className="h-4 w-4 mr-2 text-amber-600" /> Suspend
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
                                                onClick={() => handleWalletAction("credit")}
                                            >
                                                <Plus className="h-4 w-4 mr-1" />
                                                {walletSubmitting === "credit" ? "Crediting..." : "Credit"}
                                            </Button>
                                            <Button
                                                className="flex-1"
                                                variant="outline"
                                                disabled={walletSubmitting !== null}
                                                onClick={() => handleWalletAction("debit")}
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
                                                                    <p className={`text-sm font-semibold ${isCredit ? "text-emerald-600" : "text-red-600"}`}>
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
                        </div>
                    )}
                </DialogContent>
            </Dialog>

            <AlertDialog open={!!pendingStatusChange} onOpenChange={(open) => !open && setPendingStatusChange(null)}>
                <AlertDialogContent>
                    <AlertDialogHeader>
                        <AlertDialogTitle>
                            {pendingStatusChange?.status === "banned" ? "Ban this user?" : "Suspend this user?"}
                        </AlertDialogTitle>
                        <AlertDialogDescription>
                            {pendingStatusChange?.status === "banned"
                                ? `${pendingStatusChange?.name} will be permanently banned and unable to book rides or log in.`
                                : `${pendingStatusChange?.name} will be suspended and unable to book rides until reactivated.`}
                        </AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                        <AlertDialogCancel>Cancel</AlertDialogCancel>
                        <AlertDialogAction
                            className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
                            onClick={async () => {
                                if (!pendingStatusChange) return;
                                setStatusUpdating(pendingStatusChange.id);
                                try {
                                    await updateUserStatus(pendingStatusChange.id, { status: pendingStatusChange.status });
                                    setSelectedUser((prev: any) => prev ? { ...prev, status: pendingStatusChange.status } : prev);
                                    setUsers(prev => prev.map(u => u.id === pendingStatusChange.id ? { ...u, status: pendingStatusChange.status } : u));
                                    toast({ title: `User ${pendingStatusChange.status === "banned" ? "banned" : "suspended"}` });
                                } catch (err: any) {
                                    console.error('[UsersPage] Failed to update user status:', err);
                                    toast({ title: "Failed to update user status", description: err?.message, variant: "destructive" });
                                } finally {
                                    setStatusUpdating(null);
                                }
                                setPendingStatusChange(null);
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
