"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import type {
    CorporatePolicy,
    PaymentSourcePolicy,
    TimeWindowPolicy,
} from "@/lib/api";
import {
    getCompanyPolicy,
    patchCompanyPolicy,
} from "@/lib/companyApi";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Badge } from "@/components/ui/badge";
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select";
import { Plus, Save, Trash2 } from "lucide-react";

const DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"] as const;

const DAY_LABEL: Record<(typeof DAYS)[number], string> = {
    mon: "Mon", tue: "Tue", wed: "Wed", thu: "Thu", fri: "Fri", sat: "Sat", sun: "Sun",
};

const PAYMENT_LABELS: Record<PaymentSourcePolicy, string> = {
    allowance_only: "Allowance only",
    master_only: "Master wallet only",
    both: "Allowance or personal card",
};

interface Draft {
    active: boolean;
    max_fare_per_ride: string;
    allowed_payment_source: PaymentSourcePolicy;
    allowed_time_windows: TimeWindowPolicy[];
}

function emptyWindow(): TimeWindowPolicy {
    return { day: "mon", start: "09:00", end: "18:00" };
}

function policyToDraft(p: CorporatePolicy | null): Draft {
    return {
        active: p?.active ?? true,
        max_fare_per_ride: p?.max_fare_per_ride ? String(p.max_fare_per_ride) : "",
        allowed_payment_source: p?.allowed_payment_source ?? "both",
        allowed_time_windows: p?.allowed_time_windows ?? [],
    };
}

export default function PolicyPage() {
    const { id } = useParams<{ id: string }>();
    const [draft, setDraft] = useState<Draft>(policyToDraft(null));
    const [existing, setExisting] = useState<CorporatePolicy | null>(null);
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [feedback, setFeedback] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);

    const load = useCallback(async () => {
        if (!id) return;
        setLoading(true);
        try {
            const p = await getCompanyPolicy(id);
            const hasFields = p && "active" in p;
            setExisting(hasFields ? (p as CorporatePolicy) : null);
            setDraft(policyToDraft(hasFields ? (p as CorporatePolicy) : null));
        } catch (e) {
            setError(e instanceof Error ? e.message : "Failed to load");
        } finally {
            setLoading(false);
        }
    }, [id]);

    useEffect(() => {
        load();
    }, [load]);

    const addWindow = () =>
        setDraft({ ...draft, allowed_time_windows: [...draft.allowed_time_windows, emptyWindow()] });

    const updateWindow = (i: number, patch: Partial<TimeWindowPolicy>) => {
        const windows = draft.allowed_time_windows.map((w, idx) =>
            idx === i ? { ...w, ...patch } : w
        );
        setDraft({ ...draft, allowed_time_windows: windows });
    };

    const removeWindow = (i: number) => {
        setDraft({
            ...draft,
            allowed_time_windows: draft.allowed_time_windows.filter((_, idx) => idx !== i),
        });
    };

    const invalidWindow = draft.allowed_time_windows.find((w) => w.end <= w.start);

    const save = async () => {
        if (!id) return;
        if (invalidWindow) {
            setError("Every time window end must be after its start.");
            return;
        }
        setSaving(true);
        setError(null);
        setFeedback(null);
        try {
            await patchCompanyPolicy(id, {
                active: draft.active,
                max_fare_per_ride: draft.max_fare_per_ride
                    ? Number(draft.max_fare_per_ride)
                    : null,
                allowed_payment_source: draft.allowed_payment_source,
                allowed_time_windows:
                    draft.allowed_time_windows.length > 0
                        ? draft.allowed_time_windows
                        : null,
            });
            setFeedback("Policy saved");
            await load();
        } catch (e) {
            setError(e instanceof Error ? e.message : "Save failed");
        } finally {
            setSaving(false);
        }
    };

    return (
        <div className="space-y-6">
            <header className="flex items-center justify-between">
                <div>
                    <h1 className="text-2xl font-semibold">Policy</h1>
                    <p className="text-muted-foreground">
                        Limits and allowed windows for all work rides.
                    </p>
                </div>
                <Badge
                    className={
                        existing
                            ? "bg-emerald-100 text-emerald-800"
                            : "bg-gray-200 text-gray-700"
                    }
                >
                    {existing ? "Configured" : "Not set"}
                </Badge>
            </header>

            {loading ? (
                <p className="text-sm text-muted-foreground">Loading…</p>
            ) : (
                <>
                    <Card>
                        <CardContent className="space-y-4 p-4">
                            <div className="flex items-center justify-between">
                                <Label>Policy active</Label>
                                <Switch
                                    checked={draft.active}
                                    onCheckedChange={(v) => setDraft({ ...draft, active: v })}
                                />
                            </div>

                            <div>
                                <Label>Max fare per ride (CAD)</Label>
                                <Input
                                    type="number"
                                    placeholder="No cap"
                                    value={draft.max_fare_per_ride}
                                    onChange={(e) =>
                                        setDraft({ ...draft, max_fare_per_ride: e.target.value })
                                    }
                                />
                            </div>

                            <div>
                                <Label>Payment source</Label>
                                <Select
                                    value={draft.allowed_payment_source}
                                    onValueChange={(v) =>
                                        setDraft({ ...draft, allowed_payment_source: v as PaymentSourcePolicy })
                                    }
                                >
                                    <SelectTrigger>
                                        <SelectValue />
                                    </SelectTrigger>
                                    <SelectContent>
                                        {(Object.keys(PAYMENT_LABELS) as PaymentSourcePolicy[]).map((k) => (
                                            <SelectItem key={k} value={k}>
                                                {PAYMENT_LABELS[k]}
                                            </SelectItem>
                                        ))}
                                    </SelectContent>
                                </Select>
                            </div>
                        </CardContent>
                    </Card>

                    <Card>
                        <CardContent className="space-y-3 p-4">
                            <div className="flex items-center justify-between">
                                <div>
                                    <h2 className="font-medium">Allowed time windows</h2>
                                    <p className="text-xs text-muted-foreground">
                                        Leave empty for 24/7 bookings.
                                    </p>
                                </div>
                                <Button size="sm" variant="outline" onClick={addWindow}>
                                    <Plus className="mr-1 h-3.5 w-3.5" /> Add window
                                </Button>
                            </div>
                            {draft.allowed_time_windows.length === 0 && (
                                <p className="text-sm text-muted-foreground">No windows set.</p>
                            )}
                            {draft.allowed_time_windows.map((w, i) => (
                                <div
                                    key={i}
                                    className="grid gap-2 sm:grid-cols-[120px_1fr_1fr_auto] items-center"
                                >
                                    <Select
                                        value={w.day}
                                        onValueChange={(v) =>
                                            updateWindow(i, { day: v as TimeWindowPolicy["day"] })
                                        }
                                    >
                                        <SelectTrigger>
                                            <SelectValue />
                                        </SelectTrigger>
                                        <SelectContent>
                                            {DAYS.map((d) => (
                                                <SelectItem key={d} value={d}>
                                                    {DAY_LABEL[d]}
                                                </SelectItem>
                                            ))}
                                        </SelectContent>
                                    </Select>
                                    <Input
                                        type="time"
                                        value={w.start}
                                        onChange={(e) => updateWindow(i, { start: e.target.value })}
                                    />
                                    <Input
                                        type="time"
                                        value={w.end}
                                        onChange={(e) => updateWindow(i, { end: e.target.value })}
                                    />
                                    <Button
                                        size="icon"
                                        variant="ghost"
                                        onClick={() => removeWindow(i)}
                                    >
                                        <Trash2 className="h-4 w-4" />
                                    </Button>
                                </div>
                            ))}
                            {invalidWindow && (
                                <p className="text-xs text-red-600">
                                    Every window end must be after start.
                                </p>
                            )}
                        </CardContent>
                    </Card>

                    <div className="flex items-center gap-3">
                        <Button onClick={save} disabled={saving}>
                            <Save className="mr-1 h-4 w-4" />
                            {saving ? "Saving…" : "Save policy"}
                        </Button>
                        {feedback && (
                            <span className="text-xs text-emerald-700">{feedback}</span>
                        )}
                        {error && <span className="text-xs text-red-600">{error}</span>}
                    </div>
                </>
            )}
        </div>
    );
}
