"use client";

import { useEffect, useState, useCallback, useMemo } from "react";
import Link from "next/link";
import { getDeskTickets, getDeskAgents, getDeskDepartments, createDeskTicket, searchDeskTickets } from "@/lib/api";
import { useRequireModule } from "@/hooks/useRequireModule";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog";
import { useToast } from "@/components/ui/use-toast";
import { Badge } from "@/components/ui/badge";
import {
    Table,
    TableBody,
    TableCell,
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
import { useTableSort, SortableHead } from "@/components/ui/sortable-table";
import { Inbox, ChevronLeft, ChevronRight, RefreshCw, Search, Plus, X } from "lucide-react";

const STATUSES = ["All", "Open", "On Hold", "Escalated", "Closed"];
const PRIORITIES = ["All", "Low", "Medium", "High", "Urgent"];
const CHANNELS = ["All", "Email", "Web", "Phone", "Chat", "Twitter", "Facebook", "Forums"];
const PAGE_SIZES = [25, 50, 100];

function statusClass(status: string): string {
    const s = (status || "").toLowerCase();
    if (s.includes("open")) return "bg-blue-100 text-blue-800 hover:bg-blue-100";
    if (s.includes("hold")) return "bg-amber-100 text-amber-800 hover:bg-amber-100";
    if (s.includes("escal")) return "bg-red-100 text-red-800 hover:bg-red-100";
    if (s.includes("closed")) return "bg-gray-200 text-gray-700 hover:bg-gray-200";
    return "bg-slate-100 text-slate-700 hover:bg-slate-100";
}

function priorityClass(p: string): string {
    const s = (p || "").toLowerCase();
    if (s === "high" || s === "urgent") return "bg-red-100 text-red-800 hover:bg-red-100";
    if (s === "medium") return "bg-amber-100 text-amber-800 hover:bg-amber-100";
    return "bg-slate-100 text-slate-700 hover:bg-slate-100";
}

function tagNames(t: any): string {
    const tags = t?.tags;
    if (!Array.isArray(tags)) return "";
    return tags.map((x: any) => (typeof x === "string" ? x : x?.name)).filter(Boolean).join(", ");
}

export default function TicketListPage() {
    const { allowed } = useRequireModule("support_tickets");
    const [tickets, setTickets] = useState<any[]>([]);
    const [agents, setAgents] = useState<any[]>([]);

    // Server-side filters (Zoho List Tickets supports these).
    const [status, setStatus] = useState("All");
    const [priority, setPriority] = useState("All");
    const [channel, setChannel] = useState("All");
    const [assignee, setAssignee] = useState("all");

    // Server-side default sort order for the loaded page (newest first); column
    // headers re-sort the loaded rows client-side via useTableSort.
    const sortField = "createdTime";
    const sortDir: "desc" | "asc" = "desc";

    // Pagination.
    const [pageSize, setPageSize] = useState(25);
    const [page, setPage] = useState(0);

    // Search box (server-side, account-wide) + a created-date refine (client).
    const [refine, setRefine] = useState("");
    const [searchQ, setSearchQ] = useState("");
    const [fromDate, setFromDate] = useState("");
    const [toDate, setToDate] = useState("");

    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    // New-ticket dialog.
    const { toast } = useToast();
    const [createOpen, setCreateOpen] = useState(false);
    const [creating, setCreating] = useState(false);
    const [departments, setDepartments] = useState<any[]>([]);
    const [form, setForm] = useState({ subject: "", description: "", email: "", name: "", priority: "Medium", department: "" });
    const setF = (k: string, v: string) => setForm((f) => ({ ...f, [k]: v }));

    const create = async () => {
        if (!form.subject.trim()) return;
        setCreating(true);
        try {
            const [first, ...rest] = form.name.trim().split(" ");
            await createDeskTicket({
                subject: form.subject.trim(),
                description: form.description.trim(),
                email: form.email.trim() || undefined,
                first_name: first || undefined,
                last_name: rest.join(" ") || undefined,
                priority: form.priority,
                channel: "Web",
                department_id: form.department || undefined,
            });
            toast({ title: "Ticket created" });
            setCreateOpen(false);
            setForm({ subject: "", description: "", email: "", name: "", priority: "Medium", department: "" });
            setPage(0);
            load();
        } catch (e: any) {
            toast({ title: "Create failed", description: e?.message, variant: "destructive" });
        } finally {
            setCreating(false);
        }
    };

    const load = useCallback(async () => {
        setLoading(true);
        setError(null);
        try {
            // When a search term is active, query Zoho account-wide; otherwise
            // page the filtered list.
            const res = searchQ
                ? await searchDeskTickets({
                      q: searchQ,
                      from: page * pageSize + 1,
                      limit: pageSize,
                      status: status === "All" ? undefined : status,
                      priority: priority === "All" ? undefined : priority,
                      assigneeId: assignee === "all" ? undefined : assignee,
                  })
                : await getDeskTickets({
                      from: page * pageSize + 1,
                      limit: pageSize,
                      status: status === "All" ? undefined : status,
                      priority: priority === "All" ? undefined : priority,
                      channel: channel === "All" ? undefined : channel,
                      assigneeId: assignee === "all" ? undefined : assignee,
                      sortBy: `${sortDir === "desc" ? "-" : ""}${sortField}`,
                  });
            setTickets(res.data || []);
        } catch (e: any) {
            setError(e?.message || "Failed to load tickets");
            setTickets([]);
        } finally {
            setLoading(false);
        }
    }, [page, pageSize, status, priority, channel, assignee, sortField, sortDir, searchQ]);

    useEffect(() => {
        if (allowed) load();
    }, [allowed, load]);

    useEffect(() => {
        if (!allowed) return;
        getDeskAgents().then((r) => setAgents(r.data || [])).catch(() => {});
        getDeskDepartments().then((r) => {
            const d = r.data || [];
            setDepartments(d);
            if (d.length) setForm((f) => ({ ...f, department: f.department || d[0].id }));
        }).catch(() => {});
    }, [allowed]);

    // Created-date range narrows the loaded results (client-side).
    const shown = useMemo(() => {
        if (!fromDate && !toDate) return tickets;
        return tickets.filter((t) => {
            const created = (t.createdTime || "").slice(0, 10);
            if (fromDate && created && created < fromDate) return false;
            if (toDate && created && created > toDate) return false;
            return true;
        });
    }, [tickets, fromDate, toDate]);

    const { sorted, sort, toggle } = useTableSort(shown);

    const runSearch = () => { setPage(0); setSearchQ(refine.trim()); };
    const clearSearch = () => { setRefine(""); setSearchQ(""); setPage(0); };

    if (!allowed) return null;

    const reset = (fn: (v: string) => void) => (v: string) => { setPage(0); fn(v); };

    return (
        <div className="space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
                <h1 className="flex items-center gap-2 text-2xl font-bold">
                    <Inbox className="h-6 w-6" /> Tickets
                </h1>
                <div className="flex items-center gap-2">
                    <Button onClick={() => setCreateOpen(true)}>
                        <Plus className="mr-2 h-4 w-4" /> New ticket
                    </Button>
                    <Button variant="outline" size="icon" onClick={load} disabled={loading} aria-label="Refresh">
                        <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
                    </Button>
                </div>
            </div>

            <Dialog open={createOpen} onOpenChange={setCreateOpen}>
                <DialogContent>
                    <DialogHeader>
                        <DialogTitle>New ticket</DialogTitle>
                        <DialogDescription>Creates a ticket in Zoho Desk.</DialogDescription>
                    </DialogHeader>
                    <div className="space-y-3">
                        <div className="space-y-1">
                            <Label>Subject *</Label>
                            <Input value={form.subject} onChange={(e) => setF("subject", e.target.value)} />
                        </div>
                        <div className="space-y-1">
                            <Label>Description</Label>
                            <Textarea rows={3} value={form.description} onChange={(e) => setF("description", e.target.value)} />
                        </div>
                        <div className="grid grid-cols-2 gap-3">
                            <div className="space-y-1">
                                <Label>Requester name</Label>
                                <Input value={form.name} onChange={(e) => setF("name", e.target.value)} />
                            </div>
                            <div className="space-y-1">
                                <Label>Requester email</Label>
                                <Input type="email" value={form.email} onChange={(e) => setF("email", e.target.value)} />
                            </div>
                        </div>
                        <div className="grid grid-cols-2 gap-3">
                            <div className="space-y-1">
                                <Label>Department</Label>
                                <Select value={form.department || undefined} onValueChange={(v) => setF("department", v)}>
                                    <SelectTrigger aria-label={departments.length ? "Select" : "No departments"}><SelectValue placeholder={departments.length ? "Select" : "No departments"} /></SelectTrigger>
                                    <SelectContent>
                                        {departments.map((d) => <SelectItem key={d.id} value={d.id}>{d.name}</SelectItem>)}
                                    </SelectContent>
                                </Select>
                            </div>
                            <div className="space-y-1">
                                <Label>Priority</Label>
                                <Select value={form.priority} onValueChange={(v) => setF("priority", v)}>
                                    <SelectTrigger aria-label="Priority"><SelectValue /></SelectTrigger>
                                    <SelectContent>{PRIORITIES.filter((p) => p !== "All").map((p) => <SelectItem key={p} value={p}>{p}</SelectItem>)}</SelectContent>
                                </Select>
                            </div>
                        </div>
                    </div>
                    <DialogFooter>
                        <Button variant="outline" onClick={() => setCreateOpen(false)}>Cancel</Button>
                        <Button onClick={create} disabled={creating || !form.subject.trim()}>
                            {creating ? "Creating…" : "Create ticket"}
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>

            {/* Filters */}
            <div className="flex flex-wrap items-center gap-2">
                <Select value={status} onValueChange={reset(setStatus)}>
                    <SelectTrigger className="w-32" aria-label="Status"><SelectValue placeholder="Status" /></SelectTrigger>
                    <SelectContent>{STATUSES.map((s) => <SelectItem key={s} value={s}>{s === "All" ? "All status" : s}</SelectItem>)}</SelectContent>
                </Select>
                <Select value={priority} onValueChange={reset(setPriority)}>
                    <SelectTrigger className="w-32" aria-label="Priority"><SelectValue placeholder="Priority" /></SelectTrigger>
                    <SelectContent>{PRIORITIES.map((p) => <SelectItem key={p} value={p}>{p === "All" ? "All priority" : p}</SelectItem>)}</SelectContent>
                </Select>
                <Select value={channel} onValueChange={reset(setChannel)}>
                    <SelectTrigger className="w-32" aria-label="Channel"><SelectValue placeholder="Channel" /></SelectTrigger>
                    <SelectContent>{CHANNELS.map((c) => <SelectItem key={c} value={c}>{c === "All" ? "All channels" : c}</SelectItem>)}</SelectContent>
                </Select>
                <Select value={assignee} onValueChange={reset(setAssignee)}>
                    <SelectTrigger className="w-44" aria-label="Assignee"><SelectValue placeholder="Assignee" /></SelectTrigger>
                    <SelectContent>
                        <SelectItem value="all">All assignees</SelectItem>
                        {agents.map((a) => (
                            <SelectItem key={a.id} value={a.id}>
                                {`${a.firstName || ""} ${a.lastName || ""}`.trim() || a.emailId || a.id}
                            </SelectItem>
                        ))}
                    </SelectContent>
                </Select>
                <div className="flex items-center gap-1">
                    <div className="relative">
                        <Search className="absolute left-2 top-2.5 h-4 w-4 text-muted-foreground" />
                        <Input
                            value={refine}
                            onChange={(e) => setRefine(e.target.value)}
                            onKeyDown={(e) => { if (e.key === "Enter") runSearch(); }}
                            placeholder="Search all tickets: # / subject / contact…"
                            className="w-64 pl-8"
                        />
                    </div>
                    <Button variant="secondary" size="sm" onClick={runSearch} disabled={!refine.trim()}>Search</Button>
                    {searchQ && (
                        <Badge variant="secondary" className="gap-1">
                            “{searchQ}”
                            <button onClick={clearSearch}><X className="h-3 w-3" /></button>
                        </Badge>
                    )}
                </div>
                <div className="flex items-center gap-1 text-sm text-muted-foreground">
                    <span>Created</span>
                    <Input type="date" value={fromDate} onChange={(e) => setFromDate(e.target.value)} className="w-40" aria-label="Start date" />
                    <span>→</span>
                    <Input type="date" value={toDate} onChange={(e) => setToDate(e.target.value)} className="w-40" aria-label="End date" />
                    {(fromDate || toDate) && (
                        <Button variant="ghost" size="sm" onClick={() => { setFromDate(""); setToDate(""); }}>Clear</Button>
                    )}
                </div>
            </div>

            {error && <Card><CardContent className="p-4 text-sm text-destructive">{error}</CardContent></Card>}

            <div className="rounded-lg border">
                <Table>
                    <TableHeader>
                        <TableRow>
                            <SortableHead column="ticketNumber" sort={sort} onSort={toggle} className="w-20">#</SortableHead>
                            <SortableHead column="subject" sort={sort} onSort={toggle}>Subject</SortableHead>
                            <SortableHead column="contact.email" sort={sort} onSort={toggle}>Requester</SortableHead>
                            <SortableHead column="category" sort={sort} onSort={toggle}>Category</SortableHead>
                            <SortableHead column="priority" sort={sort} onSort={toggle}>Priority</SortableHead>
                            <SortableHead column="assignee.firstName" sort={sort} onSort={toggle}>Assignee</SortableHead>
                            <SortableHead column="createdTime" sort={sort} onSort={toggle}>Created</SortableHead>
                            <SortableHead column="status" sort={sort} onSort={toggle}>Status</SortableHead>
                        </TableRow>
                    </TableHeader>
                    <TableBody>
                        {loading && (
                            <TableRow><TableCell colSpan={8} className="py-8 text-center text-muted-foreground">Loading…</TableCell></TableRow>
                        )}
                        {!loading && shown.length === 0 && (
                            <TableRow><TableCell colSpan={8} className="py-8 text-center text-muted-foreground">No tickets found.</TableCell></TableRow>
                        )}
                        {!loading && sorted.map((t) => (
                            <TableRow key={t.id} className="cursor-pointer">
                                <TableCell>
                                    <Link href={`/dashboard/support-tickets/tickets/${t.id}`} className="font-mono text-sm text-blue-600 dark:text-blue-400">
                                        {t.ticketNumber}
                                    </Link>
                                </TableCell>
                                <TableCell className="max-w-xs">
                                    <Link href={`/dashboard/support-tickets/tickets/${t.id}`} className="block truncate font-medium hover:underline">
                                        {t.subject || "(no subject)"}
                                    </Link>
                                    {tagNames(t) && <p className="truncate text-xs text-muted-foreground">{tagNames(t)}</p>}
                                </TableCell>
                                <TableCell className="text-sm">{t.contact?.email || t.email || "—"}</TableCell>
                                <TableCell className="text-sm">{t.category || "—"}</TableCell>
                                <TableCell><Badge variant="secondary" className={priorityClass(t.priority)}>{t.priority || "None"}</Badge></TableCell>
                                <TableCell className="text-sm">
                                    {t.assignee ? `${t.assignee.firstName || ""} ${t.assignee.lastName || ""}`.trim() || "—" : "Unassigned"}
                                </TableCell>
                                <TableCell className="whitespace-nowrap text-xs text-muted-foreground">
                                    {t.createdTime?.slice(0, 10) || "—"}
                                </TableCell>
                                <TableCell><Badge variant="secondary" className={statusClass(t.status)}>{t.status}</Badge></TableCell>
                            </TableRow>
                        ))}
                    </TableBody>
                </Table>
            </div>

            {/* Pagination + page size */}
            <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex items-center gap-2 text-sm text-muted-foreground">
                    <span>Rows per page</span>
                    <Select value={String(pageSize)} onValueChange={(v) => { setPage(0); setPageSize(Number(v)); }}>
                        <SelectTrigger className="w-20" aria-label="Rows per page"><SelectValue /></SelectTrigger>
                        <SelectContent>{PAGE_SIZES.map((n) => <SelectItem key={n} value={String(n)}>{n}</SelectItem>)}</SelectContent>
                    </Select>
                    {refine && <span>· {shown.length} of {tickets.length} shown</span>}
                </div>
                <div className="flex items-center gap-2">
                    <Button variant="outline" size="sm" onClick={() => setPage((p) => Math.max(0, p - 1))} disabled={page === 0 || loading}>
                        <ChevronLeft className="h-4 w-4" /> Prev
                    </Button>
                    <span className="text-sm text-muted-foreground">Page {page + 1}</span>
                    <Button variant="outline" size="sm" onClick={() => setPage((p) => p + 1)} disabled={tickets.length < pageSize || loading}>
                        Next <ChevronRight className="h-4 w-4" />
                    </Button>
                </div>
            </div>
        </div>
    );
}
