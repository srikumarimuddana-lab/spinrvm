"use client";

import { useEffect, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
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
import { HelpCircle, Search, MessageSquare, CheckCircle, XCircle, Clock } from "lucide-react";
import { formatDate } from "@/lib/utils";
import { getDisputes, resolveDispute } from "@/lib/api";

// Mock dispute data - replace with API calls when backend is ready
const mockDisputes = [
    {
        id: "1",
        ride_id: "abc123",
        user_name: "John Doe",
        user_type: "rider",
        dispute_type: "fare",
        description: "I was charged more than the estimated fare",
        status: "pending",
        created_at: "2024-01-15T10:30:00Z",
        resolution: null,
    },
    {
        id: "2",
        ride_id: "def456",
        user_name: "Jane Smith",
        user_type: "driver",
        dispute_type: "behavior",
        description: "Rider was rude and damaged the vehicle",
        status: "resolved",
        created_at: "2024-01-14T08:15:00Z",
        resolution: "Refunded $25 to driver for cleaning fees",
    },
];

const STATUS_CONFIG = {
    pending: { label: "Pending", icon: Clock, color: "bg-amber-500/15 text-amber-600" },
    resolved: { label: "Resolved", icon: CheckCircle, color: "bg-emerald-500/15 text-emerald-600" },
    rejected: { label: "Rejected", icon: XCircle, color: "bg-red-500/15 text-red-600" },
};

const DISPUTE_TYPES = [
    { value: "fare", label: "Fare Dispute" },
    { value: "behavior", label: "Behavior Issue" },
    { value: "route", label: "Route Issue" },
    { value: "safety", label: "Safety Concern" },
    { value: "other", label: "Other" },
];

export default function DisputesTab() {
    const [disputes, setDisputes] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [search, setSearch] = useState("");
    const [statusFilter, setStatusFilter] = useState("all");
    const [selectedDispute, setSelectedDispute] = useState<any>(null);
    const [resolutionDialogOpen, setResolutionDialogOpen] = useState(false);
    const [resolution, setResolution] = useState("");
    const [disputeStats, setDisputeStats] = useState({ pending: 0, resolved: 0, rejected: 0 });

    useEffect(() => {
        fetchDisputes();
    }, []);

    const fetchDisputes = async () => {
        setLoading(true);
        try {
            const data = await getDisputes();
            const transformed = (data || []).map((d: any) => ({
                id: d.id,
                ride_id: d.ride_id,
                user_name: d.user_name || 'Unknown',
                user_type: 'rider',
                dispute_type: d.reason || 'other',
                description: d.description,
                status: d.status,
                created_at: d.created_at,
                resolution: d.admin_note || d.resolution,
            }));
            setDisputes(transformed);
            setDisputeStats({
                pending: transformed.filter((d: any) => d.status === 'open' || d.status === 'pending').length,
                resolved: transformed.filter((d: any) => d.status === 'resolved').length,
                rejected: transformed.filter((d: any) => d.status === 'rejected').length,
            });
        } catch (error) {
            console.error('Failed to fetch disputes:', error);
            setDisputes(mockDisputes);
        } finally {
            setLoading(false);
        }
    };

    const filtered = disputes.filter((d) => {
        const matchSearch =
            !search ||
            d.user_name?.toLowerCase().includes(search.toLowerCase()) ||
            d.ride_id?.toLowerCase().includes(search.toLowerCase()) ||
            d.description?.toLowerCase().includes(search.toLowerCase());
        const matchStatus = statusFilter === "all" || d.status === statusFilter;
        return matchSearch && matchStatus;
    });

    const handleResolve = async () => {
        if (!selectedDispute || !resolution.trim()) return;

        try {
            await resolveDispute(selectedDispute.id, {
                resolution: 'approved',
                refund_amount: selectedDispute.requested_amount,
                admin_note: resolution.trim(),
            });
            await fetchDisputes();
            setResolutionDialogOpen(false);
            setResolution("");
            setSelectedDispute(null);
        } catch (error: any) {
            alert(`Failed to resolve dispute: ${error.message}`);
        }
    };

    const handleReject = async () => {
        if (!selectedDispute || !resolution.trim()) return;

        try {
            await resolveDispute(selectedDispute.id, {
                resolution: 'rejected',
                admin_note: resolution.trim(),
            });
            await fetchDisputes();
            setResolutionDialogOpen(false);
            setResolution("");
            setSelectedDispute(null);
        } catch (error: any) {
            alert(`Failed to reject dispute: ${error.message}`);
        }
    };

    return (
        <div className="space-y-6">
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-3xl font-bold tracking-tight flex items-center gap-2">
                        <HelpCircle className="h-8 w-8 text-amber-500" />
                        Disputes
                    </h1>
                    <p className="text-muted-foreground mt-1">
                        Review and resolve disputes from riders and drivers.
                    </p>
                </div>
            </div>

            {/* Filters */}
            <div className="flex flex-wrap items-center gap-3">
                <div className="relative flex-1 max-w-sm">
                    <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                    <Input
                        placeholder="Search disputes..."
                        value={search}
                        onChange={(e) => setSearch(e.target.value)}
                        className="pl-9"
                    />
                </div>
                <Select value={statusFilter} onValueChange={setStatusFilter}>
                    <SelectTrigger className="w-40">
                        <SelectValue placeholder="Filter by status" />
                    </SelectTrigger>
                    <SelectContent>
                        <SelectItem value="all">All Status</SelectItem>
                        <SelectItem value="pending">Pending</SelectItem>
                        <SelectItem value="resolved">Resolved</SelectItem>
                        <SelectItem value="rejected">Rejected</SelectItem>
                    </SelectContent>
                </Select>
            </div>

            {/* Stats */}
            <div className="grid grid-cols-3 gap-4">
                <Card>
                    <CardContent className="pt-4">
                        <div className="flex items-center gap-2">
                            <Clock className="h-5 w-5 text-amber-500" />
                            <div>
                                <p className="text-xs text-muted-foreground">Pending</p>
                                <p className="text-2xl font-bold">{disputeStats.pending}</p>
                            </div>
                        </div>
                    </CardContent>
                </Card>
                <Card>
                    <CardContent className="pt-4">
                        <div className="flex items-center gap-2">
                            <CheckCircle className="h-5 w-5 text-emerald-500" />
                            <div>
                                <p className="text-xs text-muted-foreground">Resolved</p>
                                <p className="text-2xl font-bold">{disputeStats.resolved}</p>
                            </div>
                        </div>
                    </CardContent>
                </Card>
                <Card>
                    <CardContent className="pt-4">
                        <div className="flex items-center gap-2">
                            <XCircle className="h-5 w-5 text-red-500" />
                            <div>
                                <p className="text-xs text-muted-foreground">Rejected</p>
                                <p className="text-2xl font-bold">{disputeStats.rejected}</p>
                            </div>
                        </div>
                    </CardContent>
                </Card>
            </div>

            {/* Table */}
            <Card className="border-border/50">
                <CardContent className="p-0">
                    {loading ? (
                        <div className="flex items-center justify-center p-12">
                            <div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" />
                        </div>
                    ) : (
                        <Table>
                            <TableHeader>
                                <TableRow>
                                    <TableHead>ID</TableHead>
                                    <TableHead>User</TableHead>
                                    <TableHead>Type</TableHead>
                                    <TableHead>Description</TableHead>
                                    <TableHead>Status</TableHead>
                                    <TableHead>Date</TableHead>
                                    <TableHead className="text-right">Actions</TableHead>
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                {filtered.length === 0 ? (
                                    <TableRow>
                                        <TableCell colSpan={7} className="text-center text-muted-foreground py-12">
                                            No disputes found.
                                        </TableCell>
                                    </TableRow>
                                ) : (
                                    filtered.map((dispute) => {
                                        const StatusIcon = STATUS_CONFIG[dispute.status as keyof typeof STATUS_CONFIG]?.icon || HelpCircle;
                                        return (
                                            <TableRow key={dispute.id} className="cursor-pointer hover:bg-muted/50" onClick={() => setSelectedDispute(dispute)}>
                                                <TableCell className="font-mono text-xs">{dispute.id}</TableCell>
                                                <TableCell>
                                                    <div>
                                                        <p className="font-medium">{dispute.user_name}</p>
                                                        <p className="text-xs text-muted-foreground capitalize">{dispute.user_type}</p>
                                                    </div>
                                                </TableCell>
                                                <TableCell>
                                                    <Badge variant="outline" className="text-xs">
                                                        {DISPUTE_TYPES.find(t => t.value === dispute.dispute_type)?.label || dispute.dispute_type}
                                                    </Badge>
                                                </TableCell>
                                                <TableCell className="max-w-[200px] truncate text-muted-foreground">
                                                    {dispute.description}
                                                </TableCell>
                                                <TableCell>
                                                    <Badge className={STATUS_CONFIG[dispute.status as keyof typeof STATUS_CONFIG]?.color || "bg-zinc-500/15"}>
                                                        <StatusIcon className="h-3 w-3 mr-1" />
                                                        {STATUS_CONFIG[dispute.status as keyof typeof STATUS_CONFIG]?.label || dispute.status}
                                                    </Badge>
                                                </TableCell>
                                                <TableCell className="text-xs text-muted-foreground">
                                                    {formatDate(dispute.created_at)}
                                                </TableCell>
                                                <TableCell className="text-right">
                                                    <Button variant="ghost" size="sm" onClick={(e) => { e.stopPropagation(); setSelectedDispute(dispute); }}>
                                                        Review
                                                    </Button>
                                                </TableCell>
                                            </TableRow>
                                        );
                                    })
                                )}
                            </TableBody>
                        </Table>
                    )}
                </CardContent>
            </Card>

            {/* Review Dialog */}
            <Dialog open={!!selectedDispute} onOpenChange={(open) => { if (!open) setSelectedDispute(null); }}>
                <DialogContent className="sm:max-w-lg">
                    <DialogHeader>
                        <DialogTitle className="flex items-center gap-2">
                            <HelpCircle className="h-5 w-5 text-amber-500" />
                            Review Dispute
                        </DialogTitle>
                    </DialogHeader>
                    {selectedDispute && (
                        <div className="space-y-4">
                            <div className="grid grid-cols-2 gap-4">
                                <div>
                                    <Label className="text-xs text-muted-foreground">Ride ID</Label>
                                    <p className="font-mono text-sm">{selectedDispute.ride_id}</p>
                                </div>
                                <div>
                                    <Label className="text-xs text-muted-foreground">User</Label>
                                    <p className="text-sm">{selectedDispute.user_name} ({selectedDispute.user_type})</p>
                                </div>
                            </div>
                            <div>
                                <Label className="text-xs text-muted-foreground">Dispute Type</Label>
                                <p className="text-sm capitalize">{selectedDispute.dispute_type}</p>
                            </div>
                            <div>
                                <Label className="text-xs text-muted-foreground">Description</Label>
                                <div className="rounded-lg bg-muted/50 p-3 text-sm">
                                    {selectedDispute.description}
                                </div>
                            </div>
                            {selectedDispute.resolution && (
                                <div>
                                    <Label className="text-xs text-muted-foreground">Resolution</Label>
                                    <div className="rounded-lg bg-primary/10 p-3 text-sm">
                                        {selectedDispute.resolution}
                                    </div>
                                </div>
                            )}
                            {selectedDispute.status === "pending" && (
                                <>
                                    <div className="space-y-2">
                                        <Label htmlFor="resolution">Resolution Notes</Label>
                                        <Textarea
                                            id="resolution"
                                            placeholder="Enter resolution details..."
                                            value={resolution}
                                            onChange={(e) => setResolution(e.target.value)}
                                            rows={3}
                                        />
                                    </div>
                                    <div className="flex gap-2">
                                        <Button className="flex-1 bg-emerald-600 hover:bg-emerald-700" onClick={handleResolve}>
                                            <CheckCircle className="h-4 w-4 mr-2" /> Resolve
                                        </Button>
                                        <Button variant="destructive" className="flex-1" onClick={handleReject}>
                                            <XCircle className="h-4 w-4 mr-2" /> Reject
                                        </Button>
                                    </div>
                                </>
                            )}
                        </div>
                    )}
                </DialogContent>
            </Dialog>
        </div>
    );
}