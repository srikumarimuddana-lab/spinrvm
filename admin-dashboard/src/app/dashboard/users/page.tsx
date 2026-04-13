"use client";

import { useEffect, useState, useMemo } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
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
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select";
import { Users, Search, Mail, Phone, MapPin, Star, Calendar, Car, ShieldCheck, ShieldAlert, Download, RefreshCw, ChevronLeft, ChevronRight, Ban, CheckCircle, AlertTriangle } from "lucide-react";
import { formatDate } from "@/lib/utils";
import { getUsers, getUserDetails, updateUserStatus, getStats } from "@/lib/api";

const PER_PAGE = 25;

export default function UsersPage() {
    const [users, setUsers] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");
    const [search, setSearch] = useState("");
    const [statusUpdating, setStatusUpdating] = useState<string | null>(null);
    const [verifiedFilter, setVerifiedFilter] = useState("all");
    const [selectedUser, setSelectedUser] = useState<any>(null);
    const [page, setPage] = useState(1);

    useEffect(() => {
        fetchUsers();
    }, []);

    const fetchUsers = async () => {
        setLoading(true);
        setError("");
        try {
            const [usersData, statsData] = await Promise.all([
                getUsers(),
                getStats()
            ]);
            const transformedUsers = (usersData || []).map((u: any) => ({
                id: u.id,
                name: `${u.first_name || ''} ${u.last_name || ''}`.trim() || u.email || u.phone,
                email: u.email,
                phone: u.phone,
                created_at: u.created_at,
                total_rides: u.total_rides || 0,
                rating: u.rating || null,
                is_verified: u.is_verified ?? true,
                city: u.city,
            }));
            setUsers(transformedUsers);
        } catch (err: any) {
            console.error('Failed to fetch users:', err);
            setError("Failed to load users. Please try again.");
            setUsers([]);
        } finally {
            setLoading(false);
        }
    };

    const filtered = useMemo(() => {
        return users.filter((u) => {
            const matchSearch =
                !search ||
                u.name?.toLowerCase().includes(search.toLowerCase()) ||
                u.email?.toLowerCase().includes(search.toLowerCase()) ||
                u.phone?.toLowerCase().includes(search.toLowerCase()) ||
                u.city?.toLowerCase().includes(search.toLowerCase());
            let matchVerified = true;
            if (verifiedFilter === "verified") matchVerified = u.is_verified === true;
            else if (verifiedFilter === "unverified") matchVerified = u.is_verified !== true;
            return matchSearch && matchVerified;
        });
    }, [users, search, verifiedFilter]);

    const totalPages = Math.max(1, Math.ceil(filtered.length / PER_PAGE));
    const paginated = filtered.slice((page - 1) * PER_PAGE, page * PER_PAGE);

    useEffect(() => { setPage(1); }, [search, verifiedFilter]);

    const handleExport = () => {
        const headers = ["ID", "Name", "Email", "Phone", "City", "Total Rides", "Rating", "Verified", "Joined Date"];
        const rows = filtered.map(u => [
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
    };

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
                    <Button variant="outline" onClick={handleExport} disabled={filtered.length === 0}>
                        <Download className="mr-2 h-4 w-4" /> Export CSV
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
                                <p className="text-xs text-muted-foreground">Total Users</p>
                                <p className="text-2xl font-bold">{users.length}</p>
                            </div>
                        </div>
                    </CardContent>
                </Card>
                <Card>
                    <CardContent className="pt-4 pb-3">
                        <div className="flex items-center gap-2">
                            <ShieldCheck className="h-5 w-5 text-emerald-500" />
                            <div>
                                <p className="text-xs text-muted-foreground">Verified</p>
                                <p className="text-2xl font-bold">{users.filter(u => u.is_verified).length}</p>
                            </div>
                        </div>
                    </CardContent>
                </Card>
                <Card>
                    <CardContent className="pt-4 pb-3">
                        <div className="flex items-center gap-2">
                            <ShieldAlert className="h-5 w-5 text-amber-500" />
                            <div>
                                <p className="text-xs text-muted-foreground">Unverified</p>
                                <p className="text-2xl font-bold">{users.filter(u => !u.is_verified).length}</p>
                            </div>
                        </div>
                    </CardContent>
                </Card>
                <Card>
                    <CardContent className="pt-4 pb-3">
                        <div className="flex items-center gap-2">
                            <Star className="h-5 w-5 text-amber-400 fill-amber-400" />
                            <div>
                                <p className="text-xs text-muted-foreground">Avg Rating</p>
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
                        placeholder="Search by name, email, phone, or city..."
                        value={search}
                        onChange={(e) => setSearch(e.target.value)}
                        className="pl-9"
                    />
                </div>
                <Select value={verifiedFilter} onValueChange={setVerifiedFilter}>
                    <SelectTrigger className="w-40">
                        <SelectValue placeholder="Filter by verification" />
                    </SelectTrigger>
                    <SelectContent>
                        <SelectItem value="all">All Users</SelectItem>
                        <SelectItem value="verified">Verified</SelectItem>
                        <SelectItem value="unverified">Unverified</SelectItem>
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
                                    {filtered.length === 0 ? (
                                        <TableRow>
                                            <TableCell colSpan={8} className="text-center text-muted-foreground py-12">
                                                No users found.
                                            </TableCell>
                                        </TableRow>
                                    ) : (
                                        paginated.map((user) => (
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
                                                            {user.email || "—"}
                                                        </div>
                                                        <div className="flex items-center gap-1 text-muted-foreground">
                                                            <Phone className="h-3 w-3" />
                                                            {user.phone || "—"}
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

                            {/* Pagination */}
                            {filtered.length > PER_PAGE && (
                                <div className="flex items-center justify-between px-4 py-3 border-t">
                                    <p className="text-sm text-muted-foreground">
                                        Showing {(page - 1) * PER_PAGE + 1}–{Math.min(page * PER_PAGE, filtered.length)} of {filtered.length}
                                    </p>
                                    <div className="flex items-center gap-2">
                                        <Button variant="outline" size="sm" onClick={() => setPage(p => Math.max(1, p - 1))} disabled={page === 1}>
                                            <ChevronLeft className="h-4 w-4" />
                                        </Button>
                                        <span className="text-sm text-muted-foreground">Page {page} of {totalPages}</span>
                                        <Button variant="outline" size="sm" onClick={() => setPage(p => Math.min(totalPages, p + 1))} disabled={page === totalPages}>
                                            <ChevronRight className="h-4 w-4" />
                                        </Button>
                                    </div>
                                </div>
                            )}
                        </>
                    )}
                </CardContent>
            </Card>

            {/* User Details Dialog */}
            <Dialog open={!!selectedUser} onOpenChange={(open) => { if (!open) setSelectedUser(null); }}>
                <DialogContent className="sm:max-w-lg">
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
                                    <p className="text-sm">{selectedUser.email}</p>
                                </div>
                                <div className="space-y-1">
                                    <Label className="text-xs text-muted-foreground flex items-center gap-1">
                                        <Phone className="h-3 w-3" /> Phone
                                    </Label>
                                    <p className="text-sm">{selectedUser.phone}</p>
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
                                                } catch {} finally { setStatusUpdating(null); }
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
                                            onClick={async () => {
                                                setStatusUpdating(selectedUser.id);
                                                try {
                                                    await updateUserStatus(selectedUser.id, { status: "suspended" });
                                                    setSelectedUser({ ...selectedUser, status: "suspended" });
                                                    setUsers(prev => prev.map(u => u.id === selectedUser.id ? { ...u, status: "suspended" } : u));
                                                } catch {} finally { setStatusUpdating(null); }
                                            }}
                                        >
                                            <AlertTriangle className="h-4 w-4 mr-2 text-amber-600" /> Suspend
                                        </Button>
                                    )}
                                    {selectedUser.status !== "banned" && (
                                        <Button
                                            className="flex-1"
                                            variant="destructive"
                                            disabled={statusUpdating === selectedUser.id}
                                            onClick={async () => {
                                                setStatusUpdating(selectedUser.id);
                                                try {
                                                    await updateUserStatus(selectedUser.id, { status: "banned" });
                                                    setSelectedUser({ ...selectedUser, status: "banned" });
                                                    setUsers(prev => prev.map(u => u.id === selectedUser.id ? { ...u, status: "banned" } : u));
                                                } catch {} finally { setStatusUpdating(null); }
                                            }}
                                        >
                                            <Ban className="h-4 w-4 mr-2" /> Ban
                                        </Button>
                                    )}
                                </div>
                            </div>
                        </div>
                    )}
                </DialogContent>
            </Dialog>
        </div>
    );
}