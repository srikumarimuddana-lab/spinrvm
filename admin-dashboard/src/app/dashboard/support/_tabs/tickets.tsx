"use client";

import { useCallback, useEffect, useRef, useState } from "react";
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
import { useTableSort, SortableHead } from "@/components/ui/sortable-table";
import { Pagination } from "@/components/ui/pagination";
import { MessageSquare, CheckCircle, Plus, Trash2, Pencil, Send, Search, RefreshCw, HelpCircle } from "lucide-react";
import { useServiceAreas, ServiceAreaFilter, ServiceAreaSelect } from "../_components/service-area-select";
import { useToast } from "@/components/ui/use-toast";
import {
    AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent,
    AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from "@/components/ui/alert-dialog";

const PAGE_SIZE = 50;

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
    const { toast } = useToast();
    const [deleteTarget, setDeleteTarget] = useState<string | null>(null);
    const { areas } = useServiceAreas();
    const [tickets, setTickets] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [selected, setSelected] = useState<any>(null);
    const [reply, setReply] = useState("");
    const [replying, setReplying] = useState(false);
    const [search, setSearch] = useState("");
    const [statusFilter, setStatusFilter] = useState("all");
    const [areaFilter, setAreaFilter] = useState("all");
    const [dialogOpen, setDialogOpen] = useState(false);
    const [editing, setEditing] = useState<any>(null);
    const [saving, setSaving] = useState(false);
    const [form, setForm] = useState({ subject: "", category: "general", priority: "medium", message: "", user_name: "", user_email: "", service_area_id: "" });
    const [page, setPage] = useState(0);
    const [hasNextPage, setHasNextPage] = useState(false);
    const reqIdRef = useRef(0);

    const load = useCallback(() => {
        setLoading(true);
        const reqId = ++reqIdRef.current;
        const opts: any = { limit: PAGE_SIZE + 1, offset: page * PAGE_SIZE };
        if (statusFilter !== "all") opts.status = statusFilter;
        if (areaFilter !== "all") opts.service_area_id = areaFilter;
        getTickets(opts)
            .then((rows) => {
                if (reqId !== reqIdRef.current) return;
                const arr = Array.isArray(rows) ? rows : [];
                setHasNextPage(arr.length > PAGE_SIZE);
                setTickets(arr.slice(0, PAGE_SIZE));
            })
            .catch(() => { if (reqId === reqIdRef.current) { setTickets([]); setHasNextPage(false); } })
            .finally(() => { if (reqId === reqIdRef.current) setLoading(false); });
    }, [page, statusFilter, areaFilter]);
    useEffect(() => { load(); }, [load]);
    useEffect(() => { setPage(0); }, [statusFilter, areaFilter]);

    // Search filters the current page client-side; full-text server search is not implemented.
    const filtered = tickets.filter((t) => {
        if (!search) return true;
        const q = search.toLowerCase();
        return t.subject?.toLowerCase().includes(q) || t.user_name?.toLowerCase().includes(q);
    });
    const { sorted, sort, toggle } = useTableSort(filtered);

    const reset = () => { setForm({ subject: "", category: "general", priority: "medium", message: "", user_name: "", user_email: "", service_area_id: "" }); setEditing(null); };

    const handleSave = async () => {
        if (!form.subject.trim()) { toast({ title: "Missing subject", variant: "destructive" }); return; }
        setSaving(true);
        try {
            if (editing) await updateTicket(editing.id, { subject: form.subject, category: form.category, priority: form.priority, service_area_id: form.service_area_id || null });
            else await createTicket({ ...form, service_area_id: form.service_area_id || null });
            toast({ title: editing ? "Ticket updated" : "Ticket created" });
            setDialogOpen(false); reset(); load();
        } catch (e: any) { toast({ title: "Failed to save ticket", description: e.message, variant: "destructive" }); } finally { setSaving(false); }
    };

    const areaName = (id: string) => areas.find((a) => a.id === id)?.name || "";

    return (
        <div className="space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="flex items-center gap-2 flex-1 flex-wrap">
                    <div className="relative flex-1 max-w-xs"><Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" /><Input placeholder="Search..." value={search} onChange={(e) => setSearch(e.target.value)} className="pl-9 h-9" /></div>
                    <Select value={statusFilter} onValueChange={setStatusFilter}><SelectTrigger className="w-32 h-9" aria-label="Filter by status"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="all">All Status</SelectItem><SelectItem value="open">Open</SelectItem><SelectItem value="in_progress">In Progress</SelectItem><SelectItem value="closed">Closed</SelectItem></SelectContent></Select>
                    <ServiceAreaFilter value={areaFilter} onChange={setAreaFilter} areas={areas} />
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
                    : <Table><TableHeader><TableRow><SortableHead column="subject" sort={sort} onSort={toggle}>Subject</SortableHead><SortableHead column="category" sort={sort} onSort={toggle}>Category</SortableHead><SortableHead column="service_area_id" sort={sort} onSort={toggle}>Area</SortableHead><SortableHead column="priority" sort={sort} onSort={toggle}>Priority</SortableHead><SortableHead column="status" sort={sort} onSort={toggle}>Status</SortableHead><SortableHead column="created_at" sort={sort} onSort={toggle}>Date</SortableHead><TableHead className="text-right">Actions</TableHead></TableRow></TableHeader>
                        <TableBody>{sorted.map((t) => (
                            <TableRow key={t.id} className="cursor-pointer hover:bg-muted/50" onClick={() => setSelected(t)}>
                                <TableCell className="font-medium max-w-[160px] truncate text-sm">{t.subject}</TableCell>
                                <TableCell className="text-xs text-muted-foreground capitalize">{t.category || "general"}</TableCell>
                                <TableCell className="text-xs text-muted-foreground">{areaName(t.service_area_id) || "—"}</TableCell>
                                <TableCell><Badge className={`text-[10px] ${P_COLORS[t.priority] || P_COLORS.medium}`}>{t.priority || "medium"}</Badge></TableCell>
                                <TableCell><Badge variant="secondary" className={`text-[10px] ${statusColor(t.status)}`}>{t.status?.replace(/_/g, " ")}</Badge></TableCell>
                                <TableCell className="text-[10px] text-muted-foreground">{formatDate(t.created_at)}</TableCell>
                                <TableCell className="text-right"><div className="flex justify-end gap-0.5">
                                    <Button variant="ghost" size="icon" className="h-7 w-7" onClick={(e) => { e.stopPropagation(); setEditing(t); setForm({ subject: t.subject || "", category: t.category || "general", priority: t.priority || "medium", message: t.message || "", user_name: t.user_name || "", user_email: t.user_email || "", service_area_id: t.service_area_id || "" }); setDialogOpen(true); }}><Pencil className="h-3.5 w-3.5" /></Button>
                                    <Button variant="ghost" size="icon" className="h-7 w-7 text-destructive" onClick={(e) => { e.stopPropagation(); setDeleteTarget(t.id); }}><Trash2 className="h-3.5 w-3.5" /></Button>
                                </div></TableCell>
                            </TableRow>
                        ))}</TableBody></Table>}
                </CardContent>
                <Pagination page={page} pageSize={PAGE_SIZE} hasNextPage={hasNextPage} onPageChange={setPage} />
                </Card>

                <Card className="h-fit">
                    {selected ? (
                        <>
                            <CardHeader className="pb-2">
                                <div className="flex items-center justify-between"><CardTitle className="text-sm">{selected.subject}</CardTitle><Badge variant="secondary" className={`text-[10px] ${statusColor(selected.status)}`}>{selected.status}</Badge></div>
                                <div className="flex flex-wrap gap-1.5 mt-1">
                                    <Badge className={`text-[10px] ${P_COLORS[selected.priority] || P_COLORS.medium}`}>{selected.priority || "medium"}</Badge>
                                    <span className="text-[10px] text-muted-foreground">{selected.category || "General"} · {formatDate(selected.created_at)}</span>
                                    {areaName(selected.service_area_id) && <Badge variant="outline" className="text-[10px]">{areaName(selected.service_area_id)}</Badge>}
                                </div>
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
                        <ServiceAreaSelect value={form.service_area_id} onChange={(v) => setForm({ ...form, service_area_id: v })} areas={areas} />
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

            <AlertDialog open={!!deleteTarget} onOpenChange={(open) => { if (!open) setDeleteTarget(null); }}>
                <AlertDialogContent>
                    <AlertDialogHeader>
                        <AlertDialogTitle>Delete ticket?</AlertDialogTitle>
                        <AlertDialogDescription>This cannot be undone.</AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                        <AlertDialogCancel>Cancel</AlertDialogCancel>
                        <AlertDialogAction onClick={() => { if (deleteTarget) { deleteTicket(deleteTarget).then(load); if (selected?.id === deleteTarget) setSelected(null); setDeleteTarget(null); } }} className="bg-red-600 hover:bg-red-700">Delete</AlertDialogAction>
                    </AlertDialogFooter>
                </AlertDialogContent>
            </AlertDialog>
        </div>
    );
}

function FaqsList() {
    const [faqDeleteTarget, setFaqDeleteTarget] = useState<string | null>(null);
    const [faqs, setFaqs] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [dialogOpen, setDialogOpen] = useState(false);
    const [editing, setEditing] = useState<any>(null);
    const [form, setForm] = useState({ question: "", answer: "", category: "", audience: "both" });

    const load = () => { setLoading(true); getFaqs().then(setFaqs).catch(() => setFaqs([])).finally(() => setLoading(false)); };
    useEffect(() => { load(); }, []);

    const handleSave = async () => { try { if (editing) await updateFaq(editing.id, form); else await createFaq(form); setDialogOpen(false); setEditing(null); setForm({ question: "", answer: "", category: "", audience: "both" }); load(); } catch {} };

    const { sorted, sort, toggle } = useTableSort(faqs);

    return (
        <div className="space-y-4">
            <div className="flex justify-end"><Button size="sm" onClick={() => { setEditing(null); setForm({ question: "", answer: "", category: "", audience: "both" }); setDialogOpen(true); }}><Plus className="mr-1.5 h-3.5 w-3.5" />Add FAQ</Button></div>
            <Card><CardContent className="p-0">
                {loading ? <div className="flex justify-center p-12"><div className="h-7 w-7 animate-spin rounded-full border-2 border-primary border-t-transparent" /></div>
                : faqs.length === 0 ? <div className="text-center py-12 text-muted-foreground text-sm">No FAQs yet.</div>
                : <Table><TableHeader><TableRow><SortableHead column="question" sort={sort} onSort={toggle}>Question</SortableHead><SortableHead column="category" sort={sort} onSort={toggle}>Category</SortableHead><TableHead className="text-right">Actions</TableHead></TableRow></TableHeader>
                    <TableBody>{sorted.map((f) => (
                        <TableRow key={f.id}><TableCell className="font-medium max-w-[280px] truncate text-sm">{f.question}</TableCell><TableCell className="text-xs text-muted-foreground">{f.category || "General"}</TableCell>
                            <TableCell className="text-right"><div className="flex justify-end gap-0.5">
                                <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => { setEditing(f); setForm({ question: f.question || "", answer: f.answer || "", category: f.category || "", audience: f.audience || "both" }); setDialogOpen(true); }}><Pencil className="h-3.5 w-3.5" /></Button>
                                <Button variant="ghost" size="icon" className="h-7 w-7 text-destructive" onClick={() => setFaqDeleteTarget(f.id)}><Trash2 className="h-3.5 w-3.5" /></Button>
                            </div></TableCell>
                        </TableRow>
                    ))}</TableBody></Table>}
            </CardContent></Card>
            <Dialog open={dialogOpen} onOpenChange={(o) => { if (!o) { setDialogOpen(false); setEditing(null); } }}>
                <DialogContent className="sm:max-w-md"><DialogHeader><DialogTitle className="text-base">{editing ? "Edit" : "Create"} FAQ</DialogTitle></DialogHeader>
                    <div className="space-y-3">
                        <div className="space-y-1.5"><Label className="text-xs">Category</Label><Input value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })} placeholder="Rides, Payments..." /></div>
                        <div className="space-y-1.5"><Label className="text-xs">Audience</Label><Select value={form.audience} onValueChange={(v) => setForm({ ...form, audience: v })}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="both">Both apps</SelectItem><SelectItem value="rider">Rider app</SelectItem><SelectItem value="driver">Driver app</SelectItem></SelectContent></Select></div>
                        <div className="space-y-1.5"><Label className="text-xs">Question</Label><Input value={form.question} onChange={(e) => setForm({ ...form, question: e.target.value })} placeholder="How do I...?" /></div>
                        <div className="space-y-1.5"><Label className="text-xs">Answer</Label><Textarea value={form.answer} onChange={(e) => setForm({ ...form, answer: e.target.value })} placeholder="Answer..." rows={4} /></div>
                        <Button className="w-full" size="sm" onClick={handleSave}>{editing ? "Update" : "Create"}</Button>
                    </div>
                </DialogContent>
            </Dialog>

            <AlertDialog open={!!faqDeleteTarget} onOpenChange={(open) => { if (!open) setFaqDeleteTarget(null); }}>
                <AlertDialogContent>
                    <AlertDialogHeader>
                        <AlertDialogTitle>Delete FAQ?</AlertDialogTitle>
                        <AlertDialogDescription>This cannot be undone.</AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                        <AlertDialogCancel>Cancel</AlertDialogCancel>
                        <AlertDialogAction onClick={() => { if (faqDeleteTarget) deleteFaq(faqDeleteTarget).then(load).finally(() => setFaqDeleteTarget(null)); }} className="bg-red-600 hover:bg-red-700">Delete</AlertDialogAction>
                    </AlertDialogFooter>
                </AlertDialogContent>
            </AlertDialog>
        </div>
    );
}
