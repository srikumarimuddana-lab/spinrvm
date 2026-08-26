"use client";

import { useEffect, useState, useCallback } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import {
    getDeskTicket,
    getDeskTicketThreads,
    getDeskThread,
    getDeskAgents,
    replyDeskTicket,
    commentDeskTicket,
    updateDeskTicket,
    updateDeskTicketTags,
    getDeskServiceAreas,
    getDeskTicketServiceArea,
    setDeskTicketServiceArea,
    aiSuggestDeskReply,
    getZohoConfig,
    type DeskServiceArea,
    type DeskTicketServiceArea,
} from "@/lib/api";
import { useRequireModule } from "@/hooks/useRequireModule";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { RichTextEditor } from "@/components/ui/rich-text-editor";
import DOMPurify from "dompurify";
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select";
import { useToast } from "@/components/ui/use-toast";
import { Input } from "@/components/ui/input";
import {
    ArrowLeft, Send, StickyNote, User, Mail, ExternalLink, Clock, CheckCircle2, Timer,
    ChevronDown, ChevronUp, Tag, Plus, X, Save, MapPin, Sparkles, AlertTriangle,
} from "lucide-react";

const STATUSES = ["Open", "On Hold", "Escalated", "Closed"];
const PRIORITIES = ["Low", "Medium", "High", "Urgent"];
const CLASSIFICATIONS = ["Question", "Problem", "Feature", "Others"];
// One-click tag presets for Spinr's common ticket types (tags are free-form).
const QUICK_TAGS = ["Driver request", "Client request", "Payment issue", "Lost & found", "Safety", "Refund"];

function initials(name: string): string {
    const parts = (name || "").trim().split(/[\s@.]+/).filter(Boolean);
    if (parts.length === 0) return "?";
    return (parts[0][0] + (parts[1]?.[0] || "")).toUpperCase();
}

function ticketTagNames(t: any): string[] {
    const tags = t?.tags;
    if (!Array.isArray(tags)) return [];
    return tags.map((x: any) => (typeof x === "string" ? x : x?.name)).filter(Boolean);
}

function statusClass(status: string): string {
    const s = (status || "").toLowerCase();
    // eslint-disable-next-line no-restricted-syntax -- "open" (new/active) has no semantic-token equivalent; must stay visually distinct from hold/escalated/closed (#2816)
    if (s.includes("open")) return "bg-blue-100 text-blue-800 hover:bg-blue-100";
    if (s.includes("hold")) return "bg-warning/15 text-warning hover:bg-warning/15";
    if (s.includes("escal")) return "bg-destructive/15 text-destructive hover:bg-destructive/15";
    if (s.includes("closed")) return "bg-muted text-muted-foreground hover:bg-muted";
    return "bg-muted text-muted-foreground hover:bg-muted";
}

function fmtTime(s?: string): string {
    return s ? s.slice(0, 16).replace("T", " ") : "—";
}

/** Human-readable duration between two Zoho ISO timestamps. */
function duration(from?: string, to?: string): string {
    if (!from || !to) return "—";
    const ms = new Date(to).getTime() - new Date(from).getTime();
    if (!isFinite(ms) || ms < 0) return "—";
    const mins = Math.round(ms / 60000);
    if (mins < 60) return `${mins}m`;
    const hrs = mins / 60;
    if (hrs < 48) return `${hrs.toFixed(1)}h`;
    return `${(hrs / 24).toFixed(1)}d`;
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

/** Convert the AI's plain-text draft into simple HTML for the rich editor:
 *  escape, blank lines -> paragraphs, single newlines -> <br>. */
function textToHtml(text: string): string {
    const esc = (s: string) =>
        s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    return (text || "")
        .trim()
        .split(/\n{2,}/)
        .map((p) => `<p>${esc(p).replace(/\n/g, "<br>")}</p>`)
        .join("");
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
    const [suggesting, setSuggesting] = useState(false);
    // Optional guidance the agent gives the AI before drafting (what to say /
    // the decision to convey). Empty = let the AI draft from the ticket alone.
    const [aiInstruction, setAiInstruction] = useState("");

    const [emailSignature, setEmailSignature] = useState("");

    // Service area (Spinr-local; auto-derived from the contact's ride history
    // when possible, else highlighted for an optional manual assignment).
    const [areas, setAreas] = useState<DeskServiceArea[]>([]);
    const [areaInfo, setAreaInfo] = useState<DeskTicketServiceArea | null>(null);
    const [savingArea, setSavingArea] = useState(false);

    // Editable classification fields (seeded from the ticket on load).
    const [category, setCategory] = useState("");
    const [subCategory, setSubCategory] = useState("");
    const [classification, setClassification] = useState("");
    const [tagInput, setTagInput] = useState("");

    // Conversation expand/collapse + lazily-fetched full thread bodies. The
    // /conversations list only returns a truncated summary per email thread.
    const [expanded, setExpanded] = useState<Record<string, boolean>>({});
    const [fullBody, setFullBody] = useState<Record<string, string>>({});
    const [loadingMsg, setLoadingMsg] = useState<Record<string, boolean>>({});

    const toggleMsg = async (m: any) => {
        const key = String(m.id ?? "");
        if (!key) return;
        const willOpen = !expanded[key];
        setExpanded((e) => ({ ...e, [key]: willOpen }));
        // Comments already carry full content; only threads need a fetch.
        if (willOpen && m.type !== "comment" && fullBody[key] === undefined) {
            setLoadingMsg((l) => ({ ...l, [key]: true }));
            try {
                const full = await getDeskThread(id, key);
                setFullBody((f) => ({ ...f, [key]: toText(full?.content || full?.plainText || "") }));
            } catch {
                setFullBody((f) => ({ ...f, [key]: "" }));
            } finally {
                setLoadingMsg((l) => ({ ...l, [key]: false }));
            }
        }
    };

    const load = useCallback(async () => {
        setLoading(true);
        setError(null);
        try {
            const [t, th] = await Promise.all([getDeskTicket(id), getDeskTicketThreads(id)]);
            setTicket(t);
            setThread(th.data || []);
            setCategory(t.category || "");
            setSubCategory(t.subCategory || "");
            setClassification(t.classification || "");
            setExpanded({});
            setFullBody({});
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
        getDeskServiceAreas().then((r) => setAreas(r.data || [])).catch(() => {});
        getZohoConfig().then((c) => {
            if (c.helpdesk_signature_enabled && c.helpdesk_signature_preview) {
                setEmailSignature(c.helpdesk_signature_preview);
            }
        }).catch(() => {});
    }, [allowed]);

    // Resolve/auto-assign the ticket's service area once, on mount. The endpoint
    // is keyed by id (it fetches the ticket itself), so it must NOT depend on the
    // `ticket` object — otherwise every unrelated save (status/priority/tags)
    // replaces `ticket`'s identity and re-fires this fetch + its auto-assign write.
    useEffect(() => {
        if (!allowed || !id) return;
        getDeskTicketServiceArea(id).then(setAreaInfo).catch(() => setAreaInfo(null));
    }, [allowed, id]);

    const assignArea = async (service_area_id: string | null) => {
        setSavingArea(true);
        try {
            const next = await setDeskTicketServiceArea(id, service_area_id);
            setAreaInfo(next);
            toast({ title: service_area_id ? "Service area assigned" : "Service area cleared" });
        } catch (e: any) {
            toast({ title: "Assign failed", description: e?.message, variant: "destructive" });
        } finally {
            setSavingArea(false);
        }
    };

    const suggestAI = async () => {
        setSuggesting(true);
        try {
            const { reply: draft } = await aiSuggestDeskReply(id, aiInstruction);
            setReply(textToHtml(draft));
            toast({ title: "Draft ready", description: "Review and edit before sending." });
        } catch (e: any) {
            toast({ title: "Couldn't draft a reply", description: e?.message, variant: "destructive" });
        } finally {
            setSuggesting(false);
        }
    };

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
        const html = DOMPurify.sanitize(reply).trim();
        if (!toText(html).trim()) return;
        setSending(true);
        try {
            await replyDeskTicket(id, { content: html, to: ticket?.email || ticket?.contact?.email });
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
        // Notes are sent as HTML — escape and convert newlines so multi-line
        // notes don't collapse into a single line.
        const html = note
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/\n/g, "<br>");
        setSending(true);
        try {
            await commentDeskTicket(id, { content: html, is_public: false });
            setNote("");
            toast({ title: "Internal note added" });
            await load();
        } catch (e: any) {
            toast({ title: "Note failed", description: e?.message, variant: "destructive" });
        } finally {
            setSending(false);
        }
    };

    const saveClassify = async () => {
        const fields: Record<string, string> = {};
        if (category.trim()) fields.category = category.trim();
        if (subCategory.trim()) fields.subCategory = subCategory.trim();
        if (classification.trim()) fields.classification = classification.trim();
        if (Object.keys(fields).length === 0) return;
        await patch(fields, "Ticket details");
    };

    const mutateTags = async (body: { add?: string[]; remove?: string[] }, label: string) => {
        setSavingField(true);
        try {
            await updateDeskTicketTags(id, body);
            toast({ title: `Tags ${label}` });
            await load();
        } catch (e: any) {
            toast({ title: "Tag update failed", description: e?.message, variant: "destructive" });
        } finally {
            setSavingField(false);
        }
    };

    const addTags = (raw: string) => {
        const names = raw.split(",").map((s) => s.trim()).filter(Boolean);
        if (names.length) mutateTags({ add: names }, "added");
        setTagInput("");
    };

    const currentTags = ticketTagNames(ticket);

    if (!allowed) return null;

    return (
        <div className="space-y-4">
            <div className="flex items-center justify-between">
                <Button asChild variant="ghost" size="sm">
                    <Link href="/dashboard/support-tickets/tickets"><ArrowLeft className="mr-2 h-4 w-4" /> Back to tickets</Link>
                </Button>
                {ticket?.webUrl && (
                    <Button asChild variant="outline" size="sm">
                        <a href={ticket.webUrl} target="_blank" rel="noopener noreferrer">
                            <ExternalLink className="mr-2 h-4 w-4" /> Open in Zoho Desk
                        </a>
                    </Button>
                )}
            </div>

            {loading && <Card><CardContent className="p-6 text-muted-foreground">Loading…</CardContent></Card>}
            {!loading && error && <Card><CardContent className="p-4 text-sm text-destructive">{error}</CardContent></Card>}

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
                            <CardHeader className="flex flex-row items-center justify-between">
                                <CardTitle className="text-base">Conversation</CardTitle>
                                <span className="text-xs text-muted-foreground">
                                    {thread.length} message{thread.length === 1 ? "" : "s"}
                                </span>
                            </CardHeader>
                            <CardContent className="space-y-3">
                                {thread.length === 0 && <p className="text-sm text-muted-foreground">No replies yet.</p>}
                                {thread.map((m: any, i: number) => {
                                    const key = String(m.id ?? i);
                                    const isComment = m.type === "comment";
                                    const isOut = (m.direction || "").toLowerCase() === "out";
                                    const author = m.author?.name || m.commenter?.name || m.fromEmailAddress || m.from || "—";
                                    const when = (m.createdTime || m.commentedTime || m.summary?.time || "").slice(0, 16).replace("T", " ");
                                    const isOpen = !!expanded[key];
                                    const preview = toText(m.summary || m.content || m.plainText || "");
                                    const body = isOpen && fullBody[key] ? fullBody[key] : preview;
                                    const role = isComment ? "Internal note" : isOut ? "Agent reply" : "Customer";
                                    const accent = isComment
                                        // eslint-disable-next-line no-restricted-syntax -- message-role differentiator (internal note/agent reply/customer), not a health-state signal (#2816)
                                        ? "border-l-amber-400 bg-amber-50/60 dark:bg-amber-950/20"
                                        : isOut
                                        ? "border-l-blue-400"
                                        : "border-l-slate-300";
                                    return (
                                        <div key={key} className={`rounded-lg border border-l-4 ${accent} p-3`}>
                                            <div className="mb-2 flex items-start justify-between gap-2">
                                                <div className="flex items-center gap-2">
                                                    <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-muted text-[11px] font-semibold">
                                                        {initials(author)}
                                                    </span>
                                                    <div className="leading-tight">
                                                        <p className="text-sm font-medium">{author}</p>
                                                        <p className="flex items-center gap-1 text-[11px] text-muted-foreground">
                                                            {isComment ? <StickyNote className="h-3 w-3" /> : <Mail className="h-3 w-3" />}
                                                            {role}{m.channel ? ` · ${m.channel}` : ""}
                                                        </p>
                                                    </div>
                                                </div>
                                                <span className="whitespace-nowrap text-xs text-muted-foreground">{when}</span>
                                            </div>
                                            <p className={`whitespace-pre-wrap text-sm ${isOpen ? "" : "line-clamp-3"}`}>
                                                {loadingMsg[key] ? "Loading full message…" : body || "(no content)"}
                                            </p>
                                            <button
                                                onClick={() => toggleMsg(m)}
                                                // eslint-disable-next-line no-restricted-syntax -- decorative link-style accent, not a status signal (#2816)
                                                className="mt-1 flex items-center gap-1 text-xs font-medium text-blue-600 dark:text-blue-400 hover:underline"
                                            >
                                                {isOpen ? <><ChevronUp className="h-3 w-3" /> Show less</> : <><ChevronDown className="h-3 w-3" /> Show full message</>}
                                            </button>
                                        </div>
                                    );
                                })}
                            </CardContent>
                        </Card>

                        <Card>
                            <CardHeader className="flex flex-row items-center justify-between">
                                <CardTitle className="text-base">Reply to requester</CardTitle>
                                <Button
                                    variant="outline"
                                    size="sm"
                                    onClick={suggestAI}
                                    disabled={suggesting || sending}
                                    title="Draft a reply with the AI assistant — you review and edit before sending"
                                >
                                    <Sparkles className="mr-2 h-4 w-4" />
                                    {suggesting ? "Drafting…" : "Suggest with AI"}
                                </Button>
                            </CardHeader>
                            <CardContent className="space-y-2">
                                <div className="space-y-1 rounded-md border border-dashed bg-muted/30 p-2">
                                    <Label htmlFor="ai-instruction" className="text-xs text-muted-foreground">
                                        Tell the AI what to say (optional)
                                    </Label>
                                    <Textarea
                                        id="ai-instruction"
                                        rows={2}
                                        value={aiInstruction}
                                        onChange={(e) => setAiInstruction(e.target.value)}
                                        placeholder="e.g. Apologize for the delay, confirm the refund is approved, and ask for the trip date. The AI reads the full ticket chain — add anything it can't know."
                                        disabled={suggesting || sending}
                                    />
                                </div>
                                <RichTextEditor
                                    value={reply}
                                    onChange={setReply}
                                    placeholder="Type your reply… (bold, lists, links supported)"
                                    disabled={sending || suggesting}
                                />
                                <p className="text-[11px] text-muted-foreground">
                                    AI drafts are suggestions — review for accuracy before sending. Nothing is sent until you click Send reply.
                                </p>
                                {emailSignature && (
                                    <div className="rounded-md border border-dashed bg-muted/30 p-3">
                                        <p className="mb-1 text-[11px] font-medium text-muted-foreground">Signature (auto-appended)</p>
                                        <div
                                            // eslint-disable-next-line no-restricted-syntax -- decorative link-style accent, not a status signal (#2816)
                                            className="text-sm text-muted-foreground [&_a]:text-blue-600 dark:[&_a]:text-blue-400 [&_a]:underline"
                                            dangerouslySetInnerHTML={{ __html: DOMPurify.sanitize(emailSignature) }}
                                        />
                                    </div>
                                )}
                                <div className="flex justify-end">
                                    <Button onClick={sendReply} disabled={sending || suggesting || !toText(reply).trim()}>
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

                        <Card className={areaInfo?.needs_assignment ? "border-warning bg-warning/10" : undefined}>
                            <CardHeader>
                                <CardTitle className="flex items-center gap-2 text-base">
                                    <MapPin className="h-4 w-4" /> Service area
                                    {areaInfo?.service_area_source === "auto" && (
                                        <Badge variant="secondary" className="text-[10px]">auto</Badge>
                                    )}
                                </CardTitle>
                            </CardHeader>
                            <CardContent className="space-y-2">
                                {areaInfo?.needs_assignment && (
                                    <p className="flex items-start gap-1.5 text-xs text-warning">
                                        <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                                        No service area on file for this requester — assign one (optional, helps routing & reporting).
                                    </p>
                                )}
                                {areaInfo?.suggested && !areaInfo?.service_area_id && (
                                    <Button
                                        variant="outline"
                                        size="sm"
                                        className="w-full justify-start"
                                        disabled={savingArea}
                                        onClick={() => assignArea(areaInfo.suggested!.service_area_id)}
                                    >
                                        <Plus className="mr-2 h-4 w-4" />
                                        Apply suggested: {areaInfo.suggested.service_area_name || areaInfo.suggested.service_area_id}
                                    </Button>
                                )}
                                <Select
                                    value={areaInfo?.service_area_id || undefined}
                                    onValueChange={(v) => assignArea(v)}
                                    disabled={savingArea}
                                >
                                    <SelectTrigger><SelectValue placeholder="Assign a service area" /></SelectTrigger>
                                    <SelectContent>
                                        {areas.map((a) => (
                                            <SelectItem key={a.id} value={a.id}>
                                                {a.name || a.city || a.id}
                                                {a.province ? ` · ${a.province}` : ""}
                                            </SelectItem>
                                        ))}
                                    </SelectContent>
                                </Select>
                                {areaInfo?.service_area_id && (
                                    <button
                                        onClick={() => assignArea(null)}
                                        disabled={savingArea}
                                        className="text-xs text-muted-foreground hover:underline"
                                    >
                                        Clear assignment
                                    </button>
                                )}
                            </CardContent>
                        </Card>

                        <Card>
                            <CardHeader><CardTitle className="text-base">Timeline</CardTitle></CardHeader>
                            <CardContent className="space-y-2 text-sm">
                                <div className="flex items-center justify-between">
                                    <span className="flex items-center gap-2 text-muted-foreground"><Clock className="h-4 w-4" /> Opened</span>
                                    <span>{fmtTime(ticket.createdTime)}</span>
                                </div>
                                <div className="flex items-center justify-between">
                                    <span className="flex items-center gap-2 text-muted-foreground"><CheckCircle2 className="h-4 w-4" /> Closed</span>
                                    <span>{ticket.closedTime ? fmtTime(ticket.closedTime) : "Not closed"}</span>
                                </div>
                                <div className="flex items-center justify-between">
                                    <span className="flex items-center gap-2 text-muted-foreground"><Timer className="h-4 w-4" /> Resolution time</span>
                                    <span className="font-medium">
                                        {ticket.closedTime ? duration(ticket.createdTime, ticket.closedTime) : "—"}
                                    </span>
                                </div>
                                {(ticket.dueDate || ticket.responseDueDate) && (
                                    <div className="flex items-center justify-between">
                                        <span className="flex items-center gap-2 text-muted-foreground"><Timer className="h-4 w-4" /> Due</span>
                                        <span>{fmtTime(ticket.dueDate || ticket.responseDueDate)}</span>
                                    </div>
                                )}
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

                        <Card>
                            <CardHeader><CardTitle className="text-base">Classify & tag</CardTitle></CardHeader>
                            <CardContent className="space-y-3">
                                <div className="space-y-1">
                                    <Label>Category</Label>
                                    <Input value={category} onChange={(e) => setCategory(e.target.value)} placeholder="e.g. Driver Request" disabled={savingField} />
                                </div>
                                <div className="space-y-1">
                                    <Label>Sub-category</Label>
                                    <Input value={subCategory} onChange={(e) => setSubCategory(e.target.value)} placeholder="optional" disabled={savingField} />
                                </div>
                                <div className="space-y-1">
                                    <Label>Classification</Label>
                                    <Select value={classification || undefined} onValueChange={setClassification} disabled={savingField}>
                                        <SelectTrigger><SelectValue placeholder="Select" /></SelectTrigger>
                                        <SelectContent>
                                            {CLASSIFICATIONS.map((c) => <SelectItem key={c} value={c}>{c}</SelectItem>)}
                                        </SelectContent>
                                    </Select>
                                </div>
                                <Button size="sm" onClick={saveClassify} disabled={savingField}>
                                    <Save className="mr-2 h-4 w-4" /> Save details
                                </Button>

                                <div className="space-y-2 border-t pt-3">
                                    <Label className="flex items-center gap-1"><Tag className="h-3.5 w-3.5" /> Tags</Label>
                                    <div className="flex flex-wrap gap-1">
                                        {currentTags.length === 0 && <span className="text-xs text-muted-foreground">No tags yet.</span>}
                                        {currentTags.map((name) => (
                                            <Badge key={name} variant="secondary" className="gap-1">
                                                {name}
                                                <button onClick={() => mutateTags({ remove: [name] }, "removed")} disabled={savingField}>
                                                    <X className="h-3 w-3" />
                                                </button>
                                            </Badge>
                                        ))}
                                    </div>
                                    <div className="flex flex-wrap gap-1">
                                        {QUICK_TAGS.filter((q) => !currentTags.includes(q)).map((q) => (
                                            <button
                                                key={q}
                                                onClick={() => mutateTags({ add: [q] }, "added")}
                                                disabled={savingField}
                                                className="rounded-full border px-2 py-0.5 text-xs text-muted-foreground hover:bg-muted"
                                            >
                                                <Plus className="mr-0.5 inline h-3 w-3" />{q}
                                            </button>
                                        ))}
                                    </div>
                                    <div className="flex gap-2">
                                        <Input
                                            value={tagInput}
                                            onChange={(e) => setTagInput(e.target.value)}
                                            onKeyDown={(e) => { if (e.key === "Enter") addTags(tagInput); }}
                                            placeholder="Add tag(s), comma-separated"
                                            disabled={savingField}
                                        />
                                        <Button variant="outline" size="sm" onClick={() => addTags(tagInput)} disabled={savingField || !tagInput.trim()}>
                                            Add
                                        </Button>
                                    </div>
                                </div>
                            </CardContent>
                        </Card>
                    </div>
                </div>
            )}
        </div>
    );
}
