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
import { Switch } from "@/components/ui/switch";
import { Separator } from "@/components/ui/separator";
import { Bell, Search, Send, Users, Car, MapPin, AlertCircle, Info, Calendar, Clock, Download, Trash2, Mail, Phone } from "lucide-react";
import { formatDate } from "@/lib/utils";
import { getNotifications, sendNotification } from "@/lib/api";

// Mock notification data - replace with API calls when backend is ready
const mockNotifications = [
    {
        id: "1",
        title: "Surge Pricing Alert",
        message: "High demand detected in downtown area. Surge pricing is now active.",
        type: "surge",
        audience: "drivers",
        status: "sent",
        created_at: "2024-01-15T10:30:00Z",
        sent_count: 150,
    },
    {
        id: "2",
        title: "Weather Advisory",
        message: "Heavy snowfall expected tonight. Please drive safely and allow extra time for trips.",
        type: "alert",
        audience: "all",
        status: "sent",
        created_at: "2024-01-14T08:00:00Z",
        sent_count: 500,
    },
    {
        id: "3",
        title: "New Feature Announcement",
        message: "We've added scheduled rides! Book your rides in advance up to 7 days.",
        type: "info",
        audience: "riders",
        status: "draft",
        created_at: "2024-01-13T14:00:00Z",
        sent_count: 0,
    },
];

const NOTIFICATION_TYPES = [
    { value: "info", label: "Information", icon: Info, color: "text-blue-500" },
    { value: "alert", label: "Alert", icon: AlertCircle, color: "text-amber-500" },
    { value: "surge", label: "Surge Pricing", icon: MapPin, color: "text-purple-500" },
    { value: "promotion", label: "Promotion", icon: Bell, color: "text-pink-500" },
    { value: "system", label: "System", icon: Clock, color: "text-gray-500" },
];

const AUDIENCE_OPTIONS = [
    { value: "all", label: "All Users", icon: Users },
    { value: "riders", label: "Riders Only", icon: Users },
    { value: "drivers", label: "Drivers Only", icon: Car },
];

const STATUS_CONFIG: Record<string, { label: string; color: string }> = {
    sent: { label: "Sent", color: "bg-emerald-500/15 text-emerald-600" },
    draft: { label: "Draft", color: "bg-zinc-500/15 text-zinc-600" },
    scheduled: { label: "Scheduled", color: "bg-blue-500/15 text-blue-600" },
};

export default function NotificationsPage() {
    const [notifications, setNotifications] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [search, setSearch] = useState("");
    const [typeFilter, setTypeFilter] = useState("all");
    const [statusFilter, setStatusFilter] = useState("all");
    const [createDialogOpen, setCreateDialogOpen] = useState(false);
    const [selectedNotification, setSelectedNotification] = useState<any>(null);
    const [notifStats, setNotifStats] = useState({ total: 0, sent: 0, drafts: 0, reach: 0 });

    const [form, setForm] = useState({
        title: "",
        message: "",
        type: "info",
        audience: "all",
        send_push: true,
        send_email: false,
        send_sms: false,
    });

    useEffect(() => {
        fetchNotifications();
    }, []);

    const fetchNotifications = async () => {
        setLoading(true);
        try {
            const data = await getNotifications();
            const transformed = (data || []).map((n: any) => ({
                id: n.id,
                title: n.title,
                message: n.body,
                type: n.type,
                audience: n.audience || 'user',
                status: n.status,
                created_at: n.created_at || n.sent_at,
                sent_count: n.sent_count || 0,
            }));
            setNotifications(transformed);
            setNotifStats({
                total: transformed.length,
                sent: transformed.filter((n: any) => n.status === 'sent').length,
                drafts: transformed.filter((n: any) => n.status === 'draft').length,
                reach: transformed.reduce((s: number, n: any) => s + (n.sent_count || 0), 0),
            });
        } catch (error) {
            console.error('Failed to fetch notifications:', error);
            setNotifications(mockNotifications);
        } finally {
            setLoading(false);
        }
    };

    const filtered = notifications.filter((n) => {
        const matchSearch =
            !search ||
            n.title?.toLowerCase().includes(search.toLowerCase()) ||
            n.message?.toLowerCase().includes(search.toLowerCase());
        const matchType = typeFilter === "all" || n.type === typeFilter;
        const matchStatus = statusFilter === "all" || n.status === statusFilter;
        return matchSearch && matchType && matchStatus;
    });

    const handleCreate = async () => {
        if (!form.title.trim() || !form.message.trim()) {
            alert("Please fill in title and message");
            return;
        }

        try {
            await sendNotification({
                title: form.title,
                body: form.message,
                type: form.type,
                audience: form.audience,
            });
            await fetchNotifications();
            setCreateDialogOpen(false);
            setForm({
                title: "",
                message: "",
                type: "info",
                audience: "all",
                send_push: true,
                send_email: false,
                send_sms: false,
            });
        } catch (error: any) {
            alert(`Failed to send notification: ${error.message}`);
        }
    };

    const handleDelete = (id: string) => {
        if (!confirm("Delete this notification?")) return;
        setNotifications(notifications.filter(n => n.id !== id));
    };

    const handleExport = () => {
        const headers = ["ID", "Title", "Type", "Audience", "Status", "Sent Count", "Created"];
        const rows = filtered.map(n => [
            n.id,
            n.title,
            n.type,
            n.audience,
            n.status,
            n.sent_count || 0,
            formatDate(n.created_at),
        ]);
        const csv = [headers, ...rows].map(row => row.join(",")).join("\n");
        const blob = new Blob([csv], { type: "text/csv" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `notifications-${new Date().toISOString().split("T")[0]}.csv`;
        a.click();
        URL.revokeObjectURL(url);
    };

    return (
        <div className="space-y-6">
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-3xl font-bold tracking-tight flex items-center gap-2">
                        <Bell className="h-8 w-8 text-violet-500" />
                        Notifications
                    </h1>
                    <p className="text-muted-foreground mt-1">
                        Send and manage push notifications, emails, and SMS messages.
                    </p>
                </div>
                <div className="flex gap-2">
                    <Button variant="outline" onClick={handleExport} disabled={filtered.length === 0}>
                        <Download className="mr-2 h-4 w-4" /> Export
                    </Button>
                    <Button onClick={() => setCreateDialogOpen(true)}>
                        <Send className="mr-2 h-4 w-4" /> Create Notification
                    </Button>
                </div>
            </div>

            {/* Stats */}
            <div className="grid grid-cols-4 gap-4">
                <Card>
                    <CardContent className="pt-4">
                        <div className="flex items-center gap-2">
                            <Bell className="h-5 w-5 text-violet-500" />
                            <div>
                                <p className="text-xs text-muted-foreground">Total</p>
                                <p className="text-2xl font-bold">{notifStats.total}</p>
                            </div>
                        </div>
                    </CardContent>
                </Card>
                <Card>
                    <CardContent className="pt-4">
                        <div className="flex items-center gap-2">
                            <Info className="h-5 w-5 text-emerald-500" />
                            <div>
                                <p className="text-xs text-muted-foreground">Sent</p>
                                <p className="text-2xl font-bold">{notifStats.sent}</p>
                            </div>
                        </div>
                    </CardContent>
                </Card>
                <Card>
                    <CardContent className="pt-4">
                        <div className="flex items-center gap-2">
                            <Clock className="h-5 w-5 text-blue-500" />
                            <div>
                                <p className="text-xs text-muted-foreground">Drafts</p>
                                <p className="text-2xl font-bold">{notifStats.drafts}</p>
                            </div>
                        </div>
                    </CardContent>
                </Card>
                <Card>
                    <CardContent className="pt-4">
                        <div className="flex items-center gap-2">
                            <Users className="h-5 w-5 text-amber-500" />
                            <div>
                                <p className="text-xs text-muted-foreground">Total Reach</p>
                                <p className="text-2xl font-bold">{notifStats.reach.toLocaleString()}</p>
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
                        placeholder="Search notifications..."
                        value={search}
                        onChange={(e) => setSearch(e.target.value)}
                        className="pl-9"
                    />
                </div>
                <Select value={typeFilter} onValueChange={setTypeFilter}>
                    <SelectTrigger className="w-40">
                        <SelectValue placeholder="Filter by type" />
                    </SelectTrigger>
                    <SelectContent>
                        <SelectItem value="all">All Types</SelectItem>
                        {NOTIFICATION_TYPES.map((t) => (
                            <SelectItem key={t.value} value={t.value}>{t.label}</SelectItem>
                        ))}
                    </SelectContent>
                </Select>
                <Select value={statusFilter} onValueChange={setStatusFilter}>
                    <SelectTrigger className="w-40">
                        <SelectValue placeholder="Filter by status" />
                    </SelectTrigger>
                    <SelectContent>
                        <SelectItem value="all">All Status</SelectItem>
                        <SelectItem value="sent">Sent</SelectItem>
                        <SelectItem value="draft">Draft</SelectItem>
                        <SelectItem value="scheduled">Scheduled</SelectItem>
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
                        <Table>
                            <TableHeader>
                                <TableRow>
                                    <TableHead>Title</TableHead>
                                    <TableHead>Type</TableHead>
                                    <TableHead>Audience</TableHead>
                                    <TableHead>Status</TableHead>
                                    <TableHead>Reach</TableHead>
                                    <TableHead>Date</TableHead>
                                    <TableHead className="text-right">Actions</TableHead>
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                {filtered.length === 0 ? (
                                    <TableRow>
                                        <TableCell colSpan={7} className="text-center text-muted-foreground py-12">
                                            No notifications found.
                                        </TableCell>
                                    </TableRow>
                                ) : (
                                    filtered.map((notification) => {
                                        const TypeIcon = NOTIFICATION_TYPES.find(t => t.value === notification.type)?.icon || Info;
                                        const AudienceIcon = AUDIENCE_OPTIONS.find(a => a.value === notification.audience)?.icon || Users;
                                        return (
                                            <TableRow key={notification.id} className="cursor-pointer hover:bg-muted/50" onClick={() => setSelectedNotification(notification)}>
                                                <TableCell>
                                                    <div>
                                                        <p className="font-medium">{notification.title}</p>
                                                        <p className="text-xs text-muted-foreground max-w-[300px] truncate">{notification.message}</p>
                                                    </div>
                                                </TableCell>
                                                <TableCell>
                                                    <div className="flex items-center gap-1">
                                                        <TypeIcon className={`h-3 w-3 ${NOTIFICATION_TYPES.find(t => t.value === notification.type)?.color}`} />
                                                        <span className="text-sm capitalize">{notification.type}</span>
                                                    </div>
                                                </TableCell>
                                                <TableCell>
                                                    <div className="flex items-center gap-1">
                                                        <AudienceIcon className="h-3 w-3 text-muted-foreground" />
                                                        <span className="text-sm capitalize">{notification.audience}</span>
                                                    </div>
                                                </TableCell>
                                                <TableCell>
                                                    <Badge className={STATUS_CONFIG[notification.status]?.color || "bg-zinc-500/15"}>
                                                        {STATUS_CONFIG[notification.status]?.label || notification.status}
                                                    </Badge>
                                                </TableCell>
                                                <TableCell>
                                                    <span className="text-sm text-muted-foreground">{notification.sent_count?.toLocaleString() || 0}</span>
                                                </TableCell>
                                                <TableCell className="text-xs text-muted-foreground">
                                                    {formatDate(notification.created_at)}
                                                </TableCell>
                                                <TableCell className="text-right">
                                                    <div className="flex justify-end gap-1">
                                                        <Button variant="ghost" size="sm" onClick={(e) => { e.stopPropagation(); setSelectedNotification(notification); }}>
                                                            View
                                                        </Button>
                                                        {notification.status === "draft" && (
                                                            <Button variant="ghost" size="icon" className="text-destructive" onClick={(e) => { e.stopPropagation(); handleDelete(notification.id); }}>
                                                                <Trash2 className="h-4 w-4" />
                                                            </Button>
                                                        )}
                                                    </div>
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

            {/* Create Notification Dialog */}
            <Dialog open={createDialogOpen} onOpenChange={setCreateDialogOpen}>
                <DialogContent className="sm:max-w-lg">
                    <DialogHeader>
                        <DialogTitle className="flex items-center gap-2">
                            <Bell className="h-5 w-5 text-violet-500" />
                            Create Notification
                        </DialogTitle>
                    </DialogHeader>
                    <div className="space-y-4">
                        <div className="space-y-2">
                            <Label>Title</Label>
                            <Input
                                placeholder="Enter notification title"
                                value={form.title}
                                onChange={(e) => setForm({ ...form, title: e.target.value })}
                            />
                        </div>
                        <div className="space-y-2">
                            <Label>Message</Label>
                            <Textarea
                                placeholder="Enter notification message"
                                value={form.message}
                                onChange={(e) => setForm({ ...form, message: e.target.value })}
                                rows={4}
                            />
                        </div>
                        <div className="grid grid-cols-2 gap-4">
                            <div className="space-y-2">
                                <Label>Type</Label>
                                <Select value={form.type} onValueChange={(v) => setForm({ ...form, type: v })}>
                                    <SelectTrigger><SelectValue /></SelectTrigger>
                                    <SelectContent>
                                        {NOTIFICATION_TYPES.map((t) => (
                                            <SelectItem key={t.value} value={t.value}>{t.label}</SelectItem>
                                        ))}
                                    </SelectContent>
                                </Select>
                            </div>
                            <div className="space-y-2">
                                <Label>Audience</Label>
                                <Select value={form.audience} onValueChange={(v) => setForm({ ...form, audience: v })}>
                                    <SelectTrigger><SelectValue /></SelectTrigger>
                                    <SelectContent>
                                        {AUDIENCE_OPTIONS.map((a) => (
                                            <SelectItem key={a.value} value={a.value}>{a.label}</SelectItem>
                                        ))}
                                    </SelectContent>
                                </Select>
                            </div>
                        </div>
                        <Separator />
                        <div className="space-y-3">
                            <Label>Delivery Methods</Label>
                            <div className="flex items-center justify-between">
                                <div className="flex items-center gap-2">
                                    <Bell className="h-4 w-4 text-muted-foreground" />
                                    <Label htmlFor="push" className="cursor-pointer">Push Notification</Label>
                                </div>
                                <Switch
                                    id="push"
                                    checked={form.send_push}
                                    onCheckedChange={(v) => setForm({ ...form, send_push: v })}
                                />
                            </div>
                            <div className="flex items-center justify-between">
                                <div className="flex items-center gap-2">
                                    <Mail className="h-4 w-4 text-muted-foreground" />
                                    <Label htmlFor="email" className="cursor-pointer">Email</Label>
                                </div>
                                <Switch
                                    id="email"
                                    checked={form.send_email}
                                    onCheckedChange={(v) => setForm({ ...form, send_email: v })}
                                />
                            </div>
                            <div className="flex items-center justify-between">
                                <div className="flex items-center gap-2">
                                    <Phone className="h-4 w-4 text-muted-foreground" />
                                    <Label htmlFor="sms" className="cursor-pointer">SMS</Label>
                                </div>
                                <Switch
                                    id="sms"
                                    checked={form.send_sms}
                                    onCheckedChange={(v) => setForm({ ...form, send_sms: v })}
                                />
                            </div>
                        </div>
                        <Button className="w-full" onClick={handleCreate}>
                            <Send className="mr-2 h-4 w-4" /> Send Notification
                        </Button>
                    </div>
                </DialogContent>
            </Dialog>

            {/* View Notification Dialog */}
            <Dialog open={!!selectedNotification} onOpenChange={(open) => { if (!open) setSelectedNotification(null); }}>
                <DialogContent className="sm:max-w-lg">
                    <DialogHeader>
                        <DialogTitle className="flex items-center gap-2">
                            {selectedNotification && (
                                <>
                                    {(() => {
                                        const TypeIcon = NOTIFICATION_TYPES.find(t => t.value === selectedNotification.type)?.icon || Info;
                                        return <TypeIcon className="h-5 w-5" />;
                                    })()}
                                    {selectedNotification.title}
                                </>
                            )}
                        </DialogTitle>
                    </DialogHeader>
                    {selectedNotification && (
                        <div className="space-y-4">
                            <div className="grid grid-cols-2 gap-4">
                                <div>
                                    <Label className="text-xs text-muted-foreground">Type</Label>
                                    <p className="text-sm capitalize">{selectedNotification.type}</p>
                                </div>
                                <div>
                                    <Label className="text-xs text-muted-foreground">Audience</Label>
                                    <p className="text-sm capitalize">{selectedNotification.audience}</p>
                                </div>
                            </div>
                            <div>
                                <Label className="text-xs text-muted-foreground">Status</Label>
                                <div className="mt-1">
                                    <Badge className={STATUS_CONFIG[selectedNotification.status]?.color || "bg-zinc-500/15"}>
                                        {STATUS_CONFIG[selectedNotification.status]?.label || selectedNotification.status}
                                    </Badge>
                                </div>
                            </div>
                            <div>
                                <Label className="text-xs text-muted-foreground">Message</Label>
                                <div className="rounded-lg bg-muted/50 p-3 text-sm">
                                    {selectedNotification.message}
                                </div>
                            </div>
                            <div className="grid grid-cols-2 gap-4">
                                <div>
                                    <Label className="text-xs text-muted-foreground">Sent Count</Label>
                                    <p className="text-lg font-semibold">{selectedNotification.sent_count?.toLocaleString() || 0}</p>
                                </div>
                                <div>
                                    <Label className="text-xs text-muted-foreground">Created</Label>
                                    <p className="text-sm">{formatDate(selectedNotification.created_at)}</p>
                                </div>
                            </div>
                        </div>
                    )}
                </DialogContent>
            </Dialog>
        </div>
    );
}

