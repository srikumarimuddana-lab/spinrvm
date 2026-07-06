"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import type {
    BillingSummary,
    CorporateMember,
    AllowanceRequestRow,
} from "@/lib/api";
import {
    getCompanyBillingSummary,
    listCompanyMembers,
    listCompanyAllowanceRequests,
} from "@/lib/companyApi";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
    Activity,
    ArrowRight,
    CreditCard,
    ScrollText,
    Users,
    Wallet,
} from "lucide-react";

function formatCAD(n: number | undefined) {
    return (n ?? 0).toLocaleString("en-CA", {
        style: "currency",
        currency: "CAD",
    });
}

export default function OverviewPage() {
    const { id } = useParams<{ id: string }>();
    const [summary, setSummary] = useState<BillingSummary | null>(null);
    const [members, setMembers] = useState<CorporateMember[]>([]);
    const [pending, setPending] = useState<AllowanceRequestRow[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        if (!id) return;
        Promise.all([
            getCompanyBillingSummary(id).catch(() => null),
            listCompanyMembers(id).catch(() => []),
            listCompanyAllowanceRequests(id, "pending").catch(() => []),
        ])
            .then(([s, m, p]) => {
                setSummary(s);
                setMembers(m);
                setPending(p);
            })
            .catch((e) =>
                setError(e instanceof Error ? e.message : "Failed to load")
            )
            .finally(() => setLoading(false));
    }, [id]);

    const activeMembers = members.filter((m) => m.status === "active").length;

    return (
        <div className="space-y-6">
            <header>
                <h1 className="text-2xl font-semibold">Overview</h1>
                <p className="text-muted-foreground">
                    Key metrics for the current month.
                </p>
            </header>

            {error && (
                <p className="rounded bg-red-50 p-3 text-sm text-red-700">{error}</p>
            )}

            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                <MetricCard
                    icon={Wallet}
                    label="Wallet balance"
                    value={formatCAD(summary?.wallet_balance)}
                    sub={summary?.wallet_currency ?? "CAD"}
                />
                <MetricCard
                    icon={CreditCard}
                    label="MTD spend"
                    value={formatCAD(summary?.total)}
                    sub={`${summary?.ride_count ?? 0} rides`}
                />
                <MetricCard
                    icon={Activity}
                    label="Avg fare"
                    value={formatCAD(summary?.avg_fare)}
                    sub="Per work ride"
                />
                <MetricCard
                    icon={Users}
                    label="Active members"
                    value={String(activeMembers)}
                    sub={`${members.length} total`}
                />
            </div>

            <div className="grid gap-4 lg:grid-cols-2">
                <Card>
                    <CardContent className="p-6">
                        <div className="mb-4 flex items-center justify-between">
                            <h2 className="font-medium">Top spend this month</h2>
                            <Link
                                href={`/company-portal/${id}/billing`}
                                className="text-xs text-primary hover:underline inline-flex items-center gap-1"
                            >
                                Full report <ArrowRight className="h-3 w-3" />
                            </Link>
                        </div>
                        {loading && <p className="text-sm text-muted-foreground">Loading…</p>}
                        {!loading && (summary?.by_member?.length ?? 0) === 0 && (
                            <p className="text-sm text-muted-foreground">No work rides yet.</p>
                        )}
                        <ul className="divide-y divide-border">
                            {summary?.by_member?.slice(0, 5).map((row) => (
                                <li
                                    key={row.member_id}
                                    className="flex items-center justify-between py-2 text-sm"
                                >
                                    <span className="font-mono text-xs">{row.member_id}</span>
                                    <span className="flex items-center gap-2">
                                        <Badge variant="secondary" className="text-[10px]">
                                            {row.ride_count} rides
                                        </Badge>
                                        <span className="font-medium">
                                            {formatCAD(row.total)}
                                        </span>
                                    </span>
                                </li>
                            ))}
                        </ul>
                    </CardContent>
                </Card>

                <Card>
                    <CardContent className="p-6">
                        <div className="mb-4 flex items-center justify-between">
                            <h2 className="font-medium">Pending allowance requests</h2>
                            <Link
                                href={`/company-portal/${id}/allowance-requests`}
                                className="text-xs text-primary hover:underline inline-flex items-center gap-1"
                            >
                                Review <ArrowRight className="h-3 w-3" />
                            </Link>
                        </div>
                        {loading && <p className="text-sm text-muted-foreground">Loading…</p>}
                        {!loading && pending.length === 0 && (
                            <p className="text-sm text-muted-foreground">
                                No requests awaiting review.
                            </p>
                        )}
                        <ul className="divide-y divide-border">
                            {pending.slice(0, 5).map((r) => (
                                <li
                                    key={r.id}
                                    className="flex items-center justify-between py-2 text-sm"
                                >
                                    <span className="max-w-[60%] truncate">
                                        {r.reason}
                                    </span>
                                    <span className="flex items-center gap-2">
                                        <ScrollText className="h-3.5 w-3.5 text-muted-foreground" />
                                        <span className="font-medium">
                                            {formatCAD(r.amount)}
                                        </span>
                                    </span>
                                </li>
                            ))}
                        </ul>
                    </CardContent>
                </Card>
            </div>
        </div>
    );
}

function MetricCard({
    icon: Icon,
    label,
    value,
    sub,
}: {
    icon: React.ComponentType<{ className?: string }>;
    label: string;
    value: string;
    sub: string;
}) {
    return (
        <Card>
            <CardContent className="p-4">
                <div className="mb-2 flex items-center gap-2 text-xs text-muted-foreground">
                    <Icon className="h-4 w-4" />
                    {label}
                </div>
                <div className="text-2xl font-semibold">{value}</div>
                <div className="text-xs text-muted-foreground">{sub}</div>
            </CardContent>
        </Card>
    );
}
