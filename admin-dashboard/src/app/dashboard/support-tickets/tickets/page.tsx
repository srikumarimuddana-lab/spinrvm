"use client";

import { useEffect, useState, useCallback } from "react";
import Link from "next/link";
import { getDeskTickets, getDeskAgents } from "@/lib/api";
import { useRequireModule } from "@/hooks/useRequireModule";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
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
import { Inbox, ChevronLeft, ChevronRight, RefreshCw } from "lucide-react";

const PAGE_SIZE = 25;
const STATUSES = ["All", "Open", "On Hold", "Escalated", "Closed"];

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

export default function TicketListPage() {
    const { allowed } = useRequireModule("support_tickets");
    const [tickets, setTickets] = useState<any[]>([]);
    const [agents, setAgents] = useState<any[]>([]);
    const [status, setStatus] = useState("All");
    const [assignee, setAssignee] = useState("all");
    const [page, setPage] = useState(0);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    const load = useCallback(async () => {
        setLoading(true);
        setError(null);
        try {
            const res = await getDeskTickets({
                from: page * PAGE_SIZE + 1,
                limit: PAGE_SIZE,
                status: status === "All" ? undefined : status,
                assigneeId: assignee === "all" ? undefined : assignee,
            });
            setTickets(res.data || []);
        } catch (e: any) {
            setError(e?.message || "Failed to load tickets");
            setTickets([]);
        } finally {
            setLoading(false);
        }
    }, [page, status, assignee]);

    useEffect(() => {
        if (allowed) load();
    }, [allowed, load]);

    useEffect(() => {
        if (!allowed) return;
        getDeskAgents().then((r) => setAgents(r.data || [])).catch(() => {});
    }, [allowed]);

    if (!allowed) return null;

    return (
        <div className="space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
                <h1 className="flex items-center gap-2 text-2xl font-bold">
                    <Inbox className="h-6 w-6" /> Tickets
                </h1>
                <div className="flex flex-wrap items-center gap-2">
                    <Select value={status} onValueChange={(v) => { setPage(0); setStatus(v); }}>
                        <SelectTrigger className="w-36"><SelectValue /></SelectTrigger>
                        <SelectContent>
                            {STATUSES.map((s) => <SelectItem key={s} value={s}>{s}</SelectItem>)}
                        </SelectContent>
                    </Select>
                    <Select value={assignee} onValueChange={(v) => { setPage(0); setAssignee(v); }}>
                        <SelectTrigger className="w-44"><SelectValue placeholder="Assignee" /></SelectTrigger>
                        <SelectContent>
                            <SelectItem value="all">All assignees</SelectItem>
                            {agents.map((a) => (
                                <SelectItem key={a.id} value={a.id}>
                                    {a.firstName || ""} {a.lastName || a.emailId || a.id}
                                </SelectItem>
                            ))}
                        </SelectContent>
                    </Select>
                    <Button variant="outline" size="icon" onClick={load} disabled={loading}>
                        <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
                    </Button>
                </div>
            </div>

            {error && <Card><CardContent className="p-4 text-sm text-red-600">{error}</CardContent></Card>}

            <div className="rounded-lg border">
                <Table>
                    <TableHeader>
                        <TableRow>
                            <TableHead className="w-20">#</TableHead>
                            <TableHead>Subject</TableHead>
                            <TableHead>Requester</TableHead>
                            <TableHead>Priority</TableHead>
                            <TableHead>Assignee</TableHead>
                            <TableHead>Status</TableHead>
                        </TableRow>
                    </TableHeader>
                    <TableBody>
                        {loading && (
                            <TableRow><TableCell colSpan={6} className="py-8 text-center text-muted-foreground">Loading…</TableCell></TableRow>
                        )}
                        {!loading && tickets.length === 0 && (
                            <TableRow><TableCell colSpan={6} className="py-8 text-center text-muted-foreground">No tickets found.</TableCell></TableRow>
                        )}
                        {!loading && tickets.map((t) => (
                            <TableRow key={t.id} className="cursor-pointer">
                                <TableCell>
                                    <Link href={`/dashboard/support-tickets/tickets/${t.id}`} className="font-mono text-sm text-blue-600">
                                        {t.ticketNumber}
                                    </Link>
                                </TableCell>
                                <TableCell className="max-w-xs">
                                    <Link href={`/dashboard/support-tickets/tickets/${t.id}`} className="block truncate font-medium hover:underline">
                                        {t.subject || "(no subject)"}
                                    </Link>
                                </TableCell>
                                <TableCell className="text-sm">{t.contact?.email || t.email || "—"}</TableCell>
                                <TableCell><Badge variant="secondary" className={priorityClass(t.priority)}>{t.priority || "None"}</Badge></TableCell>
                                <TableCell className="text-sm">
                                    {t.assignee ? `${t.assignee.firstName || ""} ${t.assignee.lastName || ""}`.trim() || "—" : "Unassigned"}
                                </TableCell>
                                <TableCell><Badge variant="secondary" className={statusClass(t.status)}>{t.status}</Badge></TableCell>
                            </TableRow>
                        ))}
                    </TableBody>
                </Table>
            </div>

            <div className="flex items-center justify-end gap-2">
                <Button variant="outline" size="sm" onClick={() => setPage((p) => Math.max(0, p - 1))} disabled={page === 0 || loading}>
                    <ChevronLeft className="h-4 w-4" /> Prev
                </Button>
                <span className="text-sm text-muted-foreground">Page {page + 1}</span>
                <Button variant="outline" size="sm" onClick={() => setPage((p) => p + 1)} disabled={tickets.length < PAGE_SIZE || loading}>
                    Next <ChevronRight className="h-4 w-4" />
                </Button>
            </div>
        </div>
    );
}
