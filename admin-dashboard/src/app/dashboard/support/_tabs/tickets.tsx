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
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { MessageSquare, CheckCircle, Plus, Trash2, Pencil, Send, Search, RefreshCw, HelpCircle, Clock, Inbox } from "lucide-react";

const CATEGORIES = ["general", "rides", "payments", "account", "safety", "driver", "technical"];
const PRIORITIES = ["low", "medium", "high", "urgent"];
const P_COLORS: Record<string, string> = { low: "bg-zinc-500/15 text-zinc-600", medium: "bg-blue-500/15 text-blue-600", high: "bg-amber-500/15 text-amber-600", urgent: "bg-red-500/15 text-red-600" };

export default function TicketsTab() {
    const [sub, setSub] = useState<"tickets" | "faqs">("tickets");
    return (
        <div className="space-y-4">
            <div className="flex gap-1 border-b -mt-1">
                {[{ k: "tickets", l: "Tickets", i: MessageSquare }, { k: "faqs", l: "FAQs", i: HelpCircle }].map((t) => (
                    <button key={t.k} onClick={() => setSub(t.k as any)} className={`flex items-center gap-1.5 px-4 py-2 text-sm font-medium border-b-2 -mb-px ${sub === t.k ? "border-primary text-primary" : "border-transparent text-muted-foreground hover:text-foreground"}`}>
                        <t.i className="h-3.5 w-3.5" />{t.l}
                    </button>
                ))}
            </div>
            {sub === "tickets" ? <TicketsList /> : <FaqsList />}
        </div>
    );
}

function TicketsList() {
    const [tickets, setTickets] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [selected, setSelected] = useState<any>(null);
    const [reply, setReply] = useState("");
    const [replying, setReplying] = useState(false);
    const [search, setSearch] = useState("");
    const [statusFilter, setStatusFilter] = useState("all");
    const [dialogOpen, setDialogOpen] = useState(false);
    const [editing, setEditing] = useState<any>(null);
    const [saving, setSaving] = useState(false);
    const [form, setForm] = useState({ subject: "", category: "general", priority: "medium", message: "", user_name: "", user_email: "" });

    const load = () => { setLoading(true); getTickets().then(setTickets).catch(() => setTickets([])).finally(() => setLoading(false)); };
    useEffect(() => { load(); }, []);

    const stats = { total: tickets.length, open: tickets.filter((t) => t.status === "open" || t.status === "in_progress").length, closed: tickets.filter((t) => t.status === "closed").length };
    const filtered = tickets.filter((t) => {
        const ms = !search || t.subject?.toLowerCase().includes(search.toLowerCase()) || t.user_name?.toLowerCase().includes(search.toLowerCase());
        return ms && (statusFilter === "all" || t.status === statusFilter);
    });

    const reset = () => { setForm({ subject: "", category: "general", priority: "medium", message: "", user_name: "", user_email: "" }); setEditing(null); };

    const handleSave = async () => {
        if (!form.subject.trim()) { alert("Enter a subject."); return; }
        setSaving(true);
        try {
            if (editing) await updateTicket(editing.id, { subject: form.subject, category: form.category, priority: form.priority });
            else await createTicket(form);
            setDialogOpen(false); reset(); load();
        } catch (e: any) { alert(e.message); } finally { setSaving(false); }
    };

    return (
        <div className="space-y-4">
            <div className="grid grid-cols-3 gap-3">
                <Card><CardContent className="pt-3 pb-2"><div className="flex items-center gap-2"><Inbox className="h-4 w-4 text-violet-500" /><div><p className="text-[10px] text-muted-foreground">Total</p><p className="text-xl font-bold">{stats.total}</p></div></div></CardContent></Card>
                <Card><CardContent className="pt-3 pb-2"><div className="flex items-center gap-2"><Clock className="h-4 w-4 text-amber-500" /><div><p className="text-[10px] text-muted-foreground">Open</p><p className="text-xl font-bold">{stats.open}</p></div></div></CardContent></Card>
                <Card><CardContent className="pt-3 pb-2"><div className="flex items-center gap-2"><CheckCircle className="h-4 w-4 text-emerald-500" /><div><p className="text-[10px] text-muted-foreground">Closed</p><p className="text-xl font-bold">{stats.closed}</p></div></div></CardContent></Card>
            </div>

            <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="flex items-center gap-2 flex-1">
                    <div className="relative flex-1 max-w-xs"><Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" /><Input placeholder="Search..." value={search} onChange={(e) => setSearch(e.target.value)} className="pl-9 h-9" /></div>
                    <Select value={statusFilter} onValueChange={setStatusFilter}><SelectTrigger className="w-32 h-9"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="all">All</SelectItem><SelectItem value="open">Open</SelectItem><SelectItem value="in_progress">In Progress</SelectItem><SelectItem value="closed">Closed</SelectItem></SelectContent></Select>
                </div>
                <div className="flex gap-2">
                    <Button variant="outline" size="sm" onClick={load}><RefreshCw className={`mr-1.5 h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />Refresh</Button>
                    <Button size="sm" onClick={() => { reset(); setDialogOpen(true); }}><Plus className="mr-1.5 h-3.5 w-3.5" />Create Ticket</Button>
                </div>
            </div>

            <div className="grid gap-4 lg:grid-cols-[1fr_380px]">
                <Card><CardContent className="p-0">
                    {loading ? <div className="flex justify-center p-12"><div className="h-7 w-7 animate-spin rounded-full border-2 border-primary border-t-transparent" /></div>
                    : filtered.length === 0 ? <div className="text-center py-12 text-muted-foreground text-sm">No tickets found.</div>
                    : <Table><TableHeader><TableRow><TableHead>Subject</TableHead><TableHead>Category</TableHead><TableHead>Priority</TableHead><TableHead>Status</TableHead><TableHead>Date</TableHead><TableHead className="text-right">Actions</TableHead></TableRow></TableHeader>
                        <TableBody>{filtered.map((t) => (
                            <TableRow key={t.id} className="cursor-pointer hover:bg-muted/50" onClick={() => setSelected(t)}>
                                <TableCell className="font-medium max-w-[180px] truncate text-sm">{t.subject}</TableCell>
                                <TableCell className="text-xs text-muted-foreground capitalize">{t.category || "general"}</TableCell>
                                <TableCell><Badge className={`text-[10px] ${P_COLORS[t.priority] || P_COLORS.medium}`}>{t.priority || "medium"}</Badge></TableCell>
                                <TableCell><Badge variant="secondary" className={`text-[10px] ${statusColor(t.status)}`}>{t.status?.replace(/_/g, " ")}</Badge></TableCell>
                                <TableCell className="text-[10px] text-muted-foreground">{formatDate(t.created_at)}</TableCell>
                                <TableCell className="text-right"><div className="flex justify-end gap-0.5">
                                    <Button variant="ghost" size="icon" className="h-7 w-7" onClick={(e) => { e.stopPropagation(); setEditing(t); setForm({ subject: t.subject || "", category: t.category || "general", priority: t.priority || "medium", message: t.message || "", user_name: t.user_name || "", user_email: t.user_email || "" }); setDialogOpen(true); }}><Pencil className="h-3.5 w-3.5" /></Button>
                                    <Button variant="ghost" size="icon" className="h-7 w-7 text-destructive" onClick={(e) => { e.stopPropagation(); if (confirm("Delete?")) { deleteTicket(t.id).then(load).catch(() => {}); if (selected?.id === t.id) setSelected(null); } }}><Trash2 className="h-3.5 w-3.5" /></Button>
                                </div></TableCell>
                            </TableRow>
                        ))}</TableBody></Table>}
                </CardContent></Card>

                <Card className="h-fit">
                    {selected ? (
                        <>
                            <CardHeader className="pb-2">
                                <div className="flex items-center justify-between"><CardTitle className="text-sm">{selected.subject}</CardTitle><Badge variant="secondary" className={`text-[10px] ${statusColor(selected.status)}`}>{selected.status}</Badge></div>
                                <div className="flex gap-1.5 mt-1"><Badge className={`text-[10px] ${P_COLORS[selected.priority] || P_COLORS.medium}`}>{selected.priority || "medium"}</Badge><span className="text-[10px] text-muted-foreground">{selected.category || "General"} · {formatDate(selected.created_at)}</span></div>
                                {selected.user_name && <p className="text-[10px] text-muted-foreground mt-1">From: {selected.user_name}</p>}
                            </CardHeader>
                            <Separator />
                            <CardContent className="pt-3 space-y-3">
                                <div className="rounded-lg bg-muted/50 p-2.5 text-xs">{selected.message || selected.description || "No message."}</div>
                                {selected.replies?.map((r: any, i: number) => (
                                    <div key={i} className="rounded-lg bg-primary/5 border border-primary/10 p-2.5 text-xs"><p className="text-[10px] text-muted-foreground mb-1">Admin · {formatDate(r.created_at)}</p>{r.message}</div>
                                ))}
                                {selected.status !== "closed" && (
                                    <>
                                        <Textarea placeholder="Type a reply..." value={reply} onChange={(e) => setReply(e.target.value)} rows={2} className="text-xs" />
                                        <div className="flex gap-2">
                                            <Button size="sm" className="flex-1" onClick={() => { if (reply.trim()) { setReplying(true); replyToTicket(selected.id, reply.trim()).then(() => { setReply(""); load(); }).finally(() => setReplying(false)); } }} disabled={replying || !reply.trim()}><Send className="mr-1.5 h-3.5 w-3.5" />{replying ? "..." : "Reply"}</Button>
                                            <Button size="sm" variant="outline" onClick={() => { closeTicket(selected.id).then(() => { setSelected(null); load(); }); }}><CheckCircle className="mr-1.5 h-3.5 w-3.5" />Close</Button>
                                        </div>
                                    </>
                                )}
                            </CardContent>
                        </>
                    ) : <CardContent className="py-10 text-center text-muted-foreground text-sm"><MessageSquare className="mx-auto mb-2 h-7 w-7 opacity-40" />Select a ticket</CardContent>}
                </Card>
            </div>

            <Dialog open={dialogOpen} onOpenChange={(o) => { if (!o) { setDialogOpen(false); reset(); } }}>
                <DialogContent className="sm:max-w-md"><DialogHeader><DialogTitle className="text-base">{editing ? "Edit Ticket" : "Create Ticket"}</DialogTitle></DialogHeader>
                    <div className="space-y-3">
                        <div className="space-y-1.5"><Label className="text-xs">Subject *</Label><Input placeholder="Issue title" value={form.subject} onChange={(e) => setForm({ ...form, subject: e.target.value })} /></div>
                        <div className="grid grid-cols-2 gap-3">
                            <div className="space-y-1.5"><Label className="text-xs">Category</Label><Select value={form.category} onValueChange={(v) => setForm({ ...form, category: v })}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent>{CATEGORIES.map((c) => <SelectItem key={c} value={c} className="capitalize">{c}</SelectItem>)}</SelectContent></Select></div>
                            <div className="space-y-1.5"><Label className="text-xs">Priority</Label><Select value={form.priority} onValueChange={(v) => setForm({ ...form, priority: v })}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent>{PRIORITIES.map((p) => <SelectItem key={p} value={p} className="capitalize">{p}</SelectItem>)}</SelectContent></Select></div>
                        </div>
                        {!editing && (<>
                            <div className="grid grid-cols-2 gap-3">
                                <div className="space-y-1.5"><Label className="text-xs">User Name</Label><Input placeholder="Optional" value={form.user_name} onChange={(e) => setForm({ ...form, user_name: e.target.value })} /></div>
                                <div className="space-y-1.5"><Label className="text-xs">User Email</Label><Input placeholder="Optional" value={form.user_email} onChange={(e) => setForm({ ...form, user_email: e.target.value })} /></div>
                            </div>
                            <div className="space-y-1.5"><Label className="text-xs">Message</Label><Textarea placeholder="Describe the issue..." value={form.message} onChange={(e) => setForm({ ...form, message: e.target.value })} rows={3} /></div>
                        </>)}
                        <Button className="w-full" size="sm" onClick={handleSave} disabled={saving}>{saving ? "Saving..." : editing ? "Update" : "Create"}</Button>
                    </div>
                </DialogContent>
            </Dialog>
        </div>
    );
}

function FaqsList() {
    const [faqs, setFaqs] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [dialogOpen, setDialogOpen] = useState(false);
    const [editing, setEditing] = useState<any>(null);
    const [form, setForm] = useState({ question: "", answer: "", category: "" });

    const load = () => { setLoading(true); getFaqs().then(setFaqs).catch(() => setFaqs([])).finally(() => setLoading(false)); };
    useEffect(() => { load(); }, []);

    const handleSave = async () => { try { if (editing) await updateFaq(editing.id, form); else await createFaq(form); setDialogOpen(false); setEditing(null); setForm({ question: "", answer: "", category: "" }); load(); } catch {} };

    return (
        <div className="space-y-4">
            <div className="flex justify-end"><Button size="sm" onClick={() => { setEditing(null); setForm({ question: "", answer: "", category: "" }); setDialogOpen(true); }}><Plus className="mr-1.5 h-3.5 w-3.5" />Add FAQ</Button></div>
            <Card><CardContent className="p-0">
                {loading ? <div className="flex justify-center p-12"><div className="h-7 w-7 animate-spin rounded-full border-2 border-primary border-t-transparent" /></div>
                : faqs.length === 0 ? <div className="text-center py-12 text-muted-foreground text-sm">No FAQs yet.</div>
                : <Table><TableHeader><TableRow><TableHead>Question</TableHead><TableHead>Category</TableHead><TableHead className="text-right">Actions</TableHead></TableRow></TableHeader>
                    <TableBody>{faqs.map((f) => (
                        <TableRow key={f.id}><TableCell className="font-medium max-w-[280px] truncate text-sm">{f.question}</TableCell><TableCell className="text-xs text-muted-foreground">{f.category || "General"}</TableCell>
                            <TableCell className="text-right"><div className="flex justify-end gap-0.5">
                                <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => { setEditing(f); setForm({ question: f.question || "", answer: f.answer || "", category: f.category || "" }); setDialogOpen(true); }}><Pencil className="h-3.5 w-3.5" /></Button>
                                <Button variant="ghost" size="icon" className="h-7 w-7 text-destructive" onClick={() => { if (confirm("Delete?")) deleteFaq(f.id).then(load); }}><Trash2 className="h-3.5 w-3.5" /></Button>
                            </div></TableCell>
                        </TableRow>
                    ))}</TableBody></Table>}
            </CardContent></Card>
            <Dialog open={dialogOpen} onOpenChange={(o) => { if (!o) { setDialogOpen(false); setEditing(null); } }}>
                <DialogContent className="sm:max-w-md"><DialogHeader><DialogTitle className="text-base">{editing ? "Edit" : "Create"} FAQ</DialogTitle></DialogHeader>
                    <div className="space-y-3">
                        <div className="space-y-1.5"><Label className="text-xs">Category</Label><Input value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })} placeholder="Rides, Payments..." /></div>
                        <div className="space-y-1.5"><Label className="text-xs">Question</Label><Input value={form.question} onChange={(e) => setForm({ ...form, question: e.target.value })} placeholder="How do I...?" /></div>
                        <div className="space-y-1.5"><Label className="text-xs">Answer</Label><Textarea value={form.answer} onChange={(e) => setForm({ ...form, answer: e.target.value })} placeholder="Answer..." rows={4} /></div>
                        <Button className="w-full" size="sm" onClick={handleSave}>{editing ? "Update" : "Create"}</Button>
                    </div>
                </DialogContent>
            </Dialog>
        </div>
    );
}
