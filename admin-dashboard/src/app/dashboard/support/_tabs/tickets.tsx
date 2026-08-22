"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import {
    getTickets, replyToTicket, closeTicket, createTicket, updateTicket, deleteTicket,
} from "@/lib/api";
import { formatDate, statusColor } from "@/lib/utils";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Alert, AlertDescription } from "@/components/ui/alert";
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
import { MessageSquare, CheckCircle, Plus, Trash2, Pencil, Send, Search, RefreshCw, ExternalLink } from "lucide-react";
import { useServiceAreas, ServiceAreaFilter, ServiceAreaSelect } from "../_components/service-area-select";
import { useToast } from "@/components/ui/use-toast";
import {
    AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent,
    AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from "@/components/ui/alert-dialog";

const PAGE_SIZE = 50;

const CATEGORIES = ["general", "rides", "payments", "account", "safety", "driver", "technical"];
const PRIORITIES = ["low", "medium", "high", "urgent"];
// Verified against real rendered badges via axe, not just token math (this
// map had no dark: variants at all before — 3 of 4 failed WCAG AA in dark
// mode, 3 of 4 also failed in light mode once actually rendered with data,
// since the crawl-audit's empty-mocked pages never exercise this). Light
// shades darkened where needed; dark shades added to match the proven-safe
// pattern already used by the sibling statusColor() above. "high" (amber)
// already passed in dark mode as-is (text-amber-600, no override needed).
// eslint-disable-next-line no-restricted-syntax -- categorical priority ladder (low/medium/high/urgent), not a single success/warning/destructive signal — too many states for the 3-token system, see comment above (#2816)
const P_COLORS: Record<string, string> = { low: "bg-zinc-500/15 text-zinc-600 dark:text-zinc-400", medium: "bg-blue-500/15 text-blue-700 dark:text-blue-400", high: "bg-amber-500/15 text-amber-800 dark:text-amber-600", urgent: "bg-red-500/15 text-red-700 dark:text-red-400" };

// Used to switch between a "tickets" and a "faqs" sub-tab here; the "faqs"
// side was a third, undocumented FAQ implementation (no permission checks,
// no audience labels, no updated_at column) with no product-decision cover
// — unlike the FaqsTab component, which explicitly documents a "point to
// the dedicated page, don't merge" decision. Removed as a plain orphaned
// duplicate (admin portal IA audit, Finding A) rather than folded into that
// decision. Support & Issues already has its own "FAQs" tab for this.
export default function TicketsTab() {
    return <TicketsList />;
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
            {/* Corporate + admin portal review, round 2: "two parallel,
                non-integrated ticketing systems." This in-house tab (getTickets/
                replyToTicket/closeTicket) and the Help Desk (Zoho) integration
                (support-tickets/tickets, getDeskTickets) are genuinely separate
                backends, not the same data behind two UIs like Disputes/FAQs
                were — full consolidation is a real migration, out of scope
                here. Light-touch per product decision: point toward the
                actively-developed system (Zoho Help Desk has its own Trends
                analytics page and dedicated backend module; this one doesn't). */}
            <Alert>
                <ExternalLink className="h-4 w-4" />
                <AlertDescription className="flex items-center justify-between gap-3">
                    <span>Spinr&apos;s primary ticketing system is now the Help Desk (Zoho) integration.</span>
                    <Link href="/dashboard/support-tickets/tickets" className="font-medium underline underline-offset-2 whitespace-nowrap">
                        Open Help Desk
                    </Link>
                </AlertDescription>
            </Alert>

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
                        <AlertDialogAction onClick={() => { if (deleteTarget) { deleteTicket(deleteTarget).then(load); if (selected?.id === deleteTarget) setSelected(null); setDeleteTarget(null); } }} className="bg-destructive text-destructive-foreground hover:bg-destructive/90">Delete</AlertDialogAction>
                    </AlertDialogFooter>
                </AlertDialogContent>
            </AlertDialog>
        </div>
    );
}
