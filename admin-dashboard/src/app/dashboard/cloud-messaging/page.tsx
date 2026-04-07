"use client";

import { useEffect, useState, useMemo } from "react";
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
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import {
    Dialog,
    DialogContent,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog";
import {
    Cloud,
    Send,
    Users,
    Car,
    Bell,
    Mail,
    Calendar,
    Clock,
    Download,
    Search,
    CheckCircle2,
    XCircle,
    AlertCircle,
    Timer,
    Trash2,
    Eye,
    RefreshCw,
    FileText,
    User,
} from "lucide-react";
import { formatDate } from "@/lib/utils";
import {
    getCloudMessages,
    sendCloudMessage,
    getCloudMessageStats,
    deleteCloudMessage,
} from "@/lib/api";

interface CloudMessage {
    id: string;
    title: string;
    description: string;
    audience: "customers" | "drivers" | "particular_customer" | "particular_driver";
    particular_id?: string;
    channel: "push" | "email";
    status: "sent" | "scheduled" | "failed" | "pending" | "cancelled";
    scheduled_at?: string;
    sent_at?: string;
    created_at: string;
    total_recipients: number;
    successful: number;
    failed_count: number;
}

interface MessageStats {
    total_messages: number;
    total_sent: number;
    total_scheduled: number;
    total_failed: number;
    total_recipients_reached: number;
    success_rate: number;
}

const AUDIENCE_OPTIONS = [
    { value: "customers", label: "Customers", icon: Users },
    { value: "drivers", label: "Drivers", icon: Car },
    { value: "particular_customer", label: "Particular Customer", icon: User },
    { value: "particular_driver", label: "Particular Driver", icon: User },
];

const CHANNEL_OPTIONS = [
    { value: "push", label: "Push", icon: Bell },
    { value: "email", label: "Email", icon: Mail },
];

const STATUS_CONFIG: Record<string, { label: string; color: string; icon: any }> = {
    sent: { label: "Sent", color: "bg-emerald-500/15 text-emerald-600", icon: CheckCircle2 },
    scheduled: { label: "Scheduled", color: "bg-blue-500/15 text-blue-600", icon: Timer },
    failed: { label: "Failed", color: "bg-red-500/15 text-red-600", icon: XCircle },
    pending: { label: "Pending", color: "bg-amber-500/15 text-amber-600", icon: Clock },
    cancelled: { label: "Cancelled", color: "bg-zinc-500/15 text-zinc-600", icon: XCircle },
};

// Mock data for development
const mockMessages: CloudMessage[] = [
    {
        id: "1",
        title: "Action Required: Update Spinr Now",
        description: "Your current app version is retiring soon. Update now to keep receiving ride requests and keep 100% of your fares!",
        audience: "drivers",
        channel: "push",
        status: "sent",
        sent_at: "2026-04-07T10:30:00Z",
        created_at: "2026-04-07T10:00:00Z",
        total_recipients: 1250,
        successful: 1200,
        failed_count: 50,
    },
    {
        id: "2",
        title: "Weekend Promo: 20% Off Your Next Ride",
        description: "Use code WEEKEND20 to get 20% off your next 3 rides this weekend. Valid until Sunday midnight.",
        audience: "customers",
        channel: "push",
        status: "scheduled",
        scheduled_at: "2026-04-10T08:00:00Z",
        created_at: "2026-04-07T09:00:00Z",
        total_recipients: 5000,
        successful: 0,
        failed_count: 0,
    },
    {
        id: "3",
        title: "New Feature: Schedule Your Rides",
        description: "You can now schedule rides up to 7 days in advance. Try it out today!",
        audience: "customers",
        channel: "email",
        status: "sent",
        sent_at: "2026-04-06T14:00:00Z",
        created_at: "2026-04-06T12:00:00Z",
        total_recipients: 8500,
        successful: 8200,
        failed_count: 300,
    },
    {
        id: "4",
        title: "Earnings Boost: Peak Hours This Week",
        description: "Earn up to 2x more during peak hours this week. Check the app for surge zones near you.",
        audience: "drivers",
        channel: "push",
        status: "failed",
        created_at: "2026-04-05T16:00:00Z",
        total_recipients: 900,
        successful: 0,
        failed_count: 900,
    },
    {
        id: "5",
        title: "Account Verification Required",
        description: "Please verify your account details to continue using Spinr services.",
        audience: "particular_customer",
        particular_id: "user_abc123",
        channel: "email",
        status: "sent",
        sent_at: "2026-04-05T11:00:00Z",
        created_at: "2026-04-05T10:30:00Z",
        total_recipients: 1,
        successful: 1,
        failed_count: 0,
    },
    {
        id: "6",
        title: "Document Expiry Reminder",
        description: "Your driver license is expiring in 7 days. Please upload updated documents to avoid service interruption.",
        audience: "particular_driver",
        particular_id: "driver_xyz789",
        channel: "push",
        status: "scheduled",
        scheduled_at: "2026-04-08T09:00:00Z",
        created_at: "2026-04-07T08:00:00Z",
        total_recipients: 1,
        successful: 0,
        failed_count: 0,
    },
];

const mockStats: MessageStats = {
    total_messages: 6,
    total_sent: 3,
    total_scheduled: 2,
    total_failed: 1,
    total_recipients_reached: 9401,
    success_rate: 95.4,
};

export default function CloudMessagingPage() {
    const [messages, setMessages] = useState<CloudMessage[]>([]);
    const [stats, setStats] = useState<MessageStats>(mockStats);
    const [loading, setLoading] = useState(true);
    const [search, setSearch] = useState("");
    const [statusFilter, setStatusFilter] = useState("all");
    const [audienceFilter, setAudienceFilter] = useState("all");
    const [selectedMessage, setSelectedMessage] = useState<CloudMessage | null>(null);
    const [sending, setSending] = useState(false);

    // Form state
    const [form, setForm] = useState({
        title: "",
        description: "",
        audience: "customers" as string,
        particular_id: "",
        channel: "push" as string,
        is_scheduled: false,
        scheduled_date: "",
        scheduled_time: "",
    });

    useEffect(() => {
        fetchData();
    }, []);

    const fetchData = async () => {
        setLoading(true);
        try {
            const [messagesData, statsData] = await Promise.all([
                getCloudMessages().catch(() => null),
                getCloudMessageStats().catch(() => null),
            ]);
            if (messagesData && Array.isArray(messagesData)) {
                setMessages(messagesData);
            } else {
                setMessages(mockMessages);
            }
            if (statsData) {
                setStats(statsData);
            } else {
                setStats(mockStats);
            }
        } catch {
            setMessages(mockMessages);
            setStats(mockStats);
        } finally {
            setLoading(false);
        }
    };

    const filtered = useMemo(() => {
        return messages.filter((m) => {
            const matchSearch =
                !search ||
                m.title.toLowerCase().includes(search.toLowerCase()) ||
                m.description.toLowerCase().includes(search.toLowerCase());
            const matchStatus = statusFilter === "all" || m.status === statusFilter;
            const matchAudience = audienceFilter === "all" || m.audience === audienceFilter;
            return matchSearch && matchStatus && matchAudience;
        });
    }, [messages, search, statusFilter, audienceFilter]);

    const scheduledMessages = useMemo(() => {
        return messages
            .filter((m) => m.status === "scheduled")
            .sort((a, b) => new Date(a.scheduled_at || "").getTime() - new Date(b.scheduled_at || "").getTime());
    }, [messages]);

    const handleSend = async () => {
        if (!form.title.trim() || !form.description.trim()) {
            alert("Please fill in title and description.");
            return;
        }
        if ((form.audience === "particular_customer" || form.audience === "particular_driver") && !form.particular_id.trim()) {
            alert("Please enter the user/driver ID.");
            return;
        }
        if (form.is_scheduled && (!form.scheduled_date || !form.scheduled_time)) {
            alert("Please select a scheduled date and time.");
            return;
        }

        setSending(true);
        try {
            const payload: any = {
                title: form.title,
                description: form.description,
                audience: form.audience,
                channel: form.channel,
            };
            if (form.particular_id) payload.particular_id = form.particular_id;
            if (form.is_scheduled) {
                payload.scheduled_at = `${form.scheduled_date}T${form.scheduled_time}:00Z`;
            }

            await sendCloudMessage(payload);
            await fetchData();
            resetForm();
        } catch (error: any) {
            alert(`Failed to send message: ${error.message || "Unknown error"}`);
            return;
        } finally {
            setSending(false);
        }
    };

    const resetForm = () => {
        setForm({
            title: "",
            description: "",
            audience: "customers",
            particular_id: "",
            channel: "push",
            is_scheduled: false,
            scheduled_date: "",
            scheduled_time: "",
        });
    };

    const handleDelete = async (id: string) => {
        if (!confirm("Are you sure you want to cancel/delete this message?")) return;
        try {
            await deleteCloudMessage(id);
        } catch {
            // Fallback: remove locally
        }
        setMessages((prev) => prev.filter((m) => m.id !== id));
    };

    const handleExport = () => {
        const headers = [
            "ID",
            "Title",
            "Description",
            "Audience",
            "Channel",
            "Status",
            "Scheduled At",
            "Sent At",
            "Total Recipients",
            "Successful",
            "Failed",
            "Created At",
        ];
        const escapeCSV = (val: string) => `"${String(val || "").replace(/"/g, '""')}"`;
        const rows = filtered.map((m) => [
            m.id,
            escapeCSV(m.title),
            escapeCSV(m.description),
            m.audience,
            m.channel,
            m.status,
            m.scheduled_at ? formatDate(m.scheduled_at) : "",
            m.sent_at ? formatDate(m.sent_at) : "",
            m.total_recipients,
            m.successful,
            m.failed_count,
            formatDate(m.created_at),
        ]);
        const csv = [headers.join(","), ...rows.map((r) => r.join(","))].join("\n");
        const blob = new Blob([csv], { type: "text/csv" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `cloud-messaging-history-${new Date().toISOString().split("T")[0]}.csv`;
        a.click();
        URL.revokeObjectURL(url);
    };

    const handleExportSummary = () => {
        const summaryRows = [
            ["Cloud Messaging Summary Report"],
            [`Generated: ${new Date().toLocaleString()}`],
            [],
            ["Metric", "Value"],
            ["Total Messages", stats.total_messages],
            ["Total Sent", stats.total_sent],
            ["Total Scheduled", stats.total_scheduled],
            ["Total Failed", stats.total_failed],
            ["Total Recipients Reached", stats.total_recipients_reached],
            ["Success Rate", `${stats.success_rate}%`],
            [],
            ["Scheduled Messages"],
            ["Title", "Audience", "Channel", "Scheduled At", "Recipients"],
            ...scheduledMessages.map((m) => [
                `"${m.title}"`,
                m.audience,
                m.channel,
                m.scheduled_at ? formatDate(m.scheduled_at) : "",
                m.total_recipients,
            ]),
        ];
        const csv = summaryRows.map((r) => r.join(",")).join("\n");
        const blob = new Blob([csv], { type: "text/csv" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `cloud-messaging-summary-${new Date().toISOString().split("T")[0]}.csv`;
        a.click();
        URL.revokeObjectURL(url);
    };

    return (
        <div className="space-y-6">
            {/* Header */}
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-3xl font-bold tracking-tight flex items-center gap-2">
                        <Cloud className="h-8 w-8 text-violet-500" />
                        Cloud Messaging
                    </h1>
                    <p className="text-muted-foreground mt-1">
                        Send push notifications and emails to customers and drivers. Schedule and track delivery.
                    </p>
                </div>
                <div className="flex gap-2">
                    <Button variant="outline" size="sm" onClick={fetchData} disabled={loading}>
                        <RefreshCw className={`mr-2 h-4 w-4 ${loading ? "animate-spin" : ""}`} /> Refresh
                    </Button>
                </div>
            </div>

            {/* Summary Stats */}
            <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4">
                <Card>
                    <CardContent className="pt-4 pb-3">
                        <div className="flex items-center gap-2">
                            <Cloud className="h-5 w-5 text-violet-500" />
                            <div>
                                <p className="text-xs text-muted-foreground">Total Messages</p>
                                <p className="text-2xl font-bold">{stats.total_messages}</p>
                            </div>
                        </div>
                    </CardContent>
                </Card>
                <Card>
                    <CardContent className="pt-4 pb-3">
                        <div className="flex items-center gap-2">
                            <CheckCircle2 className="h-5 w-5 text-emerald-500" />
                            <div>
                                <p className="text-xs text-muted-foreground">Sent</p>
                                <p className="text-2xl font-bold">{stats.total_sent}</p>
                            </div>
                        </div>
                    </CardContent>
                </Card>
                <Card>
                    <CardContent className="pt-4 pb-3">
                        <div className="flex items-center gap-2">
                            <Timer className="h-5 w-5 text-blue-500" />
                            <div>
                                <p className="text-xs text-muted-foreground">Scheduled</p>
                                <p className="text-2xl font-bold">{stats.total_scheduled}</p>
                            </div>
                        </div>
                    </CardContent>
                </Card>
                <Card>
                    <CardContent className="pt-4 pb-3">
                        <div className="flex items-center gap-2">
                            <XCircle className="h-5 w-5 text-red-500" />
                            <div>
                                <p className="text-xs text-muted-foreground">Failed</p>
                                <p className="text-2xl font-bold">{stats.total_failed}</p>
                            </div>
                        </div>
                    </CardContent>
                </Card>
                <Card>
                    <CardContent className="pt-4 pb-3">
                        <div className="flex items-center gap-2">
                            <Users className="h-5 w-5 text-amber-500" />
                            <div>
                                <p className="text-xs text-muted-foreground">Recipients Reached</p>
                                <p className="text-2xl font-bold">{stats.total_recipients_reached.toLocaleString()}</p>
                            </div>
                        </div>
                    </CardContent>
                </Card>
                <Card>
                    <CardContent className="pt-4 pb-3">
                        <div className="flex items-center gap-2">
                            <CheckCircle2 className="h-5 w-5 text-emerald-500" />
                            <div>
                                <p className="text-xs text-muted-foreground">Success Rate</p>
                                <p className="text-2xl font-bold">{stats.success_rate}%</p>
                            </div>
                        </div>
                    </CardContent>
                </Card>
            </div>

            {/* Compose Message Section */}
            <Card>
                <CardHeader className="pb-4">
                    <CardTitle className="flex items-center gap-2 text-lg">
                        <Send className="h-5 w-5 text-violet-500" />
                        Compose Message
                    </CardTitle>
                </CardHeader>
                <CardContent className="space-y-5">
                    {/* Select Users */}
                    <div className="space-y-2">
                        <Label className="text-sm font-semibold">
                            Select users : <span className="text-red-500">*</span>
                        </Label>
                        <div className="flex flex-wrap gap-4">
                            {AUDIENCE_OPTIONS.map((opt) => (
                                <label key={opt.value} className="flex items-center gap-2 cursor-pointer">
                                    <input
                                        type="radio"
                                        name="audience"
                                        value={opt.value}
                                        checked={form.audience === opt.value}
                                        onChange={(e) => setForm({ ...form, audience: e.target.value, particular_id: "" })}
                                        className="accent-red-500"
                                    />
                                    <span className="text-sm">{opt.label}</span>
                                </label>
                            ))}
                        </div>
                        {(form.audience === "particular_customer" || form.audience === "particular_driver") && (
                            <div className="mt-2 max-w-sm">
                                <Input
                                    placeholder={`Enter ${form.audience === "particular_customer" ? "customer" : "driver"} ID or email`}
                                    value={form.particular_id}
                                    onChange={(e) => setForm({ ...form, particular_id: e.target.value })}
                                />
                            </div>
                        )}
                    </div>

                    {/* Send notification via */}
                    <div className="space-y-2">
                        <Label className="text-sm font-semibold">
                            Send notification via : <span className="text-red-500">*</span>
                        </Label>
                        <div className="flex gap-4">
                            {CHANNEL_OPTIONS.map((opt) => (
                                <label key={opt.value} className="flex items-center gap-2 cursor-pointer">
                                    <input
                                        type="radio"
                                        name="channel"
                                        value={opt.value}
                                        checked={form.channel === opt.value}
                                        onChange={(e) => setForm({ ...form, channel: e.target.value })}
                                        className="accent-red-500"
                                    />
                                    <span className="text-sm">{opt.label}</span>
                                </label>
                            ))}
                        </div>
                    </div>

                    {/* Title */}
                    <div className="space-y-2">
                        <Label className="text-sm font-semibold">
                            Title <span className="text-red-500">*</span>
                        </Label>
                        <Input
                            placeholder="Enter notification title"
                            value={form.title}
                            onChange={(e) => setForm({ ...form, title: e.target.value })}
                        />
                    </div>

                    {/* Description */}
                    <div className="space-y-2">
                        <Label className="text-sm font-semibold">
                            Description <span className="text-red-500">*</span>
                        </Label>
                        <Textarea
                            placeholder="Enter notification message"
                            value={form.description}
                            onChange={(e) => setForm({ ...form, description: e.target.value })}
                            rows={4}
                        />
                    </div>

                    <Separator />

                    {/* Schedule Option */}
                    <div className="space-y-3">
                        <label className="flex items-center gap-2 cursor-pointer">
                            <input
                                type="checkbox"
                                checked={form.is_scheduled}
                                onChange={(e) => setForm({ ...form, is_scheduled: e.target.checked })}
                                className="accent-red-500 h-4 w-4"
                            />
                            <Calendar className="h-4 w-4 text-muted-foreground" />
                            <span className="text-sm font-semibold">Schedule for later</span>
                        </label>
                        {form.is_scheduled && (
                            <div className="flex gap-4 max-w-md">
                                <div className="flex-1 space-y-1">
                                    <Label className="text-xs text-muted-foreground">Date</Label>
                                    <Input
                                        type="date"
                                        value={form.scheduled_date}
                                        onChange={(e) => setForm({ ...form, scheduled_date: e.target.value })}
                                        min={new Date().toISOString().split("T")[0]}
                                    />
                                </div>
                                <div className="flex-1 space-y-1">
                                    <Label className="text-xs text-muted-foreground">Time</Label>
                                    <Input
                                        type="time"
                                        value={form.scheduled_time}
                                        onChange={(e) => setForm({ ...form, scheduled_time: e.target.value })}
                                    />
                                </div>
                            </div>
                        )}
                    </div>

                    {/* Send Button */}
                    <Button
                        className="bg-red-500 hover:bg-red-600 text-white"
                        onClick={handleSend}
                        disabled={sending}
                    >
                        {sending ? (
                            <div className="h-4 w-4 animate-spin rounded-full border-2 border-white border-t-transparent mr-2" />
                        ) : form.is_scheduled ? (
                            <Calendar className="mr-2 h-4 w-4" />
                        ) : (
                            <Send className="mr-2 h-4 w-4" />
                        )}
                        {form.is_scheduled ? "Schedule Notification" : "Send Notification"}
                    </Button>
                </CardContent>
            </Card>

            {/* Upcoming Scheduled Messages */}
            {scheduledMessages.length > 0 && (
                <Card>
                    <CardHeader className="pb-3">
                        <CardTitle className="flex items-center gap-2 text-lg">
                            <Timer className="h-5 w-5 text-blue-500" />
                            Upcoming Scheduled Messages
                            <Badge variant="secondary" className="ml-2">{scheduledMessages.length}</Badge>
                        </CardTitle>
                    </CardHeader>
                    <CardContent className="p-0">
                        <Table>
                            <TableHeader>
                                <TableRow>
                                    <TableHead>Title</TableHead>
                                    <TableHead>Audience</TableHead>
                                    <TableHead>Channel</TableHead>
                                    <TableHead>Scheduled For</TableHead>
                                    <TableHead>Recipients</TableHead>
                                    <TableHead className="text-right">Actions</TableHead>
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                {scheduledMessages.map((msg) => (
                                    <TableRow key={msg.id}>
                                        <TableCell>
                                            <div>
                                                <p className="font-medium text-sm">{msg.title}</p>
                                                <p className="text-xs text-muted-foreground truncate max-w-[250px]">{msg.description}</p>
                                            </div>
                                        </TableCell>
                                        <TableCell>
                                            <div className="flex items-center gap-1">
                                                {(() => {
                                                    const Icon = AUDIENCE_OPTIONS.find((a) => a.value === msg.audience)?.icon || Users;
                                                    return <Icon className="h-3 w-3 text-muted-foreground" />;
                                                })()}
                                                <span className="text-sm capitalize">{msg.audience.replace(/_/g, " ")}</span>
                                            </div>
                                        </TableCell>
                                        <TableCell>
                                            <Badge variant="outline" className="text-xs capitalize">{msg.channel}</Badge>
                                        </TableCell>
                                        <TableCell>
                                            <div className="flex items-center gap-1 text-sm">
                                                <Calendar className="h-3 w-3 text-blue-500" />
                                                {msg.scheduled_at ? formatDate(msg.scheduled_at) : "-"}
                                            </div>
                                        </TableCell>
                                        <TableCell className="text-sm">{msg.total_recipients.toLocaleString()}</TableCell>
                                        <TableCell className="text-right">
                                            <div className="flex justify-end gap-1">
                                                <Button
                                                    variant="ghost"
                                                    size="icon"
                                                    className="h-8 w-8"
                                                    onClick={() => setSelectedMessage(msg)}
                                                >
                                                    <Eye className="h-4 w-4" />
                                                </Button>
                                                <Button
                                                    variant="ghost"
                                                    size="icon"
                                                    className="h-8 w-8 text-destructive"
                                                    onClick={() => handleDelete(msg.id)}
                                                >
                                                    <Trash2 className="h-4 w-4" />
                                                </Button>
                                            </div>
                                        </TableCell>
                                    </TableRow>
                                ))}
                            </TableBody>
                        </Table>
                    </CardContent>
                </Card>
            )}

            {/* Message History & Report */}
            <Card>
                <CardHeader className="pb-3">
                    <div className="flex items-center justify-between">
                        <CardTitle className="flex items-center gap-2 text-lg">
                            <FileText className="h-5 w-5 text-violet-500" />
                            Message History & Report
                        </CardTitle>
                        <div className="flex gap-2">
                            <Button variant="outline" size="sm" onClick={handleExportSummary}>
                                <Download className="mr-2 h-4 w-4" /> Export Summary
                            </Button>
                            <Button variant="outline" size="sm" onClick={handleExport} disabled={filtered.length === 0}>
                                <Download className="mr-2 h-4 w-4" /> Export History
                            </Button>
                        </div>
                    </div>
                </CardHeader>
                <CardContent className="space-y-4">
                    {/* Filters */}
                    <div className="flex flex-wrap items-center gap-3">
                        <div className="relative flex-1 max-w-sm">
                            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                            <Input
                                placeholder="Search messages..."
                                value={search}
                                onChange={(e) => setSearch(e.target.value)}
                                className="pl-9"
                            />
                        </div>
                        <Select value={statusFilter} onValueChange={setStatusFilter}>
                            <SelectTrigger className="w-40">
                                <SelectValue placeholder="Status" />
                            </SelectTrigger>
                            <SelectContent>
                                <SelectItem value="all">All Status</SelectItem>
                                <SelectItem value="sent">Sent</SelectItem>
                                <SelectItem value="scheduled">Scheduled</SelectItem>
                                <SelectItem value="failed">Failed</SelectItem>
                                <SelectItem value="pending">Pending</SelectItem>
                                <SelectItem value="cancelled">Cancelled</SelectItem>
                            </SelectContent>
                        </Select>
                        <Select value={audienceFilter} onValueChange={setAudienceFilter}>
                            <SelectTrigger className="w-40">
                                <SelectValue placeholder="Audience" />
                            </SelectTrigger>
                            <SelectContent>
                                <SelectItem value="all">All Audiences</SelectItem>
                                {AUDIENCE_OPTIONS.map((a) => (
                                    <SelectItem key={a.value} value={a.value}>{a.label}</SelectItem>
                                ))}
                            </SelectContent>
                        </Select>
                    </div>

                    {/* Table */}
                    {loading ? (
                        <div className="flex items-center justify-center p-12">
                            <div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" />
                        </div>
                    ) : (
                        <div className="border rounded-lg">
                            <Table>
                                <TableHeader>
                                    <TableRow>
                                        <TableHead>Title</TableHead>
                                        <TableHead>Audience</TableHead>
                                        <TableHead>Channel</TableHead>
                                        <TableHead>Status</TableHead>
                                        <TableHead>Recipients</TableHead>
                                        <TableHead>Successful</TableHead>
                                        <TableHead>Failed</TableHead>
                                        <TableHead>Date</TableHead>
                                        <TableHead className="text-right">Actions</TableHead>
                                    </TableRow>
                                </TableHeader>
                                <TableBody>
                                    {filtered.length === 0 ? (
                                        <TableRow>
                                            <TableCell colSpan={9} className="text-center text-muted-foreground py-12">
                                                No messages found.
                                            </TableCell>
                                        </TableRow>
                                    ) : (
                                        filtered.map((msg) => {
                                            const statusCfg = STATUS_CONFIG[msg.status] || STATUS_CONFIG.pending;
                                            const StatusIcon = statusCfg.icon;
                                            return (
                                                <TableRow
                                                    key={msg.id}
                                                    className="cursor-pointer hover:bg-muted/50"
                                                    onClick={() => setSelectedMessage(msg)}
                                                >
                                                    <TableCell>
                                                        <div>
                                                            <p className="font-medium text-sm">{msg.title}</p>
                                                            <p className="text-xs text-muted-foreground truncate max-w-[250px]">
                                                                {msg.description}
                                                            </p>
                                                        </div>
                                                    </TableCell>
                                                    <TableCell>
                                                        <span className="text-sm capitalize">{msg.audience.replace(/_/g, " ")}</span>
                                                    </TableCell>
                                                    <TableCell>
                                                        <Badge variant="outline" className="text-xs capitalize">{msg.channel}</Badge>
                                                    </TableCell>
                                                    <TableCell>
                                                        <Badge className={statusCfg.color}>
                                                            <StatusIcon className="h-3 w-3 mr-1" />
                                                            {statusCfg.label}
                                                        </Badge>
                                                    </TableCell>
                                                    <TableCell className="text-sm">{msg.total_recipients.toLocaleString()}</TableCell>
                                                    <TableCell>
                                                        <span className="text-sm text-emerald-600 font-medium">{msg.successful.toLocaleString()}</span>
                                                    </TableCell>
                                                    <TableCell>
                                                        <span className={`text-sm font-medium ${msg.failed_count > 0 ? "text-red-500" : "text-muted-foreground"}`}>
                                                            {msg.failed_count.toLocaleString()}
                                                        </span>
                                                    </TableCell>
                                                    <TableCell className="text-xs text-muted-foreground">
                                                        {msg.sent_at ? formatDate(msg.sent_at) : msg.scheduled_at ? formatDate(msg.scheduled_at) : formatDate(msg.created_at)}
                                                    </TableCell>
                                                    <TableCell className="text-right">
                                                        <div className="flex justify-end gap-1">
                                                            <Button
                                                                variant="ghost"
                                                                size="icon"
                                                                className="h-8 w-8"
                                                                onClick={(e) => { e.stopPropagation(); setSelectedMessage(msg); }}
                                                            >
                                                                <Eye className="h-4 w-4" />
                                                            </Button>
                                                            {(msg.status === "scheduled" || msg.status === "pending") && (
                                                                <Button
                                                                    variant="ghost"
                                                                    size="icon"
                                                                    className="h-8 w-8 text-destructive"
                                                                    onClick={(e) => { e.stopPropagation(); handleDelete(msg.id); }}
                                                                >
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
                        </div>
                    )}
                </CardContent>
            </Card>

            {/* View Message Detail Dialog */}
            <Dialog open={!!selectedMessage} onOpenChange={(open) => { if (!open) setSelectedMessage(null); }}>
                <DialogContent className="sm:max-w-lg">
                    <DialogHeader>
                        <DialogTitle className="flex items-center gap-2">
                            <Cloud className="h-5 w-5 text-violet-500" />
                            Message Details
                        </DialogTitle>
                    </DialogHeader>
                    {selectedMessage && (
                        <div className="space-y-4">
                            <div>
                                <Label className="text-xs text-muted-foreground">Title</Label>
                                <p className="font-semibold">{selectedMessage.title}</p>
                            </div>
                            <div>
                                <Label className="text-xs text-muted-foreground">Description</Label>
                                <div className="rounded-lg bg-muted/50 p-3 text-sm mt-1">
                                    {selectedMessage.description}
                                </div>
                            </div>
                            <div className="grid grid-cols-2 gap-4">
                                <div>
                                    <Label className="text-xs text-muted-foreground">Audience</Label>
                                    <p className="text-sm capitalize">{selectedMessage.audience.replace(/_/g, " ")}</p>
                                </div>
                                <div>
                                    <Label className="text-xs text-muted-foreground">Channel</Label>
                                    <p className="text-sm capitalize">{selectedMessage.channel}</p>
                                </div>
                            </div>
                            {selectedMessage.particular_id && (
                                <div>
                                    <Label className="text-xs text-muted-foreground">Target ID</Label>
                                    <p className="text-sm font-mono">{selectedMessage.particular_id}</p>
                                </div>
                            )}
                            <div>
                                <Label className="text-xs text-muted-foreground">Status</Label>
                                <div className="mt-1">
                                    <Badge className={STATUS_CONFIG[selectedMessage.status]?.color || "bg-zinc-500/15"}>
                                        {STATUS_CONFIG[selectedMessage.status]?.label || selectedMessage.status}
                                    </Badge>
                                </div>
                            </div>
                            <Separator />
                            <div>
                                <Label className="text-xs text-muted-foreground mb-2 block">Delivery Report</Label>
                                <div className="grid grid-cols-3 gap-3">
                                    <div className="rounded-lg bg-muted/50 p-3 text-center">
                                        <p className="text-lg font-bold">{selectedMessage.total_recipients.toLocaleString()}</p>
                                        <p className="text-xs text-muted-foreground">Total Recipients</p>
                                    </div>
                                    <div className="rounded-lg bg-emerald-50 dark:bg-emerald-900/20 p-3 text-center">
                                        <p className="text-lg font-bold text-emerald-600">{selectedMessage.successful.toLocaleString()}</p>
                                        <p className="text-xs text-muted-foreground">Successful</p>
                                    </div>
                                    <div className="rounded-lg bg-red-50 dark:bg-red-900/20 p-3 text-center">
                                        <p className="text-lg font-bold text-red-500">{selectedMessage.failed_count.toLocaleString()}</p>
                                        <p className="text-xs text-muted-foreground">Failed</p>
                                    </div>
                                </div>
                                {selectedMessage.total_recipients > 0 && selectedMessage.status === "sent" && (
                                    <div className="mt-3">
                                        <div className="flex justify-between text-xs text-muted-foreground mb-1">
                                            <span>Success Rate</span>
                                            <span>
                                                {((selectedMessage.successful / selectedMessage.total_recipients) * 100).toFixed(1)}%
                                            </span>
                                        </div>
                                        <div className="h-2 rounded-full bg-muted overflow-hidden">
                                            <div
                                                className="h-full rounded-full bg-emerald-500 transition-all"
                                                style={{ width: `${(selectedMessage.successful / selectedMessage.total_recipients) * 100}%` }}
                                            />
                                        </div>
                                    </div>
                                )}
                            </div>
                            <div className="grid grid-cols-2 gap-4 text-sm">
                                {selectedMessage.scheduled_at && (
                                    <div>
                                        <Label className="text-xs text-muted-foreground">Scheduled For</Label>
                                        <p>{formatDate(selectedMessage.scheduled_at)}</p>
                                    </div>
                                )}
                                {selectedMessage.sent_at && (
                                    <div>
                                        <Label className="text-xs text-muted-foreground">Sent At</Label>
                                        <p>{formatDate(selectedMessage.sent_at)}</p>
                                    </div>
                                )}
                                <div>
                                    <Label className="text-xs text-muted-foreground">Created At</Label>
                                    <p>{formatDate(selectedMessage.created_at)}</p>
                                </div>
                            </div>
                        </div>
                    )}
                </DialogContent>
            </Dialog>
        </div>
    );
}
