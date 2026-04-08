"use client";

import { useEffect, useState } from "react";
import {
    getTickets, replyToTicket, closeTicket, createTicket, updateTicket, deleteTicket,
    getFaqs, createFaq, updateFaq, deleteFaq,
} from "@/lib/api";
import { formatDate, statusColor } from "@/lib/utils";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import {
    Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import {
    Dialog, DialogContent, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import {
    Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";
import {
    MessageSquare, CheckCircle, Plus, Trash2, Pencil, Send, Search,
    LifeBuoy, HelpCircle, RefreshCw,
} from "lucide-react";

export default function SupportPage() {
    return (
        <div className="space-y-6">
            <div>
                <h1 className="text-3xl font-bold tracking-tight flex items-center gap-2">
                    <LifeBuoy className="h-8 w-8 text-violet-500" />
                    Support
                </h1>
                <p className="text-muted-foreground mt-1">Manage support tickets and FAQs.</p>
            </div>

            <Tabs defaultValue="tickets">
                <TabsList>
                    <TabsTrigger value="tickets">Tickets</TabsTrigger>
                    <TabsTrigger value="faqs">FAQs</TabsTrigger>
                </TabsList>
                <TabsContent value="tickets" className="mt-4"><TicketsTab /></TabsContent>
                <TabsContent value="faqs" className="mt-4"><FaqsTab /></TabsContent>
            </Tabs>
        </div>
    );
}

/* ── Tickets ─────────────────────────────── */

const TICKET_CATEGORIES = ["general", "rides", "payments", "account", "safety", "driver", "technical"];
const TICKET_PRIORITIES = ["low", "medium", "high", "urgent"];

const PRIORITY_COLORS: Record<string, string> = {
    low: "bg-zinc-500/15 text-zinc-600",
    medium: "bg-blue-500/15 text-blue-600",
    high: "bg-amber-500/15 text-amber-600",
    urgent: "bg-red-500/15 text-red-600",
};

function TicketsTab() {
    const [tickets, setTickets] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [selected, setSelected] = useState<any>(null);
    const [reply, setReply] = useState("");
    const [replying, setReplying] = useState(false);
    const [search, setSearch] = useState("");
    const [statusFilter, setStatusFilter] = useState("all");

    // Create/Edit dialog
    const [dialogOpen, setDialogOpen] = useState(false);
    const [editingTicket, setEditingTicket] = useState<any>(null);
    const [saving, setSaving] = useState(false);
    const [form, setForm] = useState({
        subject: "",
        category: "general",
        priority: "medium",
        message: "",
        user_name: "",
        user_email: "",
    });

    const fetchTickets = () => {
        setLoading(true);
        getTickets().then(setTickets).catch(() => {}).finally(() => setLoading(false));
    };

    useEffect(() => { fetchTickets(); }, []);

    const filtered = tickets.filter((t) => {
        const matchSearch = !search || t.subject?.toLowerCase().includes(search.toLowerCase()) || t.user_name?.toLowerCase().includes(search.toLowerCase());
        const matchStatus = statusFilter === "all" || t.status === statusFilter;
        return matchSearch && matchStatus;
    });

    const resetForm = () => {
        setForm({ subject: "", category: "general", priority: "medium", message: "", user_name: "", user_email: "" });
        setEditingTicket(null);
    };

    const openCreate = () => { resetForm(); setDialogOpen(true); };

    const openEdit = (t: any) => {
        setEditingTicket(t);
        setForm({
            subject: t.subject || "",
            category: t.category || "general",
            priority: t.priority || "medium",
            message: t.message || "",
            user_name: t.user_name || "",
            user_email: t.user_email || "",
        });
        setDialogOpen(true);
    };

    const handleSave = async () => {
        if (!form.subject.trim()) { alert("Please enter a subject."); return; }
        setSaving(true);
        try {
            if (editingTicket) {
                await updateTicket(editingTicket.id, { subject: form.subject, category: form.category, priority: form.priority });
            } else {
                await createTicket(form);
            }
            setDialogOpen(false);
            resetForm();
            fetchTickets();
        } catch (e: any) {
            alert(`Failed: ${e.message}`);
        } finally {
            setSaving(false);
        }
    };

    const handleReply = async (id: string) => {
        if (!reply.trim()) return;
        setReplying(true);
        try { await replyToTicket(id, reply.trim()); setReply(""); fetchTickets(); }
        catch {} finally { setReplying(false); }
    };

    const handleClose = async (id: string) => {
        try { await closeTicket(id); setSelected(null); fetchTickets(); } catch {}
    };

    const handleDelete = async (id: string) => {
        if (!confirm("Delete this ticket?")) return;
        try { await deleteTicket(id); if (selected?.id === id) setSelected(null); fetchTickets(); }
        catch (e: any) { alert(`Failed: ${e.message}`); }
    };

    return (
        <div className="space-y-4">
            {/* Toolbar */}
            <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="flex flex-wrap items-center gap-3 flex-1">
                    <div className="relative flex-1 max-w-sm">
                        <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                        <Input placeholder="Search tickets..." value={search} onChange={(e) => setSearch(e.target.value)} className="pl-9" />
                    </div>
                    <Select value={statusFilter} onValueChange={setStatusFilter}>
                        <SelectTrigger className="w-36"><SelectValue /></SelectTrigger>
                        <SelectContent>
                            <SelectItem value="all">All Status</SelectItem>
                            <SelectItem value="open">Open</SelectItem>
                            <SelectItem value="in_progress">In Progress</SelectItem>
                            <SelectItem value="closed">Closed</SelectItem>
                        </SelectContent>
                    </Select>
                </div>
                <div className="flex gap-2">
                    <Button variant="outline" size="sm" onClick={fetchTickets} disabled={loading}><RefreshCw className={`mr-2 h-4 w-4 ${loading ? "animate-spin" : ""}`} /> Refresh</Button>
                    <Button size="sm" onClick={openCreate}><Plus className="mr-2 h-4 w-4" /> Create Ticket</Button>
                </div>
            </div>

            <div className="grid gap-4 lg:grid-cols-[1fr_400px]">
                {/* Table */}
                <Card className="border-border/50">
                    <CardContent className="p-0">
                        {loading ? (
                            <div className="flex items-center justify-center p-12"><div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" /></div>
                        ) : filtered.length === 0 ? (
                            <div className="text-center py-12 text-muted-foreground">No tickets found.</div>
                        ) : (
                            <Table>
                                <TableHeader>
                                    <TableRow>
                                        <TableHead>Subject</TableHead>
                                        <TableHead>Category</TableHead>
                                        <TableHead>Priority</TableHead>
                                        <TableHead>Status</TableHead>
                                        <TableHead>Date</TableHead>
                                        <TableHead className="text-right">Actions</TableHead>
                                    </TableRow>
                                </TableHeader>
                                <TableBody>
                                    {filtered.map((ticket) => (
                                        <TableRow key={ticket.id} className="cursor-pointer hover:bg-muted/50" onClick={() => setSelected(ticket)}>
                                            <TableCell className="font-medium max-w-[200px] truncate">{ticket.subject}</TableCell>
                                            <TableCell className="text-muted-foreground capitalize">{ticket.category || "General"}</TableCell>
                                            <TableCell><Badge className={PRIORITY_COLORS[ticket.priority] || PRIORITY_COLORS.medium}>{ticket.priority || "medium"}</Badge></TableCell>
                                            <TableCell><Badge variant="secondary" className={statusColor(ticket.status)}>{ticket.status?.replace(/_/g, " ")}</Badge></TableCell>
                                            <TableCell className="text-xs text-muted-foreground">{formatDate(ticket.created_at)}</TableCell>
                                            <TableCell className="text-right">
                                                <div className="flex justify-end gap-1">
                                                    <Button variant="ghost" size="icon" className="h-8 w-8" onClick={(e) => { e.stopPropagation(); openEdit(ticket); }}><Pencil className="h-4 w-4" /></Button>
                                                    <Button variant="ghost" size="icon" className="h-8 w-8 text-destructive" onClick={(e) => { e.stopPropagation(); handleDelete(ticket.id); }}><Trash2 className="h-4 w-4" /></Button>
                                                </div>
                                            </TableCell>
                                        </TableRow>
                                    ))}
                                </TableBody>
                            </Table>
                        )}
                    </CardContent>
                </Card>

                {/* Detail Panel */}
                <Card className="border-border/50 h-fit">
                    {selected ? (
                        <>
                            <CardHeader className="pb-3">
                                <div className="flex items-center justify-between">
                                    <CardTitle className="text-base">{selected.subject}</CardTitle>
                                    <Badge variant="secondary" className={statusColor(selected.status)}>{selected.status}</Badge>
                                </div>
                                <div className="flex gap-2 mt-1">
                                    <Badge className={PRIORITY_COLORS[selected.priority] || PRIORITY_COLORS.medium}>{selected.priority || "medium"}</Badge>
                                    <span className="text-xs text-muted-foreground">{formatDate(selected.created_at)} · {selected.category || "General"}</span>
                                </div>
                                {selected.user_name && <p className="text-xs text-muted-foreground mt-1">From: {selected.user_name} {selected.user_email && `(${selected.user_email})`}</p>}
                            </CardHeader>
                            <Separator />
                            <CardContent className="pt-4 space-y-4">
                                <div className="rounded-lg bg-muted/50 p-3 text-sm">{selected.message || selected.description || "No message."}</div>

                                {selected.replies?.map((r: any, i: number) => (
                                    <div key={i} className="rounded-lg bg-primary/5 border border-primary/10 p-3 text-sm">
                                        <p className="text-xs text-muted-foreground mb-1">Admin Reply · {formatDate(r.created_at)}</p>
                                        {r.message}
                                    </div>
                                ))}

                                {selected.status !== "closed" && (
                                    <>
                                        <Textarea placeholder="Type a reply..." value={reply} onChange={(e) => setReply(e.target.value)} rows={3} />
                                        <div className="flex gap-2">
                                            <Button className="flex-1" onClick={() => handleReply(selected.id)} disabled={replying || !reply.trim()}>
                                                <Send className="mr-2 h-4 w-4" />{replying ? "Sending..." : "Reply"}
                                            </Button>
                                            <Button variant="outline" onClick={() => handleClose(selected.id)}><CheckCircle className="mr-2 h-4 w-4" />Close</Button>
                                        </div>
                                    </>
                                )}
                            </CardContent>
                        </>
                    ) : (
                        <CardContent className="py-12 text-center text-muted-foreground">
                            <MessageSquare className="mx-auto mb-3 h-8 w-8 opacity-40" />
                            Select a ticket to view details.
                        </CardContent>
                    )}
                </Card>
            </div>

            {/* Create/Edit Ticket Dialog */}
            <Dialog open={dialogOpen} onOpenChange={(open) => { if (!open) { setDialogOpen(false); resetForm(); } }}>
                <DialogContent className="sm:max-w-lg">
                    <DialogHeader><DialogTitle>{editingTicket ? "Edit Ticket" : "Create Ticket"}</DialogTitle></DialogHeader>
                    <div className="space-y-4">
                        <div className="space-y-2">
                            <Label>Subject <span className="text-destructive">*</span></Label>
                            <Input placeholder="Enter ticket subject" value={form.subject} onChange={(e) => setForm({ ...form, subject: e.target.value })} />
                        </div>
                        <div className="grid grid-cols-2 gap-4">
                            <div className="space-y-2">
                                <Label>Category</Label>
                                <Select value={form.category} onValueChange={(v) => setForm({ ...form, category: v })}>
                                    <SelectTrigger><SelectValue /></SelectTrigger>
                                    <SelectContent>
                                        {TICKET_CATEGORIES.map((c) => <SelectItem key={c} value={c} className="capitalize">{c}</SelectItem>)}
                                    </SelectContent>
                                </Select>
                            </div>
                            <div className="space-y-2">
                                <Label>Priority</Label>
                                <Select value={form.priority} onValueChange={(v) => setForm({ ...form, priority: v })}>
                                    <SelectTrigger><SelectValue /></SelectTrigger>
                                    <SelectContent>
                                        {TICKET_PRIORITIES.map((p) => <SelectItem key={p} value={p} className="capitalize">{p}</SelectItem>)}
                                    </SelectContent>
                                </Select>
                            </div>
                        </div>
                        {!editingTicket && (
                            <>
                                <div className="grid grid-cols-2 gap-4">
                                    <div className="space-y-2">
                                        <Label>User Name</Label>
                                        <Input placeholder="Optional" value={form.user_name} onChange={(e) => setForm({ ...form, user_name: e.target.value })} />
                                    </div>
                                    <div className="space-y-2">
                                        <Label>User Email</Label>
                                        <Input placeholder="Optional" value={form.user_email} onChange={(e) => setForm({ ...form, user_email: e.target.value })} />
                                    </div>
                                </div>
                                <div className="space-y-2">
                                    <Label>Message</Label>
                                    <Textarea placeholder="Describe the issue..." value={form.message} onChange={(e) => setForm({ ...form, message: e.target.value })} rows={4} />
                                </div>
                            </>
                        )}
                        <Button className="w-full" onClick={handleSave} disabled={saving}>{saving ? "Saving..." : editingTicket ? "Update Ticket" : "Create Ticket"}</Button>
                    </div>
                </DialogContent>
            </Dialog>
        </div>
    );
}

/* ── FAQs ────────────────────────────────── */
function FaqsTab() {
    const [faqs, setFaqs] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [dialogOpen, setDialogOpen] = useState(false);
    const [editing, setEditing] = useState<any>(null);
    const [form, setForm] = useState({ question: "", answer: "", category: "" });

    const fetchFaqs = () => {
        setLoading(true);
        getFaqs().then(setFaqs).catch(() => {}).finally(() => setLoading(false));
    };

    useEffect(() => { fetchFaqs(); }, []);

    const handleSave = async () => {
        try {
            if (editing) { await updateFaq(editing.id, form); }
            else { await createFaq(form); }
            setDialogOpen(false);
            setEditing(null);
            setForm({ question: "", answer: "", category: "" });
            fetchFaqs();
        } catch {}
    };

    const handleDelete = async (id: string) => {
        if (!confirm("Delete this FAQ?")) return;
        try { await deleteFaq(id); fetchFaqs(); } catch {}
    };

    return (
        <div className="space-y-4">
            <div className="flex justify-end">
                <Button onClick={() => { setEditing(null); setForm({ question: "", answer: "", category: "" }); setDialogOpen(true); }}>
                    <Plus className="mr-2 h-4 w-4" /> Add FAQ
                </Button>
            </div>

            <Card className="border-border/50">
                <CardContent className="p-0">
                    {loading ? (
                        <div className="flex items-center justify-center p-12"><div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" /></div>
                    ) : faqs.length === 0 ? (
                        <div className="text-center py-12 text-muted-foreground">No FAQs yet.</div>
                    ) : (
                        <Table>
                            <TableHeader>
                                <TableRow>
                                    <TableHead>Question</TableHead>
                                    <TableHead>Category</TableHead>
                                    <TableHead className="text-right">Actions</TableHead>
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                {faqs.map((faq) => (
                                    <TableRow key={faq.id}>
                                        <TableCell className="font-medium max-w-[300px] truncate">{faq.question}</TableCell>
                                        <TableCell className="text-muted-foreground">{faq.category || "General"}</TableCell>
                                        <TableCell className="text-right">
                                            <div className="flex justify-end gap-1">
                                                <Button variant="ghost" size="icon" className="h-8 w-8" onClick={() => { setEditing(faq); setForm({ question: faq.question || "", answer: faq.answer || "", category: faq.category || "" }); setDialogOpen(true); }}><Pencil className="h-4 w-4" /></Button>
                                                <Button variant="ghost" size="icon" className="h-8 w-8 text-destructive" onClick={() => handleDelete(faq.id)}><Trash2 className="h-4 w-4" /></Button>
                                            </div>
                                        </TableCell>
                                    </TableRow>
                                ))}
                            </TableBody>
                        </Table>
                    )}
                </CardContent>
            </Card>

            <Dialog open={dialogOpen} onOpenChange={(open) => { if (!open) { setDialogOpen(false); setEditing(null); } }}>
                <DialogContent>
                    <DialogHeader><DialogTitle>{editing ? "Edit" : "Create"} FAQ</DialogTitle></DialogHeader>
                    <div className="space-y-4 pt-2">
                        <div className="space-y-2"><Label>Category</Label><Input value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })} placeholder="Rides, Payments, Safety..." /></div>
                        <div className="space-y-2"><Label>Question</Label><Input value={form.question} onChange={(e) => setForm({ ...form, question: e.target.value })} placeholder="How do I cancel a ride?" /></div>
                        <div className="space-y-2"><Label>Answer</Label><Textarea value={form.answer} onChange={(e) => setForm({ ...form, answer: e.target.value })} placeholder="To cancel a ride..." rows={5} /></div>
                        <Button className="w-full" onClick={handleSave}>{editing ? "Update" : "Create"}</Button>
                    </div>
                </DialogContent>
            </Dialog>
        </div>
    );
}
