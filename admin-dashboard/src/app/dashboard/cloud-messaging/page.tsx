"use client";

import { useEffect, useState, useMemo, useCallback } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Switch } from "@/components/ui/switch";
import {
    Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";
import {
    Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import {
    Dialog, DialogContent, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import {
    Cloud, Send, Users, Car, Bell, Mail, Calendar, Clock, Download, Search,
    CheckCircle2, XCircle, Timer, Trash2, Eye, RefreshCw, FileText, User,
    Phone, ChevronLeft, ChevronRight, Info, AlertCircle, MapPin, Flame, X, Check,
} from "lucide-react";
import { cn, formatDate } from "@/lib/utils";
import {
    getCloudMessages, sendCloudMessage, getCloudMessageStats,
    deleteCloudMessage, getUsers, getDrivers,
} from "@/lib/api";

// --- Types ---

interface CloudMessage {
    id: string;
    title: string;
    description: string;
    audience: string;
    particular_ids?: string[];
    particular_id?: string;
    channels?: string[];
    channel?: string;
    type?: string;
    status: string;
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

// --- Constants ---

const AUDIENCE_OPTIONS = [
    { value: "customers", label: "Customers", icon: Users },
    { value: "drivers", label: "Drivers", icon: Car },
    { value: "particular_customer", label: "Particular Customer", icon: User },
    { value: "particular_driver", label: "Particular Driver", icon: User },
];

const NOTIFICATION_TYPES = [
    { value: "info", label: "Information", icon: Info, color: "text-blue-500" },
    { value: "alert", label: "Alert", icon: AlertCircle, color: "text-amber-500" },
    { value: "surge", label: "Surge Pricing", icon: MapPin, color: "text-purple-500" },
    { value: "promotion", label: "Promotion", icon: Flame, color: "text-pink-500" },
    { value: "system", label: "System", icon: Clock, color: "text-gray-500" },
];

const STATUS_CONFIG: Record<string, { label: string; color: string; icon: any }> = {
    sent: { label: "Sent", color: "bg-emerald-500/15 text-emerald-600", icon: CheckCircle2 },
    scheduled: { label: "Scheduled", color: "bg-blue-500/15 text-blue-600", icon: Timer },
    failed: { label: "Failed", color: "bg-red-500/15 text-red-600", icon: XCircle },
    pending: { label: "Pending", color: "bg-amber-500/15 text-amber-600", icon: Clock },
    cancelled: { label: "Cancelled", color: "bg-zinc-500/15 text-zinc-600", icon: XCircle },
};

const PER_PAGE = 20;
const emptyStats: MessageStats = { total_messages: 0, total_sent: 0, total_scheduled: 0, total_failed: 0, total_recipients_reached: 0, success_rate: 0 };

// --- Component ---

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

    // Multi-select user/driver
    const [userOptions, setUserOptions] = useState<UserOption[]>([]);
    const [selectedUsers, setSelectedUsers] = useState<UserOption[]>([]);
    const [userSearch, setUserSearch] = useState("");
    const [userSearchLoading, setUserSearchLoading] = useState(false);

    // Form
    const [form, setForm] = useState({
        title: "",
        description: "",
        audience: "customers",
        particular_ids: [] as string[],
        type: "info",
        send_push: true,
        send_email: false,
        send_sms: false,
        is_scheduled: false,
        scheduled_at: "",
    });

    useEffect(() => { fetchData(); }, []);

    const fetchData = async () => {
        setLoading(true);
        try {
            const [md, sd] = await Promise.all([
                getCloudMessages().catch(() => null),
                getCloudMessageStats().catch(() => null),
            ]);
            setMessages(md && Array.isArray(md) ? md : []);
            setStats(sd || emptyStats);
        } catch {
            setMessages([]);
            setStats(emptyStats);
        } finally {
            setLoading(false);
        }
    };

    // Fetch users/drivers for multi-select
    const fetchUserOptions = useCallback(async (type: "customer" | "driver", query: string) => {
        setUserSearchLoading(true);
        try {
            const data = type === "customer" ? await getUsers() : await getDrivers();
            const opts: UserOption[] = (data || []).map((u: any) => ({
                id: u.id,
                label: `${u.first_name || ""} ${u.last_name || ""}`.trim() || u.email || u.phone || u.id,
                email: u.email,
                phone: u.phone,
            }));
            const q = query.toLowerCase();
            const filtered = q
                ? opts.filter((o) => o.label.toLowerCase().includes(q) || o.email?.toLowerCase().includes(q) || o.phone?.includes(q) || o.id.toLowerCase().includes(q))
                : opts.slice(0, 50);
            setUserOptions(filtered);
        } catch {
            setUserOptions([]);
        } finally {
            setUserSearchLoading(false);
        }
    }, []);

    useEffect(() => {
        if (form.audience !== "particular_customer" && form.audience !== "particular_driver") {
            setUserOptions([]);
            return;
        }
        const type = form.audience === "particular_customer" ? "customer" : "driver";
        const timer = setTimeout(() => fetchUserOptions(type, userSearch), 300);
        return () => clearTimeout(timer);
    }, [form.audience, userSearch, fetchUserOptions]);

    const toggleUserSelection = (opt: UserOption) => {
        if (form.particular_ids.includes(opt.id)) {
            setForm((f) => ({ ...f, particular_ids: f.particular_ids.filter((id) => id !== opt.id) }));
            setSelectedUsers((prev) => prev.filter((u) => u.id !== opt.id));
        } else {
            setForm((f) => ({ ...f, particular_ids: [...f.particular_ids, opt.id] }));
            setSelectedUsers((prev) => [...prev, opt]);
        }
    };

    const removeUser = (id: string) => {
        setForm((f) => ({ ...f, particular_ids: f.particular_ids.filter((uid) => uid !== id) }));
        setSelectedUsers((prev) => prev.filter((u) => u.id !== id));
    };

    // Filtered messages
    const filtered = useMemo(() => {
        return messages.filter((m) => {
            const matchSearch = !search || m.title.toLowerCase().includes(search.toLowerCase()) || m.description.toLowerCase().includes(search.toLowerCase());
            const matchStatus = statusFilter === "all" || m.status === statusFilter;
            const matchAudience = audienceFilter === "all" || m.audience === audienceFilter;
            const msgDate = m.sent_at || m.scheduled_at || m.created_at;
            let matchDate = true;
            if (dateFrom && msgDate) matchDate = matchDate && msgDate >= dateFrom;
            if (dateTo && msgDate) matchDate = matchDate && msgDate <= dateTo + "T23:59:59Z";
            return matchSearch && matchStatus && matchAudience && matchDate;
        });
    }, [messages, search, statusFilter, audienceFilter, dateFrom, dateTo]);

    const scheduledMessages = useMemo(() => {
        return messages.filter((m) => m.status === "scheduled").sort((a, b) => new Date(a.scheduled_at || "").getTime() - new Date(b.scheduled_at || "").getTime());
    }, [messages]);

    const totalHistoryPages = Math.max(1, Math.ceil(filtered.length / PER_PAGE));
    const paginatedHistory = filtered.slice((historyPage - 1) * PER_PAGE, historyPage * PER_PAGE);
    useEffect(() => { setHistoryPage(1); }, [search, statusFilter, audienceFilter, dateFrom, dateTo]);

    const handleSend = async () => {
        if (!form.title.trim() || !form.description.trim()) { alert("Please fill in title and description."); return; }
        const isParticular = form.audience === "particular_customer" || form.audience === "particular_driver";
        if (isParticular && form.particular_ids.length === 0) { alert("Please select at least one user/driver."); return; }
        if (form.is_scheduled && !form.scheduled_at) { alert("Please select a date and time."); return; }
        const channels: string[] = [];
        if (form.send_push) channels.push("push");
        if (form.send_email) channels.push("email");
        if (form.send_sms) channels.push("sms");
        if (channels.length === 0) { alert("Please select at least one delivery channel."); return; }

        setSending(true);
        try {
            const payload: any = {
                title: form.title,
                description: form.description,
                audience: form.audience,
                channels,
                type: form.type,
            };
            if (isParticular) payload.particular_ids = form.particular_ids;
            if (form.is_scheduled && form.scheduled_at) {
                payload.scheduled_at = new Date(form.scheduled_at).toISOString();
            }
            await sendCloudMessage(payload);
            await fetchData();
            resetForm();
        } catch (error: any) {
            alert(`Failed to send: ${error.message || "Unknown error"}`);
        } finally {
            setSending(false);
        }
    };

    const resetForm = () => {
        setForm({ title: "", description: "", audience: "customers", particular_ids: [], type: "info", send_push: true, send_email: false, send_sms: false, is_scheduled: false, scheduled_at: "" });
        setSelectedUsers([]);
        setUserSearch("");
        setUserOptions([]);
    };

    const handleDelete = async (id: string) => {
        if (!confirm("Cancel this message?")) return;
        try { await deleteCloudMessage(id); } catch { /* continue */ }
        setMessages((prev) => prev.filter((m) => m.id !== id));
    };

    const handleExport = () => {
        const headers = ["ID", "Title", "Description", "Audience", "Channels", "Type", "Status", "Scheduled At", "Sent At", "Recipients", "Successful", "Failed", "Created"];
        const esc = (v: string) => `"${String(v || "").replace(/"/g, '""')}"`;
        const rows = filtered.map((m) => [m.id, esc(m.title), esc(m.description), m.audience, (m.channels || [m.channel]).join(";"), m.type || "", m.status, m.scheduled_at ? formatDate(m.scheduled_at) : "", m.sent_at ? formatDate(m.sent_at) : "", m.total_recipients, m.successful, m.failed_count, formatDate(m.created_at)]);
        const csv = [headers.join(","), ...rows.map((r) => r.join(","))].join("\n");
        const blob = new Blob([csv], { type: "text/csv" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a"); a.href = url; a.download = `cloud-messaging-${new Date().toISOString().split("T")[0]}.csv`; a.click();
        URL.revokeObjectURL(url);
    };

    const handleExportSummary = () => {
        const rows = [["Cloud Messaging Summary"], [`Generated: ${new Date().toLocaleString()}`], [], ["Metric", "Value"], ["Total", stats.total_messages], ["Sent", stats.total_sent], ["Scheduled", stats.total_scheduled], ["Failed", stats.total_failed], ["Recipients Reached", stats.total_recipients_reached], ["Success Rate", `${stats.success_rate}%`], [], ["Scheduled Messages"], ["Title", "Audience", "Channels", "Scheduled At", "Recipients"], ...scheduledMessages.map((m) => [`"${m.title}"`, m.audience, (m.channels || [m.channel]).join(";"), m.scheduled_at ? formatDate(m.scheduled_at) : "", m.total_recipients])];
        const csv = rows.map((r) => r.join(",")).join("\n");
        const blob = new Blob([csv], { type: "text/csv" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a"); a.href = url; a.download = `cloud-messaging-summary-${new Date().toISOString().split("T")[0]}.csv`; a.click();
        URL.revokeObjectURL(url);
    };

    const getChannels = (m: CloudMessage) => m.channels || (m.channel ? [m.channel] : ["push"]);

    return (
        <div className="space-y-6">
            {/* Header */}
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-3xl font-bold tracking-tight flex items-center gap-2">
                        <Cloud className="h-8 w-8 text-violet-500" />
                        Cloud Messaging
                    </h1>
                    <p className="text-muted-foreground mt-1">Send push notifications, emails, and SMS to customers and drivers.</p>
                </div>
                <Button variant="outline" size="sm" onClick={fetchData} disabled={loading}>
                    <RefreshCw className={`mr-2 h-4 w-4 ${loading ? "animate-spin" : ""}`} /> Refresh
                </Button>
            </div>

            {/* Stats */}
            <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4">
                {[
                    { label: "Total Messages", value: stats.total_messages, icon: Cloud, color: "text-violet-500" },
                    { label: "Sent", value: stats.total_sent, icon: CheckCircle2, color: "text-emerald-500" },
                    { label: "Scheduled", value: stats.total_scheduled, icon: Timer, color: "text-blue-500" },
                    { label: "Failed", value: stats.total_failed, icon: XCircle, color: "text-red-500" },
                    { label: "Recipients Reached", value: stats.total_recipients_reached.toLocaleString(), icon: Users, color: "text-amber-500" },
                    { label: "Success Rate", value: `${stats.success_rate}%`, icon: CheckCircle2, color: "text-emerald-500" },
                ].map((s, i) => (
                    <Card key={i}>
                        <CardContent className="pt-4 pb-3">
                            <div className="flex items-center gap-2">
                                <s.icon className={`h-5 w-5 ${s.color}`} />
                                <div>
                                    <p className="text-xs text-muted-foreground">{s.label}</p>
                                    <p className="text-2xl font-bold">{s.value}</p>
                                </div>
                            </div>
                        </CardContent>
                    </Card>
                ))}
            </div>

            {/* Tabs */}
            <div className="flex gap-1 border-b">
                {[
                    { key: "compose", label: "Compose Message", icon: Send },
                    { key: "scheduled", label: `Upcoming (${scheduledMessages.length})`, icon: Timer },
                    { key: "history", label: "Message History", icon: FileText },
                ].map((tab) => (
                    <button key={tab.key} onClick={() => setActiveTab(tab.key as any)} className={`flex items-center gap-2 px-4 py-2.5 text-sm font-medium border-b-2 transition-colors -mb-px ${activeTab === tab.key ? "border-red-500 text-red-600 dark:text-red-400" : "border-transparent text-muted-foreground hover:text-foreground hover:border-border"}`}>
                        <tab.icon className="h-4 w-4" /> {tab.label}
                    </button>
                ))}
            </div>

            {/* ═══ COMPOSE TAB ═══ */}
            {activeTab === "compose" && (
                <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
                    {/* ── Left Column (2/3 width) ── */}
                    <div className="lg:col-span-2 space-y-6">
                        {/* Audience Card */}
                        <Card>
                            <CardHeader className="pb-3">
                                <CardTitle className="text-base">Audience</CardTitle>
                            </CardHeader>
                            <CardContent className="space-y-4">
                                <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
                                    {AUDIENCE_OPTIONS.map((opt) => {
                                        const isActive = form.audience === opt.value;
                                        return (
                                            <button
                                                key={opt.value}
                                                onClick={() => { setForm({ ...form, audience: opt.value, particular_ids: [] }); setSelectedUsers([]); setUserSearch(""); setUserOptions([]); }}
                                                className={cn(
                                                    "flex items-center gap-2 rounded-lg border px-3 py-2.5 text-sm font-medium transition-colors",
                                                    isActive
                                                        ? "border-primary bg-primary/5 text-primary"
                                                        : "border-border text-muted-foreground hover:bg-accent hover:text-accent-foreground"
                                                )}
                                            >
                                                <opt.icon className="h-4 w-4" />
                                                {opt.label}
                                            </button>
                                        );
                                    })}
                                </div>

                                {/* Multi-select user/driver */}
                                {(form.audience === "particular_customer" || form.audience === "particular_driver") && (
                                    <div className="space-y-3 pt-1">
                                        {selectedUsers.length > 0 && (
                                            <div className="flex flex-wrap gap-1.5">
                                                {selectedUsers.map((u) => (
                                                    <span key={u.id} className="inline-flex items-center gap-1 bg-primary/10 text-primary rounded-full px-2.5 py-1 text-xs font-medium">
                                                        <User className="h-3 w-3" />
                                                        {u.label}
                                                        <button onClick={() => removeUser(u.id)} className="ml-0.5 hover:text-destructive"><X className="h-3 w-3" /></button>
                                                    </span>
                                                ))}
                                            </div>
                                        )}
                                        <div className="relative">
                                            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                                            <Input placeholder={`Search ${form.audience === "particular_customer" ? "customers" : "drivers"} by name, email, or phone...`} value={userSearch} onChange={(e) => setUserSearch(e.target.value)} className="pl-9" />
                                        </div>
                                        {userSearchLoading && <p className="text-xs text-muted-foreground">Searching...</p>}
                                        {!userSearchLoading && userOptions.length > 0 && (
                                            <div className="border rounded-lg max-h-52 overflow-y-auto divide-y">
                                                {userOptions.map((opt) => {
                                                    const isSelected = form.particular_ids.includes(opt.id);
                                                    return (
                                                        <button key={opt.id} className={cn("w-full text-left px-3 py-2.5 text-sm flex items-center justify-between transition-colors", isSelected ? "bg-primary/5" : "hover:bg-accent")} onClick={() => toggleUserSelection(opt)}>
                                                            <div>
                                                                <p className="font-medium">{opt.label}</p>
                                                                <p className="text-xs text-muted-foreground">{[opt.email, opt.phone].filter(Boolean).join(" · ")}</p>
                                                            </div>
                                                            {isSelected ? <Check className="h-4 w-4 text-primary shrink-0" /> : <span className="text-xs text-muted-foreground font-mono shrink-0">{opt.id.slice(0, 8)}</span>}
                                                        </button>
                                                    );
                                                })}
                                            </div>
                                        )}
                                        {!userSearchLoading && userSearch && userOptions.length === 0 && <p className="text-xs text-muted-foreground">No results found</p>}
                                    </div>
                                )}
                            </CardContent>
                        </Card>

                        {/* Message Content Card */}
                        <Card>
                            <CardHeader className="pb-3">
                                <CardTitle className="text-base">Message Content</CardTitle>
                            </CardHeader>
                            <CardContent className="space-y-4">
                                <div className="space-y-2">
                                    <Label className="text-sm font-medium">Title <span className="text-destructive">*</span></Label>
                                    <Input placeholder="Enter notification title" value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} />
                                </div>
                                <div className="space-y-2">
                                    <Label className="text-sm font-medium">Description <span className="text-destructive">*</span></Label>
                                    <Textarea placeholder="Enter notification message..." value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} rows={5} className="resize-none" />
                                </div>
                                <div className="space-y-2 max-w-xs">
                                    <Label className="text-sm font-medium">Notification Type</Label>
                                    <Select value={form.type} onValueChange={(v) => setForm({ ...form, type: v })}>
                                        <SelectTrigger><SelectValue /></SelectTrigger>
                                        <SelectContent>
                                            {NOTIFICATION_TYPES.map((t) => (
                                                <SelectItem key={t.value} value={t.value}>
                                                    <span className="flex items-center gap-2"><t.icon className={`h-3.5 w-3.5 ${t.color}`} />{t.label}</span>
                                                </SelectItem>
                                            ))}
                                        </SelectContent>
                                    </Select>
                                </div>
                            </CardContent>
                        </Card>
                    </div>

                    {/* ── Right Column: Settings & Send (1/3 width) ── */}
                    <div className="space-y-6">
                        {/* Delivery Channels */}
                        <Card>
                            <CardHeader className="pb-3">
                                <CardTitle className="text-base">Delivery Channels</CardTitle>
                            </CardHeader>
                            <CardContent className="space-y-3">
                                <div className="flex items-center justify-between rounded-lg border px-3 py-2.5">
                                    <div className="flex items-center gap-2.5"><Bell className="h-4 w-4 text-muted-foreground" /><span className="text-sm font-medium">Push Notification</span></div>
                                    <Switch id="ch-push" checked={form.send_push} onCheckedChange={(v) => setForm({ ...form, send_push: v })} />
                                </div>
                                <div className="flex items-center justify-between rounded-lg border px-3 py-2.5">
                                    <div className="flex items-center gap-2.5"><Mail className="h-4 w-4 text-muted-foreground" /><span className="text-sm font-medium">Email</span></div>
                                    <Switch id="ch-email" checked={form.send_email} onCheckedChange={(v) => setForm({ ...form, send_email: v })} />
                                </div>
                                <div className="flex items-center justify-between rounded-lg border px-3 py-2.5">
                                    <div className="flex items-center gap-2.5"><Phone className="h-4 w-4 text-muted-foreground" /><span className="text-sm font-medium">SMS</span></div>
                                    <Switch id="ch-sms" checked={form.send_sms} onCheckedChange={(v) => setForm({ ...form, send_sms: v })} />
                                </div>
                            </CardContent>
                        </Card>

                        {/* Schedule */}
                        <Card>
                            <CardHeader className="pb-3">
                                <CardTitle className="text-base">Schedule</CardTitle>
                            </CardHeader>
                            <CardContent className="space-y-3">
                                <div className="flex items-center justify-between rounded-lg border px-3 py-2.5">
                                    <div className="flex items-center gap-2.5"><Calendar className="h-4 w-4 text-muted-foreground" /><span className="text-sm font-medium">Schedule for later</span></div>
                                    <Switch checked={form.is_scheduled} onCheckedChange={(v) => setForm({ ...form, is_scheduled: v })} />
                                </div>
                                {form.is_scheduled && (
                                    <div className="space-y-1.5">
                                        <Label className="text-xs text-muted-foreground">Date & Time</Label>
                                        <Input
                                            type="datetime-local"
                                            value={form.scheduled_at}
                                            onChange={(e) => setForm({ ...form, scheduled_at: e.target.value })}
                                            min={new Date().toISOString().slice(0, 16)}
                                        />
                                    </div>
                                )}
                                {!form.is_scheduled && (
                                    <p className="text-xs text-muted-foreground">Message will be sent immediately.</p>
                                )}
                            </CardContent>
                        </Card>

                        {/* Send Button */}
                        <Button className="w-full h-11 bg-primary hover:bg-primary/90 text-primary-foreground" onClick={handleSend} disabled={sending}>
                            {sending ? <div className="h-4 w-4 animate-spin rounded-full border-2 border-primary-foreground border-t-transparent mr-2" /> : form.is_scheduled ? <Calendar className="mr-2 h-4 w-4" /> : <Send className="mr-2 h-4 w-4" />}
                            {form.is_scheduled ? "Schedule Notification" : "Send Notification"}
                        </Button>
                    </div>
                </div>
            )}

            {/* ═══ SCHEDULED TAB ═══ */}
            {activeTab === "scheduled" && (
                <Card>
                    <CardHeader className="pb-3">
                        <CardTitle className="flex items-center gap-2 text-lg">
                            <Timer className="h-5 w-5 text-blue-500" /> Upcoming Scheduled Messages <Badge variant="secondary" className="ml-2">{scheduledMessages.length}</Badge>
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
                                <TableHeader><TableRow>
                                    <TableHead>Title</TableHead><TableHead>Audience</TableHead><TableHead>Channels</TableHead><TableHead>Scheduled For</TableHead><TableHead>Recipients</TableHead><TableHead className="text-right">Actions</TableHead>
                                </TableRow></TableHeader>
                                <TableBody>
                                    {scheduledMessages.map((msg) => (
                                        <TableRow key={msg.id}>
                                            <TableCell><p className="font-medium text-sm">{msg.title}</p><p className="text-xs text-muted-foreground truncate max-w-[250px]">{msg.description}</p></TableCell>
                                            <TableCell><span className="text-sm capitalize">{msg.audience.replace(/_/g, " ")}</span></TableCell>
                                            <TableCell><div className="flex gap-1">{getChannels(msg).map((c) => <Badge key={c} variant="outline" className="text-xs capitalize">{c}</Badge>)}</div></TableCell>
                                            <TableCell><div className="flex items-center gap-1 text-sm"><Calendar className="h-3 w-3 text-blue-500" />{msg.scheduled_at ? formatDate(msg.scheduled_at) : "—"}</div></TableCell>
                                            <TableCell className="text-sm">{msg.total_recipients.toLocaleString()}</TableCell>
                                            <TableCell className="text-right"><div className="flex justify-end gap-1">
                                                <Button variant="ghost" size="icon" className="h-8 w-8" onClick={() => setSelectedMessage(msg)}><Eye className="h-4 w-4" /></Button>
                                                <Button variant="ghost" size="icon" className="h-8 w-8 text-destructive" onClick={() => handleDelete(msg.id)}><Trash2 className="h-4 w-4" /></Button>
                                            </div></TableCell>
                                        </TableRow>
                                    ))}
                                </TableBody>
                            </Table>
                        )}
                    </CardContent>
                </Card>
            )}

            {/* ═══ HISTORY TAB ═══ */}
            {activeTab === "history" && (
                <Card>
                    <CardHeader className="pb-3">
                        <div className="flex items-center justify-between">
                            <CardTitle className="flex items-center gap-2 text-lg"><FileText className="h-5 w-5 text-violet-500" /> Message History & Report</CardTitle>
                            <div className="flex gap-2">
                                <Button variant="outline" size="sm" onClick={handleExportSummary}><Download className="mr-2 h-4 w-4" /> Summary</Button>
                                <Button variant="outline" size="sm" onClick={handleExport} disabled={filtered.length === 0}><Download className="mr-2 h-4 w-4" /> Export</Button>
                            </div>
                        </div>
                    </CardHeader>
                    <CardContent className="space-y-4">
                        <div className="flex flex-wrap items-center gap-3">
                            <div className="relative flex-1 max-w-sm">
                                <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                                <Input placeholder="Search messages..." value={search} onChange={(e) => setSearch(e.target.value)} className="pl-9" />
                            </div>
                            <Select value={statusFilter} onValueChange={setStatusFilter}><SelectTrigger className="w-36"><SelectValue placeholder="Status" /></SelectTrigger><SelectContent><SelectItem value="all">All Status</SelectItem><SelectItem value="sent">Sent</SelectItem><SelectItem value="scheduled">Scheduled</SelectItem><SelectItem value="failed">Failed</SelectItem><SelectItem value="cancelled">Cancelled</SelectItem></SelectContent></Select>
                            <Select value={audienceFilter} onValueChange={setAudienceFilter}><SelectTrigger className="w-36"><SelectValue placeholder="Audience" /></SelectTrigger><SelectContent><SelectItem value="all">All</SelectItem>{AUDIENCE_OPTIONS.map((a) => <SelectItem key={a.value} value={a.value}>{a.label}</SelectItem>)}</SelectContent></Select>
                        </div>
                        <div className="flex flex-wrap items-center gap-3">
                            <div className="flex items-center gap-2"><Label className="text-xs text-muted-foreground">From</Label><Input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} className="w-40" /></div>
                            <div className="flex items-center gap-2"><Label className="text-xs text-muted-foreground">To</Label><Input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} className="w-40" /></div>
                            {(dateFrom || dateTo) && <Button variant="ghost" size="sm" onClick={() => { setDateFrom(""); setDateTo(""); }}>Clear</Button>}
                        </div>

                        {loading ? (
                            <div className="flex items-center justify-center p-12"><div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" /></div>
                        ) : (
                            <div className="border rounded-lg">
                                <Table>
                                    <TableHeader><TableRow>
                                        <TableHead>Title</TableHead><TableHead>Type</TableHead><TableHead>Audience</TableHead><TableHead>Channels</TableHead><TableHead>Status</TableHead><TableHead>Recipients</TableHead><TableHead>OK</TableHead><TableHead>Fail</TableHead><TableHead>Date</TableHead><TableHead className="text-right">Actions</TableHead>
                                    </TableRow></TableHeader>
                                    <TableBody>
                                        {filtered.length === 0 ? (
                                            <TableRow><TableCell colSpan={10} className="text-center text-muted-foreground py-12">No messages found.</TableCell></TableRow>
                                        ) : paginatedHistory.map((msg) => {
                                            const sc = STATUS_CONFIG[msg.status] || STATUS_CONFIG.pending;
                                            const SI = sc.icon;
                                            const typeCfg = NOTIFICATION_TYPES.find((t) => t.value === msg.type);
                                            const TI = typeCfg?.icon || Info;
                                            return (
                                                <TableRow key={msg.id} className="cursor-pointer hover:bg-muted/50" onClick={() => setSelectedMessage(msg)}>
                                                    <TableCell><p className="font-medium text-sm">{msg.title}</p><p className="text-xs text-muted-foreground truncate max-w-[180px]">{msg.description}</p></TableCell>
                                                    <TableCell><div className="flex items-center gap-1"><TI className={`h-3 w-3 ${typeCfg?.color || ""}`} /><span className="text-xs capitalize">{msg.type || "info"}</span></div></TableCell>
                                                    <TableCell><span className="text-sm capitalize">{msg.audience.replace(/_/g, " ")}</span></TableCell>
                                                    <TableCell><div className="flex gap-1">{getChannels(msg).map((c) => <Badge key={c} variant="outline" className="text-[10px] capitalize">{c}</Badge>)}</div></TableCell>
                                                    <TableCell><Badge className={sc.color}><SI className="h-3 w-3 mr-1" />{sc.label}</Badge></TableCell>
                                                    <TableCell className="text-sm">{msg.total_recipients.toLocaleString()}</TableCell>
                                                    <TableCell><span className="text-sm text-emerald-600 font-medium">{msg.successful.toLocaleString()}</span></TableCell>
                                                    <TableCell><span className={`text-sm font-medium ${msg.failed_count > 0 ? "text-red-500" : "text-muted-foreground"}`}>{msg.failed_count.toLocaleString()}</span></TableCell>
                                                    <TableCell className="text-xs text-muted-foreground">{formatDate(msg.sent_at || msg.scheduled_at || msg.created_at)}</TableCell>
                                                    <TableCell className="text-right">
                                                        <div className="flex justify-end gap-1">
                                                            <Button variant="ghost" size="icon" className="h-8 w-8" onClick={(e) => { e.stopPropagation(); setSelectedMessage(msg); }}><Eye className="h-4 w-4" /></Button>
                                                            {(msg.status === "scheduled" || msg.status === "pending") && <Button variant="ghost" size="icon" className="h-8 w-8 text-destructive" onClick={(e) => { e.stopPropagation(); handleDelete(msg.id); }}><Trash2 className="h-4 w-4" /></Button>}
                                                        </div>
                                                    </TableCell>
                                                </TableRow>
                                            );
                                        })}
                                    </TableBody>
                                </Table>
                                {filtered.length > PER_PAGE && (
                                    <div className="flex items-center justify-between px-4 py-3 border-t">
                                        <p className="text-sm text-muted-foreground">Showing {(historyPage - 1) * PER_PAGE + 1}–{Math.min(historyPage * PER_PAGE, filtered.length)} of {filtered.length}</p>
                                        <div className="flex items-center gap-2">
                                            <Button variant="outline" size="sm" onClick={() => setHistoryPage((p) => Math.max(1, p - 1))} disabled={historyPage === 1}><ChevronLeft className="h-4 w-4" /></Button>
                                            <span className="text-sm text-muted-foreground">Page {historyPage} of {totalHistoryPages}</span>
                                            <Button variant="outline" size="sm" onClick={() => setHistoryPage((p) => Math.min(totalHistoryPages, p + 1))} disabled={historyPage === totalHistoryPages}><ChevronRight className="h-4 w-4" /></Button>
                                        </div>
                                    </div>
                                )}
                            </div>
                        )}
                    </CardContent>
                </Card>
            )}

            {/* ═══ DETAIL DIALOG ═══ */}
            <Dialog open={!!selectedMessage} onOpenChange={(open) => { if (!open) setSelectedMessage(null); }}>
                <DialogContent className="sm:max-w-lg">
                    <DialogHeader><DialogTitle className="flex items-center gap-2"><Cloud className="h-5 w-5 text-violet-500" /> Message Details</DialogTitle></DialogHeader>
                    {selectedMessage && (
                        <div className="space-y-4">
                            <div><Label className="text-xs text-muted-foreground">Title</Label><p className="font-semibold">{selectedMessage.title}</p></div>
                            <div><Label className="text-xs text-muted-foreground">Description</Label><div className="rounded-lg bg-muted/50 p-3 text-sm mt-1">{selectedMessage.description}</div></div>
                            <div className="grid grid-cols-3 gap-4">
                                <div><Label className="text-xs text-muted-foreground">Type</Label><p className="text-sm capitalize">{selectedMessage.type || "info"}</p></div>
                                <div><Label className="text-xs text-muted-foreground">Audience</Label><p className="text-sm capitalize">{selectedMessage.audience.replace(/_/g, " ")}</p></div>
                                <div><Label className="text-xs text-muted-foreground">Channels</Label><div className="flex gap-1 mt-0.5">{getChannels(selectedMessage).map((c) => <Badge key={c} variant="outline" className="text-xs capitalize">{c}</Badge>)}</div></div>
                            </div>
                            <div><Label className="text-xs text-muted-foreground">Status</Label><div className="mt-1"><Badge className={STATUS_CONFIG[selectedMessage.status]?.color || "bg-zinc-500/15"}>{STATUS_CONFIG[selectedMessage.status]?.label || selectedMessage.status}</Badge></div></div>
                            <Separator />
                            <div>
                                <Label className="text-xs text-muted-foreground mb-2 block">Delivery Report</Label>
                                <div className="grid grid-cols-3 gap-3">
                                    <div className="rounded-lg bg-muted/50 p-3 text-center"><p className="text-lg font-bold">{selectedMessage.total_recipients.toLocaleString()}</p><p className="text-xs text-muted-foreground">Recipients</p></div>
                                    <div className="rounded-lg bg-emerald-50 dark:bg-emerald-900/20 p-3 text-center"><p className="text-lg font-bold text-emerald-600">{selectedMessage.successful.toLocaleString()}</p><p className="text-xs text-muted-foreground">Successful</p></div>
                                    <div className="rounded-lg bg-red-50 dark:bg-red-900/20 p-3 text-center"><p className="text-lg font-bold text-red-500">{selectedMessage.failed_count.toLocaleString()}</p><p className="text-xs text-muted-foreground">Failed</p></div>
                                </div>
                                {selectedMessage.total_recipients > 0 && selectedMessage.status === "sent" && (
                                    <div className="mt-3">
                                        <div className="flex justify-between text-xs text-muted-foreground mb-1"><span>Success Rate</span><span>{((selectedMessage.successful / selectedMessage.total_recipients) * 100).toFixed(1)}%</span></div>
                                        <div className="h-2 rounded-full bg-muted overflow-hidden"><div className="h-full rounded-full bg-emerald-500 transition-all" style={{ width: `${(selectedMessage.successful / selectedMessage.total_recipients) * 100}%` }} /></div>
                                    </div>
                                )}
                            </div>
                            <div className="grid grid-cols-2 gap-4 text-sm">
                                {selectedMessage.scheduled_at && <div><Label className="text-xs text-muted-foreground">Scheduled For</Label><p>{formatDate(selectedMessage.scheduled_at)}</p></div>}
                                {selectedMessage.sent_at && <div><Label className="text-xs text-muted-foreground">Sent At</Label><p>{formatDate(selectedMessage.sent_at)}</p></div>}
                                <div><Label className="text-xs text-muted-foreground">Created At</Label><p>{formatDate(selectedMessage.created_at)}</p></div>
                            </div>
                        </div>
                    )}
                </DialogContent>
            </Dialog>
        </div>
    );
}
