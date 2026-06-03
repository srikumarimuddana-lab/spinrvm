"use client";

import { useEffect, useState, useCallback } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import {
    getDeskTicket,
    getDeskTicketThreads,
    getDeskAgents,
    replyDeskTicket,
    commentDeskTicket,
    updateDeskTicket,
} from "@/lib/api";
import { useRequireModule } from "@/hooks/useRequireModule";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select";
import { useToast } from "@/components/ui/use-toast";
import { ArrowLeft, Send, StickyNote, User, Mail } from "lucide-react";

const STATUSES = ["Open", "On Hold", "Escalated", "Closed"];
const PRIORITIES = ["Low", "Medium", "High", "Urgent"];

function statusClass(status: string): string {
    const s = (status || "").toLowerCase();
    if (s.includes("open")) return "bg-blue-100 text-blue-800 hover:bg-blue-100";
    if (s.includes("hold")) return "bg-amber-100 text-amber-800 hover:bg-amber-100";
    if (s.includes("escal")) return "bg-red-100 text-red-800 hover:bg-red-100";
    if (s.includes("closed")) return "bg-gray-200 text-gray-700 hover:bg-gray-200";
    return "bg-slate-100 text-slate-700 hover:bg-slate-100";
}

/** Strip HTML to plain text. Thread/comment bodies originate from customer
 *  emails and other agents, so we never inject their HTML into the admin DOM
 *  (XSS). We render text only. */
function toText(html: string): string {
    if (!html) return "";
    if (typeof document === "undefined") return html.replace(/<[^>]*>/g, "");
    const el = document.createElement("div");
    el.innerHTML = html;
    return el.textContent || el.innerText || "";
}

export default function TicketDetailPage() {
    const { allowed } = useRequireModule("support_tickets");
    const params = useParams();
    const id = String(params?.id || "");
    const { toast } = useToast();

    const [ticket, setTicket] = useState<any>(null);
    const [thread, setThread] = useState<any[]>([]);
    const [agents, setAgents] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    const [reply, setReply] = useState("");
    const [note, setNote] = useState("");
    const [sending, setSending] = useState(false);
    const [savingField, setSavingField] = useState(false);

    const load = useCallback(async () => {
        setLoading(true);
        setError(null);
        try {
            const [t, th] = await Promise.all([getDeskTicket(id), getDeskTicketThreads(id)]);
            setTicket(t);
            setThread(th.data || []);
        } catch (e: any) {
            setError(e?.message || "Failed to load ticket");
        } finally {
            setLoading(false);
        }
    }, [id]);

    useEffect(() => {
        if (allowed && id) load();
    }, [allowed, id, load]);

    useEffect(() => {
        if (!allowed) return;
        getDeskAgents().then((r) => setAgents(r.data || [])).catch(() => {});
    }, [allowed]);

    const patch = async (fields: Record<string, string>, label: string) => {
        setSavingField(true);
        try {
            await updateDeskTicket(id, fields);
            toast({ title: `${label} updated` });
            await load();
        } catch (e: any) {
            toast({ title: "Update failed", description: e?.message, variant: "destructive" });
        } finally {
            setSavingField(false);
        }
    };

    const sendReply = async () => {
        if (!reply.trim()) return;
        setSending(true);
        try {
            await replyDeskTicket(id, { content: reply, to: ticket?.email || ticket?.contact?.email });
            setReply("");
            toast({ title: "Reply sent" });
            await load();
        } catch (e: any) {
            toast({ title: "Reply failed", description: e?.message, variant: "destructive" });
        } finally {
            setSending(false);
        }
    };

    const sendNote = async () => {
        if (!note.trim()) return;
        setSending(true);
        try {
            await commentDeskTicket(id, { content: note, is_public: false });
            setNote("");
            toast({ title: "Internal note added" });
            await load();
        } catch (e: any) {
            toast({ title: "Note failed", description: e?.message, variant: "destructive" });
        } finally {
            setSending(false);
        }
    };

    if (!allowed) return null;

    return (
        <div className="space-y-4">
            <div className="flex items-center justify-between">
                <Button asChild variant="ghost" size="sm">
                    <Link href="/dashboard/support-tickets/tickets"><ArrowLeft className="mr-2 h-4 w-4" /> Back to tickets</Link>
                </Button>
            </div>

            {loading && <Card><CardContent className="p-6 text-muted-foreground">Loading…</CardContent></Card>}
            {!loading && error && <Card><CardContent className="p-4 text-sm text-red-600">{error}</CardContent></Card>}

            {!loading && ticket && (
                <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
                    {/* Main column: subject + conversation + reply */}
                    <div className="space-y-4 lg:col-span-2">
                        <Card>
                            <CardHeader>
                                <div className="flex items-start justify-between gap-2">
                                    <CardTitle className="text-lg">{ticket.subject || "(no subject)"}</CardTitle>
                                    <Badge variant="secondary" className={statusClass(ticket.status)}>{ticket.status}</Badge>
                                </div>
                                <p className="text-xs text-muted-foreground">
                                    #{ticket.ticketNumber} · created {ticket.createdTime?.slice(0, 16).replace("T", " ")}
                                </p>
                            </CardHeader>
                            <CardContent>
                                <p className="whitespace-pre-wrap text-sm">{toText(ticket.description || "")}</p>
                            </CardContent>
                        </Card>

                        <Card>
                            <CardHeader><CardTitle className="text-base">Conversation</CardTitle></CardHeader>
                            <CardContent className="space-y-3">
                                {thread.length === 0 && <p className="text-sm text-muted-foreground">No replies yet.</p>}
                                {thread.map((m: any, i: number) => {
                                    const isComment = m.type === "comment";
                                    const author = m.author?.name || m.commenter?.name || m.fromEmailAddress || m.from || "—";
                                    const when = (m.createdTime || m.commentedTime || m.summary?.time || "").slice(0, 16).replace("T", " ");
                                    const body = toText(m.content || m.summary || m.plainText || "");
                                    return (
                                        <div key={m.id || i} className={`rounded-lg border p-3 ${isComment ? "bg-amber-50 dark:bg-amber-950/20" : ""}`}>
                                            <div className="mb-1 flex items-center justify-between">
                                                <span className="flex items-center gap-1 text-sm font-medium">
                                                    {isComment ? <StickyNote className="h-3 w-3" /> : <Mail className="h-3 w-3" />}
                                                    {author} {isComment && <span className="text-xs text-amber-700">(internal note)</span>}
                                                </span>
                                                <span className="text-xs text-muted-foreground">{when}</span>
                                            </div>
                                            <p className="whitespace-pre-wrap text-sm">{body}</p>
                                        </div>
                                    );
                                })}
                            </CardContent>
                        </Card>

                        <Card>
                            <CardHeader><CardTitle className="text-base">Reply to requester</CardTitle></CardHeader>
                            <CardContent className="space-y-2">
                                <Textarea rows={4} value={reply} onChange={(e) => setReply(e.target.value)} placeholder="Type your reply…" />
                                <div className="flex justify-end">
                                    <Button onClick={sendReply} disabled={sending || !reply.trim()}>
                                        <Send className="mr-2 h-4 w-4" /> Send reply
                                    </Button>
                                </div>
                            </CardContent>
                        </Card>

                        <Card>
                            <CardHeader><CardTitle className="text-base">Internal note</CardTitle></CardHeader>
                            <CardContent className="space-y-2">
                                <Textarea rows={2} value={note} onChange={(e) => setNote(e.target.value)} placeholder="Add a private note (not sent to the requester)…" />
                                <div className="flex justify-end">
                                    <Button variant="outline" onClick={sendNote} disabled={sending || !note.trim()}>
                                        <StickyNote className="mr-2 h-4 w-4" /> Add note
                                    </Button>
                                </div>
                            </CardContent>
                        </Card>
                    </div>

                    {/* Side column: properties / assign */}
                    <div className="space-y-4">
                        <Card>
                            <CardHeader><CardTitle className="text-base">Requester</CardTitle></CardHeader>
                            <CardContent className="space-y-1 text-sm">
                                <p className="flex items-center gap-2"><User className="h-4 w-4" />
                                    {`${ticket.contact?.firstName || ""} ${ticket.contact?.lastName || ""}`.trim() || "—"}
                                </p>
                                <p className="flex items-center gap-2 text-muted-foreground"><Mail className="h-4 w-4" />
                                    {ticket.contact?.email || ticket.email || "—"}
                                </p>
                            </CardContent>
                        </Card>

                        <Card>
                            <CardHeader><CardTitle className="text-base">Properties</CardTitle></CardHeader>
                            <CardContent className="space-y-3">
                                <div className="space-y-1">
                                    <Label>Status</Label>
                                    <Select value={ticket.status} onValueChange={(v) => patch({ status: v }, "Status")} disabled={savingField}>
                                        <SelectTrigger><SelectValue /></SelectTrigger>
                                        <SelectContent>
                                            {STATUSES.map((s) => <SelectItem key={s} value={s}>{s}</SelectItem>)}
                                        </SelectContent>
                                    </Select>
                                </div>
                                <div className="space-y-1">
                                    <Label>Priority</Label>
                                    <Select value={ticket.priority || ""} onValueChange={(v) => patch({ priority: v }, "Priority")} disabled={savingField}>
                                        <SelectTrigger><SelectValue placeholder="None" /></SelectTrigger>
                                        <SelectContent>
                                            {PRIORITIES.map((p) => <SelectItem key={p} value={p}>{p}</SelectItem>)}
                                        </SelectContent>
                                    </Select>
                                </div>
                                <div className="space-y-1">
                                    <Label>Assignee</Label>
                                    <Select
                                        value={ticket.assigneeId || ticket.assignee?.id || ""}
                                        onValueChange={(v) => patch({ assigneeId: v }, "Assignee")}
                                        disabled={savingField}
                                    >
                                        <SelectTrigger><SelectValue placeholder="Unassigned" /></SelectTrigger>
                                        <SelectContent>
                                            {agents.map((a) => (
                                                <SelectItem key={a.id} value={a.id}>
                                                    {`${a.firstName || ""} ${a.lastName || ""}`.trim() || a.emailId || a.id}
                                                </SelectItem>
                                            ))}
                                        </SelectContent>
                                    </Select>
                                </div>
                            </CardContent>
                        </Card>
                    </div>
                </div>
            )}
        </div>
    );
}
