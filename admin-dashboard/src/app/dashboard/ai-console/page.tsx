"use client";

/**
 * AI Console — super-admin testing surface for the rider/driver assistant.
 *
 * Pick a user, see exactly the AI threads they see in-app, and send
 * messages AS them (the conversation lands in their real account). Backend
 * enforces role === super_admin and audits every action; this page only
 * hides the UI for everyone else.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import {
    adminAiChat,
    adminSearchUsers,
    getAdminAiConversations,
    getAdminAiMessages,
    getUsers,
} from "@/lib/api";
import { useAuthStore } from "@/store/authStore";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { useToast } from "@/components/ui/use-toast";
import { Bot, MessageSquarePlus, Send, ShieldAlert, User as UserIcon } from "lucide-react";

interface ChatBubble {
    id: string;
    role: "user" | "assistant";
    content: string;
}

export default function AiConsolePage() {
    const { toast } = useToast();
    const me = useAuthStore((s: any) => s.user);

    const [users, setUsers] = useState<any[]>([]);
    const [roleFilter, setRoleFilter] = useState<"rider" | "driver">("rider");
    const [search, setSearch] = useState("");
    const [targetId, setTargetId] = useState<string>("");

    const [conversations, setConversations] = useState<{ id: string; title: string; updated_at: string }[]>([]);
    const [conversationId, setConversationId] = useState<string | null>(null);
    const [messages, setMessages] = useState<ChatBubble[]>([]);
    const [input, setInput] = useState("");
    const [sending, setSending] = useState(false);

    const isSuperAdmin = me?.role === "super_admin";

    // Server-side search (the default /admin/users page is only 50 rows, so
    // local filtering would hide older users) via the POST-based typeahead —
    // search terms can be phone numbers and must stay out of URL/proxy logs
    // (Codex review, PR #1797). Debounced so we don't query per keystroke.
    useEffect(() => {
        if (!isSuperAdmin) return;
        const term = search.trim();
        const handle = setTimeout(
            () => {
                const fetcher = term
                    ? adminSearchUsers({ search: term, role: roleFilter, limit: 50 })
                    : getUsers(roleFilter);
                fetcher
                    .then((rows) => setUsers(Array.isArray(rows) ? rows : (rows as any)?.users ?? []))
                    .catch(() => setUsers([]));
            },
            term ? 300 : 0,
        );
        return () => clearTimeout(handle);
    }, [isSuperAdmin, roleFilter, search]);

    const filteredUsers = useMemo(() => users.slice(0, 50), [users]);

    const target = users.find((u) => u.id === targetId);

    // A refreshed search can drop the previously selected user from the
    // results; without this, the composer would silently keep posting into
    // the OLD user's real thread (Codex review, PR #1797). The composer is
    // additionally gated on `target` below.
    useEffect(() => {
        if (targetId && users.length > 0 && !users.some((u) => u.id === targetId)) {
            setTargetId("");
            setConversationId(null);
            setConversations([]);
            setMessages([]);
        }
    }, [users, targetId]);

    const loadConversations = useCallback(async (userId: string) => {
        try {
            const data = await getAdminAiConversations(userId);
            setConversations(data.conversations ?? []);
        } catch {
            setConversations([]);
        }
    }, []);

    const selectUser = (id: string) => {
        setTargetId(id);
        setConversationId(null);
        setMessages([]);
        if (id) loadConversations(id);
    };

    const openConversation = async (convId: string) => {
        if (!targetId) return;
        setConversationId(convId);
        try {
            const data = await getAdminAiMessages(targetId, convId);
            setMessages(
                (data.messages ?? []).map((m) => ({ id: m.id, role: m.role, content: m.content })),
            );
        } catch {
            setMessages([]);
        }
    };

    const startNew = () => {
        setConversationId(null);
        setMessages([]);
    };

    const send = async () => {
        const text = input.trim();
        // Gate on `target` (resolved from the CURRENT results), not just the
        // stored id — never post into a user who dropped out of the search.
        if (!text || !target || sending) return;
        setSending(true);
        setInput("");
        setMessages((prev) => [...prev, { id: `local-${Date.now()}`, role: "user", content: text }]);
        try {
            const res = await adminAiChat({
                user_id: target.id,
                message: text,
                conversation_id: conversationId,
                audience: roleFilter,
            });
            setConversationId(res.conversation_id);
            setMessages((prev) => [
                ...prev,
                { id: res.message_id ?? `r-${Date.now()}`, role: "assistant", content: res.reply },
                ...res.actions.map((a: any, i: number) => ({
                    id: `a-${Date.now()}-${i}`,
                    role: "assistant" as const,
                    content: `⚡ action → ${a.type}${a.type === "booking_proposal" ? ` (${a.proposal?.pickup_address} → ${a.proposal?.dropoff_address})` : ""}`,
                })),
            ]);
            loadConversations(target.id);
        } catch (err: any) {
            toast({
                title: "AI chat failed",
                description: String(err?.message || err),
                variant: "destructive",
            });
        } finally {
            setSending(false);
        }
    };

    if (!isSuperAdmin) {
        return (
            <div className="flex flex-col items-center justify-center p-16 gap-3 text-center">
                <ShieldAlert className="h-10 w-10 text-muted-foreground" />
                <h2 className="text-lg font-semibold">Super admin only</h2>
                <p className="text-sm text-muted-foreground max-w-md">
                    The AI console impersonates users and reads their chat history, so it is
                    restricted to super admins. Every action here is audit-logged.
                </p>
            </div>
        );
    }

    return (
        <div className="space-y-4">
            <div>
                <h1 className="text-xl font-semibold">AI Console</h1>
                <p className="text-sm text-muted-foreground">
                    Test the assistant as a rider or driver. Conversations are real — they appear in
                    the user&apos;s app and are stamped + audited as admin-initiated.
                </p>
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
                {/* Target picker + their threads */}
                <Card className="border-border/50">
                    <CardHeader>
                        <CardTitle className="text-base">Test as</CardTitle>
                    </CardHeader>
                    <Separator />
                    <CardContent className="pt-4 space-y-3">
                        <div className="grid grid-cols-2 gap-2">
                            <div className="space-y-1">
                                <Label>Audience</Label>
                                <Select
                                    value={roleFilter}
                                    onValueChange={(v: "rider" | "driver") => {
                                        setRoleFilter(v);
                                        selectUser("");
                                    }}
                                >
                                    <SelectTrigger><SelectValue /></SelectTrigger>
                                    <SelectContent>
                                        <SelectItem value="rider">Rider</SelectItem>
                                        <SelectItem value="driver">Driver</SelectItem>
                                    </SelectContent>
                                </Select>
                            </div>
                            <div className="space-y-1">
                                <Label>Search</Label>
                                <Input
                                    value={search}
                                    onChange={(e) => setSearch(e.target.value)}
                                    placeholder="name / phone / id"
                                />
                            </div>
                        </div>
                        <div className="space-y-1">
                            <Label>User</Label>
                            <Select value={targetId} onValueChange={selectUser}>
                                <SelectTrigger>
                                    <SelectValue placeholder={`Select a ${roleFilter}…`} />
                                </SelectTrigger>
                                <SelectContent>
                                    {filteredUsers.map((u) => (
                                        <SelectItem key={u.id} value={u.id}>
                                            {[u.first_name, u.last_name].filter(Boolean).join(" ") || u.id}
                                            {u.phone ? ` · …${String(u.phone).slice(-4)}` : ""}
                                        </SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                        </div>

                        {targetId && (
                            <div className="space-y-2 pt-2">
                                <div className="flex items-center justify-between">
                                    <Label>Their AI threads</Label>
                                    <Button variant="ghost" size="sm" onClick={startNew}>
                                        <MessageSquarePlus className="h-4 w-4 mr-1" /> New
                                    </Button>
                                </div>
                                <div className="space-y-1 max-h-64 overflow-y-auto">
                                    {conversations.length === 0 && (
                                        <p className="text-xs text-muted-foreground">No conversations yet.</p>
                                    )}
                                    {conversations.map((c) => (
                                        <button
                                            key={c.id}
                                            onClick={() => openConversation(c.id)}
                                            className={`w-full text-left text-sm px-2 py-1.5 rounded-md hover:bg-accent ${conversationId === c.id ? "bg-accent" : ""}`}
                                        >
                                            <span className="line-clamp-1">{c.title}</span>
                                            <span className="text-[10px] text-muted-foreground">{c.updated_at}</span>
                                        </button>
                                    ))}
                                </div>
                            </div>
                        )}
                    </CardContent>
                </Card>

                {/* Chat panel */}
                <Card className="border-border/50 lg:col-span-2">
                    <CardHeader>
                        <CardTitle className="text-base">
                            {target
                                ? `Chatting as ${[target.first_name, target.last_name].filter(Boolean).join(" ") || target.id} (${roleFilter})`
                                : "Select a user to start"}
                        </CardTitle>
                    </CardHeader>
                    <Separator />
                    <CardContent className="pt-4">
                        <div className="h-[420px] overflow-y-auto space-y-3 pb-3">
                            {messages.map((m) => (
                                <div key={m.id} className={`flex gap-2 ${m.role === "user" ? "justify-end" : ""}`}>
                                    {m.role === "assistant" && <Bot className="h-5 w-5 text-primary shrink-0 mt-1" />}
                                    <div
                                        className={`max-w-[75%] rounded-lg px-3 py-2 text-sm whitespace-pre-wrap ${m.role === "user" ? "bg-primary text-primary-foreground" : "bg-muted"}`}
                                    >
                                        {m.content}
                                    </div>
                                    {m.role === "user" && <UserIcon className="h-5 w-5 text-muted-foreground shrink-0 mt-1" />}
                                </div>
                            ))}
                            {messages.length === 0 && targetId && (
                                <p className="text-sm text-muted-foreground">
                                    Start a new thread or open one of their existing conversations.
                                </p>
                            )}
                        </div>
                        <div className="flex gap-2 pt-2 border-t">
                            <Input
                                value={input}
                                onChange={(e) => setInput(e.target.value)}
                                onKeyDown={(e) => e.key === "Enter" && !e.shiftKey && send()}
                                placeholder={target ? "Message as this user…" : "Select a user first"}
                                disabled={!target || sending}
                            />
                            <Button onClick={send} disabled={!target || sending || !input.trim()}>
                                <Send className="h-4 w-4" />
                            </Button>
                        </div>
                    </CardContent>
                </Card>
            </div>
        </div>
    );
}
