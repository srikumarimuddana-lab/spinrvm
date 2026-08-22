"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { ArrowLeft, CreditCard, RefreshCw } from "lucide-react";

import {
    CompanySubscriptionResponse,
    CorporateSubscriptionPlan,
    assignCompanySubscription,
    cancelCompanySubscription,
    getCompanySubscription,
    getCorporateSubscriptionPlans,
} from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Label } from "@/components/ui/label";
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from "@/components/ui/table";
import { useToast } from "@/components/ui/use-toast";

const STATUS_BADGE: Record<string, string> = {
    active: "bg-success/15 text-success",
    past_due: "bg-warning/15 text-warning",
    cancelled: "bg-muted text-muted-foreground",
};

function formatDate(iso: string | null) {
    if (!iso) return "—";
    return new Date(iso).toLocaleDateString("en-CA", { year: "numeric", month: "short", day: "numeric" });
}

export default function CompanySubscriptionPage() {
    const { id } = useParams<{ id: string }>();
    const companyId = id as string;
    const { toast } = useToast();

    const [loading, setLoading] = useState(true);
    const [data, setData] = useState<CompanySubscriptionResponse | null>(null);
    const [plans, setPlans] = useState<CorporateSubscriptionPlan[]>([]);
    const [selectedPlan, setSelectedPlan] = useState<string>("");
    const [assigning, setAssigning] = useState(false);
    const [cancelling, setCancelling] = useState(false);
    const [cancelAtPeriodEnd, setCancelAtPeriodEnd] = useState(true);

    const load = useCallback(async () => {
        setLoading(true);
        try {
            const [sub, plansResp] = await Promise.all([
                getCompanySubscription(companyId),
                getCorporateSubscriptionPlans(),
            ]);
            setData(sub);
            setPlans(plansResp.plans);
        } catch (e: any) {
            toast({ title: "Failed to load subscription", description: e?.message, variant: "destructive" });
        } finally {
            setLoading(false);
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [companyId]);

    useEffect(() => {
        if (companyId) load();
    }, [companyId, load]);

    async function handleAssign() {
        if (!selectedPlan) return;
        setAssigning(true);
        try {
            await assignCompanySubscription(companyId, selectedPlan);
            toast({ title: "Subscription assigned" });
            setSelectedPlan("");
            await load();
        } catch (e: any) {
            // The route is deliberately flag-gated (ships dark) — surface a
            // clear message rather than a generic failure when it's off.
            toast({
                title: "Could not assign subscription",
                description: e?.message ?? "Corporate subscription billing may not be enabled yet.",
                variant: "destructive",
            });
        } finally {
            setAssigning(false);
        }
    }

    async function handleCancel() {
        setCancelling(true);
        try {
            await cancelCompanySubscription(companyId, cancelAtPeriodEnd);
            toast({
                title: cancelAtPeriodEnd ? "Subscription will cancel at period end" : "Subscription cancelled",
            });
            await load();
        } catch (e: any) {
            toast({ title: "Failed to cancel subscription", description: e?.message, variant: "destructive" });
        } finally {
            setCancelling(false);
        }
    }

    if (loading) {
        return (
            <div className="flex justify-center py-20">
                <div className="h-6 w-6 animate-spin rounded-full border-2 border-primary border-t-transparent" />
            </div>
        );
    }

    const current = data?.current ?? null;

    return (
        <div className="space-y-6 p-6 max-w-2xl">
            <div className="flex items-center gap-3">
                <Link href={`/dashboard/corporate-accounts/${companyId}`}>
                    <Button variant="ghost" size="sm">
                        <ArrowLeft className="mr-1 h-4 w-4" />
                        Back
                    </Button>
                </Link>
                <h1 className="text-2xl font-semibold flex items-center gap-2">
                    <CreditCard className="h-5 w-5 text-muted-foreground" />
                    Subscription Billing
                </h1>
                <Button variant="ghost" size="sm" onClick={load}>
                    <RefreshCw className="h-3.5 w-3.5" />
                </Button>
            </div>

            <p className="text-sm text-muted-foreground">
                Flat recurring platform fee, billed to the company via Stripe. This is separate from
                ride fares — riders and drivers are never affected by this billing.
            </p>

            <Card>
                <CardContent className="p-4 space-y-3">
                    {current ? (
                        <>
                            <div className="flex items-center justify-between">
                                <div>
                                    <p className="font-medium">{current.plan_name}</p>
                                    <p className="text-sm text-muted-foreground">
                                        ${current.price} / month
                                    </p>
                                </div>
                                <Badge className={STATUS_BADGE[current.status] ?? ""}>
                                    {current.status.replace("_", " ")}
                                </Badge>
                            </div>
                            <div className="text-sm text-muted-foreground space-y-1">
                                <p>Current period ends: {formatDate(current.current_period_end)}</p>
                                {current.cancel_at_period_end && (
                                    <p className="text-warning">
                                        Cancels at period end — access continues until then.
                                    </p>
                                )}
                            </div>

                            <div className="flex items-center gap-3 pt-2 border-t">
                                <div className="flex items-center gap-2">
                                    <Switch checked={cancelAtPeriodEnd} onCheckedChange={setCancelAtPeriodEnd} />
                                    <Label className="text-xs">Cancel at period end (keep access until then)</Label>
                                </div>
                                <Button
                                    variant="outline"
                                    size="sm"
                                    className="text-destructive ml-auto"
                                    disabled={cancelling || current.status === "cancelled"}
                                    onClick={handleCancel}
                                >
                                    {cancelling ? "Cancelling…" : "Cancel subscription"}
                                </Button>
                            </div>
                        </>
                    ) : (
                        <>
                            <p className="text-sm text-muted-foreground italic">
                                No active subscription.
                            </p>
                            <div className="flex items-center gap-2">
                                <Select value={selectedPlan} onValueChange={setSelectedPlan}>
                                    <SelectTrigger className="w-64">
                                        <SelectValue placeholder="Choose a plan" />
                                    </SelectTrigger>
                                    <SelectContent>
                                        {plans.map((p) => (
                                            <SelectItem key={p.id} value={p.id}>
                                                {p.name} — ${p.monthly_price}/mo
                                            </SelectItem>
                                        ))}
                                    </SelectContent>
                                </Select>
                                <Button onClick={handleAssign} disabled={!selectedPlan || assigning}>
                                    {assigning ? "Assigning…" : "Assign plan"}
                                </Button>
                            </div>
                            {plans.length === 0 && (
                                <p className="text-xs text-muted-foreground">
                                    No subscription plans configured.
                                </p>
                            )}
                        </>
                    )}
                </CardContent>
            </Card>

            {(data?.history?.length ?? 0) > 0 && (
                <Card>
                    <CardContent className="p-0">
                        <Table>
                            <TableHeader>
                                <TableRow>
                                    <TableHead>Plan</TableHead>
                                    <TableHead>Price</TableHead>
                                    <TableHead>Status</TableHead>
                                    <TableHead>Started</TableHead>
                                    <TableHead>Cancelled</TableHead>
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                {data!.history.map((row) => (
                                    <TableRow key={row.id}>
                                        <TableCell className="text-sm">{row.plan_name}</TableCell>
                                        <TableCell className="text-sm">${row.price}</TableCell>
                                        <TableCell>
                                            <Badge className={`text-[10px] ${STATUS_BADGE[row.status] ?? ""}`}>
                                                {row.status.replace("_", " ")}
                                            </Badge>
                                        </TableCell>
                                        <TableCell className="text-xs text-muted-foreground">
                                            {formatDate(row.started_at)}
                                        </TableCell>
                                        <TableCell className="text-xs text-muted-foreground">
                                            {formatDate(row.cancelled_at)}
                                        </TableCell>
                                    </TableRow>
                                ))}
                            </TableBody>
                        </Table>
                    </CardContent>
                </Card>
            )}
        </div>
    );
}
