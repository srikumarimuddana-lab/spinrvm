"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import type {
    AllowanceTypeValue,
    CorporateMember,
} from "@/lib/api";
import {
    getMemberAllowance,
    listCompanyMembers,
    putMemberAllowance,
} from "@/lib/companyApi";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select";

interface AllowanceDraft {
    type: AllowanceTypeValue;
    amount: string;
    period_start: string;
    period_end: string;
    rollover: boolean;
}

function emptyDraft(): AllowanceDraft {
    const today = new Date().toISOString().slice(0, 10);
    return { type: "fixed_recurring", amount: "300", period_start: today, period_end: today, rollover: false };
}

export default function AllowancesPage() {
    const { id } = useParams<{ id: string }>();
    const [members, setMembers] = useState<CorporateMember[]>([]);
    const [balances, setBalances] = useState<Record<string, number | null>>({});
    const [selectedMemberId, setSelectedMemberId] = useState<string>("");
    const [draft, setDraft] = useState<AllowanceDraft>(emptyDraft());
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [feedback, setFeedback] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);

    const load = useCallback(async () => {
        if (!id) return;
        setLoading(true);
        try {
            const rows = await listCompanyMembers(id, "active");
            setMembers(rows);
            // fetch allowances in parallel to render per-member
            const entries = await Promise.all(
                rows.map(async (m) => {
                    try {
                        const a = await getMemberAllowance(id, m.id);
                        if (!a || !("amount" in a)) return [m.id, null] as const;
                        const amt = a.amount ?? null;
                        const used = a.used ?? 0;
                        return [m.id, amt == null ? null : amt - Math.max(used, 0)] as const;
                    } catch {
                        return [m.id, null] as const;
                    }
                })
            );
            setBalances(Object.fromEntries(entries));
        } catch (e) {
            setError(e instanceof Error ? e.message : "Failed to load");
        } finally {
            setLoading(false);
        }
    }, [id]);

    useEffect(() => {
        load();
    }, [load]);

    const save = async () => {
        if (!id || !selectedMemberId) return;
        setSaving(true);
        setError(null);
        setFeedback(null);
        try {
            const body = {
                type: draft.type,
                amount: draft.type === "unlimited" ? null : Number(draft.amount),
                period_start: draft.type === "fixed_recurring" ? draft.period_start : null,
                period_end: draft.type === "fixed_recurring" ? draft.period_end : null,
                rollover: draft.rollover,
            };
            await putMemberAllowance(id, selectedMemberId, body);
            setFeedback("Allowance saved");
            await load();
        } catch (e) {
            setError(e instanceof Error ? e.message : "Save failed");
        } finally {
            setSaving(false);
        }
    };

    return (
        <div className="space-y-6">
            <header>
                <h1 className="text-2xl font-semibold">Allowances</h1>
                <p className="text-muted-foreground">
                    Set a recurring or one-time spend limit per member.
                </p>
            </header>

            <Card>
                <CardContent className="space-y-4 p-4">
                    <div className="grid gap-3 md:grid-cols-2">
                        <div>
                            <Label>Member</Label>
                            <Select value={selectedMemberId} onValueChange={setSelectedMemberId}>
                                <SelectTrigger>
                                    <SelectValue placeholder="Select active member…" />
                                </SelectTrigger>
                                <SelectContent>
                                    {members.map((m) => (
                                        <SelectItem key={m.id} value={m.id}>
                                            {m.invited_email || m.user_id || m.id}
                                        </SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                        </div>
                        <div>
                            <Label>Type</Label>
                            <Select
                                value={draft.type}
                                onValueChange={(v) => setDraft({ ...draft, type: v as AllowanceTypeValue })}
                            >
                                <SelectTrigger>
                                    <SelectValue />
                                </SelectTrigger>
                                <SelectContent>
                                    <SelectItem value="fixed_recurring">Fixed recurring</SelectItem>
                                    <SelectItem value="one_time">One-time</SelectItem>
                                    <SelectItem value="unlimited">Unlimited</SelectItem>
                                </SelectContent>
                            </Select>
                        </div>
                    </div>

                    {draft.type !== "unlimited" && (
                        <div>
                            <Label>Amount (CAD)</Label>
                            <Input
                                type="number"
                                min={1}
                                value={draft.amount}
                                onChange={(e) => setDraft({ ...draft, amount: e.target.value })}
                            />
                        </div>
                    )}

                    {draft.type === "fixed_recurring" && (
                        <div className="grid gap-3 md:grid-cols-2">
                            <div>
                                <Label>Period start</Label>
                                <Input
                                    type="date"
                                    value={draft.period_start}
                                    onChange={(e) =>
                                        setDraft({ ...draft, period_start: e.target.value })
                                    }
                                />
                            </div>
                            <div>
                                <Label>Period end</Label>
                                <Input
                                    type="date"
                                    value={draft.period_end}
                                    onChange={(e) =>
                                        setDraft({ ...draft, period_end: e.target.value })
                                    }
                                />
                            </div>
                        </div>
                    )}

                    <div className="flex items-center justify-between">
                        <label className="flex items-center gap-2 text-sm">
                            <input
                                type="checkbox"
                                checked={draft.rollover}
                                onChange={(e) => setDraft({ ...draft, rollover: e.target.checked })}
                            />
                            Rollover unused amount
                        </label>
                        <Button onClick={save} disabled={saving || !selectedMemberId}>
                            {saving ? "Saving…" : "Save allowance"}
                        </Button>
                    </div>

                    {feedback && (
                        <p className="rounded bg-emerald-50 p-2 text-xs text-emerald-800">
                            {feedback}
                        </p>
                    )}
                    {error && (
                        <p className="rounded bg-red-50 p-2 text-xs text-red-700">{error}</p>
                    )}
                </CardContent>
            </Card>

            <Card>
                <CardContent className="p-4">
                    <h2 className="mb-3 font-medium">Current allowances</h2>
                    {loading && <p className="text-sm text-muted-foreground">Loading…</p>}
                    {!loading && members.length === 0 && (
                        <p className="text-sm text-muted-foreground">No active members.</p>
                    )}
                    <ul className="divide-y divide-border">
                        {members.map((m) => {
                            const bal = balances[m.id];
                            return (
                                <li
                                    key={m.id}
                                    className="flex items-center justify-between py-2 text-sm"
                                >
                                    <span>{m.invited_email || m.id}</span>
                                    {bal == null ? (
                                        <Badge variant="outline" className="text-[10px]">
                                            No allowance
                                        </Badge>
                                    ) : (
                                        <span className="font-medium">
                                            {bal.toLocaleString("en-CA", {
                                                style: "currency",
                                                currency: "CAD",
                                            })}
                                        </span>
                                    )}
                                </li>
                            );
                        })}
                    </ul>
                </CardContent>
            </Card>
        </div>
    );
}
