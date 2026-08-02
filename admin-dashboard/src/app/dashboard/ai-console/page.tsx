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
import { Bot, Car, LifeBuoy, MapPin, MessageSquarePlus, Send, ShieldAlert, Tag, User as UserIcon } from "lucide-react";
import type { AiAction } from "@spinr/shared/types/ai";
import { buildLocationChoiceMessage, buildQuoteBookingMessage } from "@spinr/shared/utils/aiLocationMessages";

interface ChatBubble {
    id: string;
    role: "user" | "assistant";
    content: string;
    /** Rich client action (fare quote / location suggestions / booking
     * proposal) — rendered as the same card the rider sees in-app. */
    action?: AiAction;
}

/** The rider sees a "Drop a pin" button that opens the map screen; the
 * console has no map, so it offers a lat,lng entry that sends the identical
 * bracketed-coordinate message the app's confirmed pin would (the format
 * prompt rule 6b passes through verbatim). Pre-filled with the assistant's
 * approximate point when it supplied one. */
function MapPinBubble({
    action,
    onQuickSend,
}: {
    action: Extract<AiAction, { type: "open_map_picker" }>;
    onQuickSend: (text: string) => void;
}) {
    const [coords, setCoords] = useState(
        action.approx_lat != null && action.approx_lng != null
            ? `${action.approx_lat}, ${action.approx_lng}`
            : "",
    );
    const parsed = useMemo(() => {
        const match = coords.match(/^\s*(-?\d{1,3}(?:\.\d+)?)\s*,\s*(-?\d{1,3}(?:\.\d+)?)\s*$/);
        if (!match) return null;
        const lat = Number(match[1]);
        const lng = Number(match[2]);
        if (Math.abs(lat) > 90 || Math.abs(lng) > 180) return null;
        return { lat, lng };
    }, [coords]);
    const sendPin = () => {
        if (!parsed) return;
        const place = action.label ? `${action.label} ` : "";
        onQuickSend(
            `I dropped a pin on the map for my ${action.location_role}: ${place}[${parsed.lat.toFixed(5)},${parsed.lng.toFixed(5)}]. Use these exact coordinates.`,
        );
    };
    return (
        <div className="max-w-[75%] rounded-lg border bg-muted px-3 py-2 text-sm space-y-2">
            <div className="flex items-center gap-2 font-semibold">
                <MapPin className="h-4 w-4 text-primary" />
                Drop-a-pin request ({action.location_role})
            </div>
            {action.label && <p className="text-xs text-muted-foreground">{action.label}</p>}
            <div className="flex items-center gap-2">
                <Input
                    value={coords}
                    onChange={(e) => setCoords(e.target.value)}
                    placeholder="lat, lng"
                    className="h-8 text-xs"
                    aria-label="Pin coordinates (lat, lng)"
                />
                <Button size="sm" className="h-8" onClick={sendPin} disabled={!parsed}>
                    Send pin
                </Button>
            </div>
            <p className="text-[11px] text-muted-foreground">
                The rider sees a button that opens the map; enter lat, lng to stand in for their
                confirmed pin.
            </p>
        </div>
    );
}

/** Mirrors the rider app's action cards so the console shows exactly what
 * the rider would see (shared/types/ai.ts is the contract for both). */
function ActionBubble({ action, onQuickSend }: { action: AiAction; onQuickSend: (text: string) => void }) {
    if (action.type === "fare_quote") {
        const meta = [
            action.distance_km != null ? `${action.distance_km} km` : null,
            action.duration_minutes != null ? `about ${action.duration_minutes} min` : null,
        ]
            .filter(Boolean)
            .join(" · ");
        return (
            <div className="max-w-[75%] rounded-lg border bg-muted px-3 py-2 text-sm space-y-2">
                <div className="flex items-center gap-2 font-semibold">
                    <Tag className="h-4 w-4 text-primary" /> Ride options
                    {meta && <span className="font-normal text-xs text-muted-foreground">{meta}</span>}
                </div>
                {action.quotes.map((q, i) => {
                    const hasSavings = !!q.promo_savings && q.final_total !== q.total;
                    // Self-contained message carrying the quote's exact
                    // [lat,lng] and vehicle id verbatim (mirrors the rider
                    // app's quote-card tap) — a prose-only message forced a
                    // third independent geocode, moving pins/prices between
                    // the quote and the confirm card (AI9).
                    return (
                        <button
                            key={q.vehicle_type_id ?? i}
                            onClick={() => onQuickSend(buildQuoteBookingMessage(action, q))}
                            className="w-full flex items-center justify-between gap-3 rounded-md bg-background px-2 py-1.5 text-left hover:bg-accent"
                        >
                            <span>
                                <span className="font-medium">{q.vehicle_type ?? "Ride"}</span>
                                <span className="block text-xs text-muted-foreground">
                                    {[
                                        q.eta_minutes != null
                                            ? `${q.eta_minutes} min${q.closest_driver_km != null ? ` (${q.closest_driver_km} km)` : ""} away`
                                            : null,
                                        q.capacity != null ? `${q.capacity} seats` : null,
                                        (q.surge_multiplier ?? 1) > 1 ? `${q.surge_multiplier}x surge` : null,
                                    ]
                                        .filter(Boolean)
                                        .join(" · ")}
                                </span>
                                {hasSavings ? (
                                    <span className="block text-xs font-medium text-green-600">
                                        {q.promo_code} · save ${q.promo_savings}
                                    </span>
                                ) : (
                                    <span className="block text-xs text-muted-foreground">No promo applied</span>
                                )}
                            </span>
                            <span className="text-right">
                                {hasSavings && (
                                    <span className="block text-xs text-muted-foreground line-through">${q.total}</span>
                                )}
                                <span className="font-semibold">${q.final_total}</span>
                            </span>
                        </button>
                    );
                })}
                {(() => {
                    // Mirror the rider app: line-item details for the recommended option.
                    const rec =
                        action.quotes.find((q) => q.vehicle_type_id === action.recommended_vehicle_type_id) ??
                        action.quotes[0];
                    if (!rec?.breakdown?.length) return null;
                    return (
                        <div className="rounded-md bg-background px-2 py-1.5 space-y-0.5">
                            <p className="text-xs font-semibold">Fare details — {rec.vehicle_type ?? "recommended"}</p>
                            {rec.breakdown.map((line, i) => (
                                <p key={i} className="flex justify-between text-xs text-muted-foreground">
                                    <span>{line.label}</span>
                                    {line.amount != null && <span>${line.amount.toFixed(2)}</span>}
                                </p>
                            ))}
                            {rec.promo_savings && (
                                <p className="flex justify-between text-xs font-medium text-green-600">
                                    <span>Promo {rec.promo_code}</span>
                                    <span>-${rec.promo_savings}</span>
                                </p>
                            )}
                            <p className="flex justify-between text-xs font-semibold">
                                <span>You pay</span>
                                <span>${rec.final_total}</span>
                            </p>
                        </div>
                    );
                })()}
                <p className="text-[11px] text-muted-foreground">
                    Totals include taxes &amp; fees. The rider sees this as a tappable card.
                </p>
            </div>
        );
    }
    if (action.type === "location_suggestions") {
        return (
            <div className="max-w-[75%] rounded-lg border bg-muted px-3 py-2 text-sm space-y-1">
                <div className="flex items-center gap-2 font-semibold">
                    <MapPin className="h-4 w-4 text-primary" />
                    Choose {action.location_role === "pickup" ? "a pickup" : action.location_role === "dropoff" ? "a dropoff" : "a location"}
                </div>
                {action.candidates.slice(0, 3).map((c, i) => {
                    // Self-contained message carrying the tapped candidate's
                    // [lat,lng] (mirrors the rider app) — a prose-only tap
                    // forces the model to re-geocode and loop on imprecise
                    // street addresses.
                    const message = buildLocationChoiceMessage(c, action.location_role);
                    return (
                        <button
                            key={`${c.lat}:${c.lng}:${i}`}
                            onClick={() => message && onQuickSend(message)}
                            className="w-full rounded-md bg-background px-2 py-1.5 text-left hover:bg-accent"
                        >
                            <span className="font-medium">{c.name || c.address}</span>
                            {c.name && c.address && (
                                <span className="block text-xs text-muted-foreground">{c.address}</span>
                            )}
                        </button>
                    );
                })}
            </div>
        );
    }
    if (action.type === "booking_proposal") {
        const p = action.proposal;
        return (
            <div className="max-w-[75%] rounded-lg border bg-muted px-3 py-2 text-sm space-y-1">
                <div className="flex items-center gap-2 font-semibold">
                    <Car className="h-4 w-4 text-primary" /> Booking card shown to rider
                </div>
                <p className="text-xs">{p.pickup_address}</p>
                <p className="text-xs">→ {p.dropoff_address}</p>
                <p className="text-xs text-muted-foreground">
                    {[
                        p.scheduled_time ? `Scheduled ${p.scheduled_time}` : "Now",
                        p.payment_method ?? null,
                        p.promo_code ? `promo ${p.promo_code}` : null,
                    ]
                        .filter(Boolean)
                        .join(" · ")}
                </p>
                <p className="text-[11px] text-muted-foreground">
                    The ride books only when the rider taps Confirm in the app.
                </p>
            </div>
        );
    }
    if (action.type === "open_map_picker") {
        return <MapPinBubble action={action} onQuickSend={onQuickSend} />;
    }
    if (action.type === "open_support") {
        return (
            <div className="max-w-[75%] rounded-lg border bg-muted px-3 py-2 text-sm space-y-1">
                <div className="flex items-center gap-2 font-semibold">
                    <LifeBuoy className="h-4 w-4 text-primary" /> Support handoff
                </div>
                <p className="text-xs text-muted-foreground">
                    Category: {action.category} · opens {action.link} in the app
                </p>
                <p className="text-[11px] text-muted-foreground">
                    The rider sees this as a tappable &quot;Contact support&quot; button.
                </p>
            </div>
        );
    }
    return (
        <div className="max-w-[75%] rounded-lg bg-muted px-3 py-2 text-sm text-muted-foreground">
            ⚡ action → {(action as { type: string }).type}
        </div>
    );
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

    const send = async (textOverride?: string) => {
        const text = (textOverride ?? input).trim();
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
                ...res.actions.map((a: AiAction, i: number) => ({
                    id: `a-${Date.now()}-${i}`,
                    role: "assistant" as const,
                    content: "",
                    action: a,
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
                                    {m.action ? (
                                        <ActionBubble action={m.action} onQuickSend={(text) => send(text)} />
                                    ) : (
                                        <div
                                            className={`max-w-[75%] rounded-lg px-3 py-2 text-sm whitespace-pre-wrap ${m.role === "user" ? "bg-primary text-primary-foreground" : "bg-muted"}`}
                                        >
                                            {m.content}
                                        </div>
                                    )}
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
                            <Button onClick={() => send()} disabled={!target || sending || !input.trim()}>
                                <Send className="h-4 w-4" />
                            </Button>
                        </div>
                    </CardContent>
                </Card>
            </div>
        </div>
    );
}
