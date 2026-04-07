"use client";

import { useEffect, useState, useMemo, useCallback } from "react";
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
    Timer,
    Trash2,
    Eye,
    RefreshCw,
    FileText,
    User,
    Phone,
    ChevronLeft,
    ChevronRight,
} from "lucide-react";
import { formatDate } from "@/lib/utils";
import {
    getCloudMessages,
    sendCloudMessage,
    getCloudMessageStats,
    deleteCloudMessage,
    getUsers,
    getDrivers,
} from "@/lib/api";

interface CloudMessage {
    id: string;
    title: string;
    description: string;
    audience: "customers" | "drivers" | "particular_customer" | "particular_driver";
    particular_id?: string;
    channel: "push" | "email" | "sms";
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

interface UserOption {
    id: string;
    label: string;
    email?: string;
    phone?: string;
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
    { value: "sms", label: "SMS", icon: Phone },
];

const STATUS_CONFIG: Record<string, { label: string; color: string; icon: any }> = {
    sent: { label: "Sent", color: "bg-emerald-500/15 text-emerald-600", icon: CheckCircle2 },
    scheduled: { label: "Scheduled", color: "bg-blue-500/15 text-blue-600", icon: Timer },
    failed: { label: "Failed", color: "bg-red-500/15 text-red-600", icon: XCircle },
    pending: { label: "Pending", color: "bg-amber-500/15 text-amber-600", icon: Clock },
    cancelled: { label: "Cancelled", color: "bg-zinc-500/15 text-zinc-600", icon: XCircle },
};

const PER_PAGE = 20;

const emptyStats: MessageStats = {
    total_messages: 0,
    total_sent: 0,
    total_scheduled: 0,
    total_failed: 0,
    total_recipients_reached: 0,
    success_rate: 0,
};

export default function CloudMessagingPage() {
    const [messages, setMessages] = useState<CloudMessage[]>([]);
    const [stats, setStats] = useState<MessageStats>(emptyStats);
    const [loading, setLoading] = useState(true);
    const [search, setSearch] = useState("");
    const [statusFilter, setStatusFilter] = useState("all");
    const [audienceFilter, setAudienceFilter] = useState("all");
    const [dateFrom, setDateFrom] = useState("");
    const [dateTo, setDateTo] = useState("");
    const [historyPage, setHistoryPage] = useState(1);
    const [selectedMessage, setSelectedMessage] = useState<CloudMessage | null>(null);
    const [sending, setSending] = useState(false);
    const [activeTab, setActiveTab] = useState<"compose" | "scheduled" | "history">("compose");

    // User/driver search for particular targeting
    const [userOptions, setUserOptions] = useState<UserOption[]>([]);
    const [userSearch, setUserSearch] = useState("");
    const [userSearchLoading, setUserSearchLoading] = useState(false);

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
                setMessages([]);
            }
            if (statsData) {
                setStats(statsData);
            } else {
                setStats(emptyStats);
            }
        } catch {
            setMessages([]);
            setStats(emptyStats);
        } finally {
            setLoading(false);
        }
    };

    // Fetch users/drivers for the searchable dropdown
    const fetchUserOptions = useCallback(async (type: "customer" | "driver", query: string) => {
        setUserSearchLoading(true);
        try {
            const data = type === "customer" ? await getUsers() : await getDrivers();
            const options: UserOption[] = (data || []).map((u: any) => ({
                id: u.id,
                label: `${u.first_name || ""} ${u.last_name || ""}`.trim() || u.email || u.phone || u.id,
                email: u.email,
                phone: u.phone,
            }));
            // Filter by search query
            if (query) {
                const q = query.toLowerCase();
                setUserOptions(options.filter((o) =>
                    o.label.toLowerCase().includes(q) ||
                    o.email?.toLowerCase().includes(q) ||
                    o.phone?.includes(q) ||
                    o.id.toLowerCase().includes(q)
                ));
            } else {
                setUserOptions(options.slice(0, 50));
            }
        } catch {
            setUserOptions([]);
        } finally {
            setUserSearchLoading(false);
        }
    }, []);

    // Debounced user search
    useEffect(() => {
        if (form.audience !== "particular_customer" && form.audience !== "particular_driver") {
            setUserOptions([]);
            return;
        }
        const type = form.audience === "particular_customer" ? "customer" : "driver";
        const timer = setTimeout(() => {
            fetchUserOptions(type, userSearch);
        }, 300);
        return () => clearTimeout(timer);
    }, [form.audience, userSearch, fetchUserOptions]);

    const filtered = useMemo(() => {
        return messages.filter((m) => {
            const matchSearch =
                !search ||
                m.title.toLowerCase().includes(search.toLowerCase()) ||
                m.description.toLowerCase().includes(search.toLowerCase());
            const matchStatus = statusFilter === "all" || m.status === statusFilter;
            const matchAudience = audienceFilter === "all" || m.audience === audienceFilter;

            // Date range filter
            const msgDate = m.sent_at || m.scheduled_at || m.created_at;
            let matchDate = true;
            if (dateFrom && msgDate) {
                matchDate = matchDate && msgDate >= dateFrom;
            }
            if (dateTo && msgDate) {
                matchDate = matchDate && msgDate <= dateTo + "T23:59:59Z";
            }

            return matchSearch && matchStatus && matchAudience && matchDate;
        });
    }, [messages, search, statusFilter, audienceFilter, dateFrom, dateTo]);

    const scheduledMessages = useMemo(() => {
        return messages
            .filter((m) => m.status === "scheduled")
            .sort((a, b) => new Date(a.scheduled_at || "").getTime() - new Date(b.scheduled_at || "").getTime());
    }, [messages]);

    const totalHistoryPages = Math.max(1, Math.ceil(filtered.length / PER_PAGE));
    const paginatedHistory = filtered.slice((historyPage - 1) * PER_PAGE, historyPage * PER_PAGE);

    useEffect(() => { setHistoryPage(1); }, [search, statusFilter, audienceFilter, dateFrom, dateTo]);

    const handleSend = async () => {
        if (!form.title.trim() || !form.description.trim()) {
            alert("Please fill in title and description.");
            return;
        }
        if ((form.audience === "particular_customer" || form.audience === "particular_driver") && !form.particular_id.trim()) {
            alert("Please select a user/driver.");
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
        setUserSearch("");
        setUserOptions([]);
    };

    const handleDelete = async (id: string) => {
        if (!confirm("Are you sure you want to cancel this message?")) return;
        try {
            await deleteCloudMessage(id);
        } catch {
            // continue
        }
        setMessages((prev) => prev.filter((m) => m.id !== id));
    };

    const handleExport = () => {
        const headers = [
            "ID", "Title", "Description", "Audience", "Channel", "Status",
            "Scheduled At", "Sent At", "Total Recipients", "Successful", "Failed", "Created At",
        ];
        const escapeCSV = (val: string) => `"${String(val || "").replace(/"/g, '""')}"`;
        const rows = filtered.map((m) => [
            m.id, escapeCSV(m.title), escapeCSV(m.description), m.audience, m.channel, m.status,
            m.scheduled_at ? formatDate(m.scheduled_at) : "", m.sent_at ? formatDate(m.sent_at) : "",
            m.total_recipients, m.successful, m.failed_count, formatDate(m.created_at),
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
                `"${m.title}"`, m.audience, m.channel, m.scheduled_at ? formatDate(m.scheduled_at) : "", m.total_recipients,
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

    const selectedUserLabel = useMemo(() => {
        if (!form.particular_id) return "";
        const found = userOptions.find((u) => u.id === form.particular_id);
        return found ? found.label : form.particular_id;
    }, [form.particular_id, userOptions]);

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
                        Send push notifications, emails, and SMS to customers and drivers.
                    </p>
                </div>
                <Button variant="outline" size="sm" onClick={fetchData} disabled={loading}>
                    <RefreshCw className={`mr-2 h-4 w-4 ${loading ? "animate-spin" : ""}`} /> Refresh
                </Button>
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

            {/* Page Tabs */}
            <div className="flex gap-1 border-b">
                {[
                    { key: "compose", label: "Compose Message", icon: Send },
                    { key: "scheduled", label: `Upcoming Schedule (${scheduledMessages.length})`, icon: Timer },
                    { key: "history", label: "Message History", icon: FileText },
                ].map((tab) => (
                    <button
                        key={tab.key}
                        onClick={() => setActiveTab(tab.key as any)}
                        className={`flex items-center gap-2 px-4 py-2.5 text-sm font-medium border-b-2 transition-colors -mb-px ${
                            activeTab === tab.key
                                ? "border-red-500 text-red-600 dark:text-red-400"
                                : "border-transparent text-muted-foreground hover:text-foreground hover:border-border"
                        }`}
                    >
                        <tab.icon className="h-4 w-4" />
                        {tab.label}
                    </button>
                ))}
            </div>

            {/* === COMPOSE TAB === */}
            {activeTab === "compose" && (
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
                                            onChange={(e) => {
                                                setForm({ ...form, audience: e.target.value, particular_id: "" });
                                                setUserSearch("");
                                                setUserOptions([]);
                                            }}
                                            className="accent-red-500"
                                        />
                                        <span className="text-sm">{opt.label}</span>
                                    </label>
                                ))}
                            </div>

                            {/* Searchable user/driver dropdown */}
                            {(form.audience === "particular_customer" || form.audience === "particular_driver") && (
                                <div className="mt-2 max-w-md space-y-2">
                                    <div className="relative">
                                        <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                                        <Input
                                            placeholder={`Search ${form.audience === "particular_customer" ? "customers" : "drivers"} by name, email, or phone...`}
                                            value={userSearch}
                                            onChange={(e) => setUserSearch(e.target.value)}
                                            className="pl-9"
                                        />
                                    </div>
                                    {form.particular_id && (
                                        <div className="flex items-center gap-2 text-sm bg-violet-500/10 text-violet-700 dark:text-violet-300 px-3 py-1.5 rounded-md">
                                            <User className="h-3 w-3" />
                                            <span>Selected: <strong>{selectedUserLabel}</strong></span>
                                            <button onClick={() => setForm({ ...form, particular_id: "" })} className="ml-auto text-muted-foreground hover:text-foreground">&times;</button>
                                        </div>
                                    )}
                                    {userSearchLoading && (
                                        <p className="text-xs text-muted-foreground">Searching...</p>
                                    )}
                                    {!userSearchLoading && userOptions.length > 0 && !form.particular_id && (
                                        <div className="border rounded-md max-h-48 overflow-y-auto">
                                            {userOptions.map((opt) => (
                                                <button
                                                    key={opt.id}
                                                    className="w-full text-left px-3 py-2 text-sm hover:bg-muted/50 border-b last:border-b-0 flex items-center justify-between"
                                                    onClick={() => {
                                                        setForm({ ...form, particular_id: opt.id });
                                                        setUserSearch("");
                                                    }}
                                                >
                                                    <div>
                                                        <p className="font-medium">{opt.label}</p>
                                                        <p className="text-xs text-muted-foreground">
                                                            {opt.email && <span>{opt.email}</span>}
                                                            {opt.email && opt.phone && <span> &middot; </span>}
                                                            {opt.phone && <span>{opt.phone}</span>}
                                                        </p>
                                                    </div>
                                                    <span className="text-xs text-muted-foreground font-mono">{opt.id.slice(0, 8)}</span>
                                                </button>
                                            ))}
                                        </div>
                                    )}
                                    {!userSearchLoading && userSearch && userOptions.length === 0 && (
                                        <p className="text-xs text-muted-foreground">No results found for &quot;{userSearch}&quot;</p>
                                    )}
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
                                        <opt.icon className="h-4 w-4 text-muted-foreground" />
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
            )}

            {/* === SCHEDULED TAB === */}
            {activeTab === "scheduled" && (
                <Card>
                    <CardHeader className="pb-3">
                        <CardTitle className="flex items-center gap-2 text-lg">
                            <Timer className="h-5 w-5 text-blue-500" />
                            Upcoming Scheduled Messages
                            <Badge variant="secondary" className="ml-2">{scheduledMessages.length}</Badge>
                        </CardTitle>
                    </CardHeader>
                    <CardContent className="p-0">
                        {scheduledMessages.length === 0 ? (
                            <div className="text-center py-16">
                                <Timer className="h-12 w-12 text-muted-foreground/30 mx-auto mb-4" />
                                <h3 className="text-lg font-semibold">No scheduled messages</h3>
                                <p className="text-muted-foreground mt-1">Schedule a message from the Compose tab.</p>
                            </div>
                        ) : (
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
                                                <span className="text-sm capitalize">{msg.audience.replace(/_/g, " ")}</span>
                                            </TableCell>
                                            <TableCell>
                                                <Badge variant="outline" className="text-xs capitalize">{msg.channel}</Badge>
                                            </TableCell>
                                            <TableCell>
                                                <div className="flex items-center gap-1 text-sm">
                                                    <Calendar className="h-3 w-3 text-blue-500" />
                                                    {msg.scheduled_at ? formatDate(msg.scheduled_at) : "—"}
                                                </div>
                                            </TableCell>
                                            <TableCell className="text-sm">{msg.total_recipients.toLocaleString()}</TableCell>
                                            <TableCell className="text-right">
                                                <div className="flex justify-end gap-1">
                                                    <Button variant="ghost" size="icon" className="h-8 w-8" onClick={() => setSelectedMessage(msg)}>
                                                        <Eye className="h-4 w-4" />
                                                    </Button>
                                                    <Button variant="ghost" size="icon" className="h-8 w-8 text-destructive" onClick={() => handleDelete(msg.id)}>
                                                        <Trash2 className="h-4 w-4" />
                                                    </Button>
                                                </div>
                                            </TableCell>
                                        </TableRow>
                                    ))}
                                </TableBody>
                            </Table>
                        )}
                    </CardContent>
                </Card>
            )}

            {/* === HISTORY TAB === */}
            {activeTab === "history" && (
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
                                <SelectTrigger className="w-36">
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
                                <SelectTrigger className="w-36">
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

                        {/* Date Range */}
                        <div className="flex flex-wrap items-center gap-3">
                            <div className="flex items-center gap-2">
                                <Label className="text-xs text-muted-foreground whitespace-nowrap">From</Label>
                                <Input
                                    type="date"
                                    value={dateFrom}
                                    onChange={(e) => setDateFrom(e.target.value)}
                                    className="w-40"
                                />
                            </div>
                            <div className="flex items-center gap-2">
                                <Label className="text-xs text-muted-foreground whitespace-nowrap">To</Label>
                                <Input
                                    type="date"
                                    value={dateTo}
                                    onChange={(e) => setDateTo(e.target.value)}
                                    className="w-40"
                                />
                            </div>
                            {(dateFrom || dateTo) && (
                                <Button variant="ghost" size="sm" onClick={() => { setDateFrom(""); setDateTo(""); }}>
                                    Clear dates
                                </Button>
                            )}
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
                                            paginatedHistory.map((msg) => {
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
                                                                <p className="text-xs text-muted-foreground truncate max-w-[200px]">{msg.description}</p>
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
                                                                <Button variant="ghost" size="icon" className="h-8 w-8" onClick={(e) => { e.stopPropagation(); setSelectedMessage(msg); }}>
                                                                    <Eye className="h-4 w-4" />
                                                                </Button>
                                                                {(msg.status === "scheduled" || msg.status === "pending") && (
                                                                    <Button variant="ghost" size="icon" className="h-8 w-8 text-destructive" onClick={(e) => { e.stopPropagation(); handleDelete(msg.id); }}>
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

                                {/* Pagination */}
                                {filtered.length > PER_PAGE && (
                                    <div className="flex items-center justify-between px-4 py-3 border-t">
                                        <p className="text-sm text-muted-foreground">
                                            Showing {(historyPage - 1) * PER_PAGE + 1}–{Math.min(historyPage * PER_PAGE, filtered.length)} of {filtered.length}
                                        </p>
                                        <div className="flex items-center gap-2">
                                            <Button variant="outline" size="sm" onClick={() => setHistoryPage(p => Math.max(1, p - 1))} disabled={historyPage === 1}>
                                                <ChevronLeft className="h-4 w-4" />
                                            </Button>
                                            <span className="text-sm text-muted-foreground">Page {historyPage} of {totalHistoryPages}</span>
                                            <Button variant="outline" size="sm" onClick={() => setHistoryPage(p => Math.min(totalHistoryPages, p + 1))} disabled={historyPage === totalHistoryPages}>
                                                <ChevronRight className="h-4 w-4" />
                                            </Button>
                                        </div>
                                    </div>
                                )}
                            </div>
                        )}
                    </CardContent>
                </Card>
            )}

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
                                <div className="rounded-lg bg-muted/50 p-3 text-sm mt-1">{selectedMessage.description}</div>
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
                                            <span>{((selectedMessage.successful / selectedMessage.total_recipients) * 100).toFixed(1)}%</span>
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
