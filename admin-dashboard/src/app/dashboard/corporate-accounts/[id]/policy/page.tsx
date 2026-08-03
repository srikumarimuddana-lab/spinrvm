"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import {
    ArrowLeft,
    Clock,
    Plus,
    Save,
    ShieldCheck,
    Trash2,
} from "lucide-react";

import {
    CorporatePolicy,
    PaymentSourcePolicy,
    TimeWindowPolicy,
    getCompanyPolicy,
    patchCompanyPolicy,
} from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";

const DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"] as const;
type Day = (typeof DAYS)[number];

const DAY_LABELS: Record<Day, string> = {
    mon: "Monday", tue: "Tuesday", wed: "Wednesday",
    thu: "Thursday", fri: "Friday", sat: "Saturday", sun: "Sunday",
};

const PAYMENT_SOURCE_LABELS: Record<PaymentSourcePolicy, string> = {
    allowance_only: "Allowance only",
    master_only: "Company master wallet only",
    both: "Allowance or personal card",
};

function emptyWindow(): TimeWindowPolicy {
    return { day: "mon", start: "09:00", end: "18:00" };
}

function TimeWindowRow({
    window: w,
    index: _index,
    onChange,
    onRemove,
}: {
    window: TimeWindowPolicy;
    index: number;
    onChange: (w: TimeWindowPolicy) => void;
    onRemove: () => void;
}) {
    const invalid = w.end <= w.start;
    return (
        <div className="flex flex-wrap items-center gap-2">
            <Select value={w.day} onValueChange={(v) => onChange({ ...w, day: v as Day })}>
                <SelectTrigger className="w-36">
                    <SelectValue />
                </SelectTrigger>
                <SelectContent>
                    {DAYS.map((d) => (
                        <SelectItem key={d} value={d}>{DAY_LABELS[d]}</SelectItem>
                    ))}
                </SelectContent>
            </Select>
            <Input
                type="time"
                value={w.start}
                onChange={(e) => onChange({ ...w, start: e.target.value })}
                className="w-32"
            />
            <span className="text-muted-foreground text-sm">to</span>
            <Input
                type="time"
                value={w.end}
                onChange={(e) => onChange({ ...w, end: e.target.value })}
                className={`w-32 ${invalid ? "border-destructive" : ""}`}
            />
            {invalid && (
                <span className="text-xs text-destructive">End must be after start</span>
            )}
            <Button size="sm" variant="ghost" className="text-destructive hover:text-destructive/80" onClick={onRemove}>
                <Trash2 className="h-3.5 w-3.5" />
            </Button>
        </div>
    );
}

export default function CompanyPolicyPage() {
    const { id } = useParams<{ id: string }>();
    const companyId = id as string;

    const [loading, setLoading] = useState(true);
    const [exists, setExists] = useState(false);
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [success, setSuccess] = useState(false);

    // Form state
    const [active, setActive] = useState(true);
    const [maxFare, setMaxFare] = useState<string>("");
    const [paymentSource, setPaymentSource] = useState<PaymentSourcePolicy>("both");
    const [timeWindows, setTimeWindows] = useState<TimeWindowPolicy[]>([]);

    const load = useCallback(async () => {
        setLoading(true);
        try {
            const data = await getCompanyPolicy(companyId);
            const policy = data as CorporatePolicy;
            if (policy?.allowed_payment_source) {
                setExists(true);
                setActive(policy.active ?? true);
                setMaxFare(policy.max_fare_per_ride != null ? String(policy.max_fare_per_ride) : "");
                setPaymentSource(policy.allowed_payment_source);
                setTimeWindows(policy.allowed_time_windows ?? []);
            }
        } catch (e: any) {
            setError(e?.message ?? "Failed to load policy");
        } finally {
            setLoading(false);
        }
    }, [companyId]);

    useEffect(() => {
        if (companyId) load();
    }, [companyId, load]);

    function addWindow() {
        setTimeWindows((prev) => [...prev, emptyWindow()]);
    }

    function updateWindow(index: number, w: TimeWindowPolicy) {
        setTimeWindows((prev) => prev.map((x, i) => (i === index ? w : x)));
    }

    function removeWindow(index: number) {
        setTimeWindows((prev) => prev.filter((_, i) => i !== index));
    }

    const hasInvalidWindow = timeWindows.some((w) => w.end <= w.start);

    async function handleSave() {
        if (hasInvalidWindow) {
            setError("Fix time window errors before saving.");
            return;
        }
        setSaving(true);
        setError(null);
        setSuccess(false);
        try {
            const patch: Partial<Omit<CorporatePolicy, "id" | "company_id">> = {
                active,
                allowed_payment_source: paymentSource,
                max_fare_per_ride: maxFare.trim() !== "" ? Number(maxFare) : null,
                allowed_time_windows: timeWindows.length > 0 ? timeWindows : null,
            };
            await patchCompanyPolicy(companyId, patch);
            setExists(true);
            setSuccess(true);
            setTimeout(() => setSuccess(false), 3000);
        } catch (e: any) {
            setError(e?.message ?? "Save failed");
        } finally {
            setSaving(false);
        }
    }

    if (loading) {
        return (
            <div className="flex justify-center py-20">
                <div className="h-6 w-6 animate-spin rounded-full border-2 border-primary border-t-transparent" />
            </div>
        );
    }

    return (
        <div className="space-y-6 p-6 max-w-2xl">
            {/* Header */}
            <div className="flex items-center gap-3">
                <Link href={`/dashboard/corporate-accounts/${companyId}/members`}>
                    <Button variant="ghost" size="sm">
                        <ArrowLeft className="mr-1 h-4 w-4" />
                        Members
                    </Button>
                </Link>
                <h1 className="text-2xl font-semibold flex items-center gap-2">
                    <ShieldCheck className="h-5 w-5 text-muted-foreground" />
                    Ride Policy
                </h1>
                {exists && (
                    <Badge variant="secondary" className="bg-emerald-100 text-emerald-800">
                        Configured
                    </Badge>
                )}
                {!exists && (
                    <Badge variant="secondary" className="bg-yellow-100 text-yellow-800">
                        Not set
                    </Badge>
                )}
            </div>

            <p className="text-sm text-muted-foreground">
                Controls which rides employees can expense. Rules are evaluated at booking time.
                Leaving a field blank means no restriction for that dimension.
            </p>

            {/* Active toggle */}
            <Card>
                <CardContent className="flex items-center justify-between p-4">
                    <div>
                        <p className="font-medium">Policy active</p>
                        <p className="text-sm text-muted-foreground">
                            When off, employees pay personally regardless of allowance balance.
                        </p>
                    </div>
                    <Switch checked={active} onCheckedChange={setActive} />
                </CardContent>
            </Card>

            {/* Fare cap */}
            <Card>
                <CardContent className="space-y-3 p-4">
                    <div>
                        <Label htmlFor="max-fare">Max fare per ride (CAD)</Label>
                        <p className="text-xs text-muted-foreground mb-2">
                            Rides estimated above this amount are blocked. Leave blank for no cap.
                        </p>
                        <div className="flex items-center gap-2">
                            <span className="text-muted-foreground">$</span>
                            <Input
                                id="max-fare"
                                type="number"
                                min={1}
                                max={10000}
                                step={1}
                                placeholder="e.g. 80"
                                value={maxFare}
                                onChange={(e) => setMaxFare(e.target.value)}
                                className="w-36"
                            />
                            {maxFare && (
                                <Button
                                    size="sm"
                                    variant="ghost"
                                    onClick={() => setMaxFare("")}
                                    className="text-muted-foreground"
                                >
                                    Clear
                                </Button>
                            )}
                        </div>
                    </div>
                </CardContent>
            </Card>

            {/* Payment source */}
            <Card>
                <CardContent className="space-y-3 p-4">
                    <div>
                        <Label>Allowed payment source</Label>
                        <p className="text-xs text-muted-foreground mb-2">
                            Controls whether the rider&apos;s personal card can be used as fallback.
                        </p>
                        <Select
                            value={paymentSource}
                            onValueChange={(v) => setPaymentSource(v as PaymentSourcePolicy)}
                        >
                            <SelectTrigger className="w-72">
                                <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                                {(Object.keys(PAYMENT_SOURCE_LABELS) as PaymentSourcePolicy[]).map(
                                    (k) => (
                                        <SelectItem key={k} value={k}>
                                            {PAYMENT_SOURCE_LABELS[k]}
                                        </SelectItem>
                                    )
                                )}
                            </SelectContent>
                        </Select>
                    </div>
                </CardContent>
            </Card>

            {/* Time windows */}
            <Card>
                <CardContent className="space-y-4 p-4">
                    <div className="flex items-center justify-between">
                        <div>
                            <Label className="flex items-center gap-1.5">
                                <Clock className="h-4 w-4" /> Allowed booking windows
                            </Label>
                            <p className="text-xs text-muted-foreground mt-1">
                                Add day + time ranges when work rides are permitted. Empty = 24/7.
                            </p>
                        </div>
                        <Button size="sm" variant="outline" onClick={addWindow}>
                            <Plus className="mr-1 h-4 w-4" /> Add window
                        </Button>
                    </div>

                    {timeWindows.length === 0 && (
                        <p className="text-sm text-muted-foreground italic">
                            No restrictions — rides allowed at any time.
                        </p>
                    )}

                    <div className="space-y-2">
                        {timeWindows.map((w, i) => (
                            <TimeWindowRow
                                key={i}
                                window={w}
                                index={i}
                                onChange={(nw) => updateWindow(i, nw)}
                                onRemove={() => removeWindow(i)}
                            />
                        ))}
                    </div>
                </CardContent>
            </Card>

            <Separator />

            {/* Save */}
            <div className="flex items-center gap-4">
                <Button
                    onClick={handleSave}
                    disabled={saving || hasInvalidWindow}
                    className="min-w-[120px]"
                >
                    <Save className="mr-2 h-4 w-4" />
                    {saving ? "Saving…" : exists ? "Save changes" : "Create policy"}
                </Button>

                {success && (
                    <span className="text-sm text-emerald-600 font-medium">
                        ✓ Policy saved
                    </span>
                )}
                {error && (
                    <span className="text-sm text-red-600">{error}</span>
                )}
            </div>
        </div>
    );
}
