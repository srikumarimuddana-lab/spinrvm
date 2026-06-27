"use client";

import { useEffect, useState } from "react";
import {
    getReferralAnalytics, getFailedReferralClaims, requeueFailedReferral, getServiceAreas,
    type ReferralAnalytics as Data, type FailedReferralClaim,
} from "@/lib/api";
import { Card, CardContent } from "@/components/ui/card";
import { formatCurrency } from "@/lib/utils";
import {
    LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid, Legend,
} from "recharts";
import { Users, CheckCircle2, Clock, XCircle, DollarSign, Percent, Gift, TrendingUp, RefreshCw } from "lucide-react";

/**
 * Referral analytics hub — redemption funnel, amounts paid, and a daily
 * redemption trend, filterable by service area and date range. The rider/driver
 * split comes from the parent toggle (the `source` prop) so it stays in sync
 * with the leaderboard rendered alongside it.
 */
export default function ReferralAnalytics({ source }: { source: "driver" | "rider" }) {
    const [areas, setAreas] = useState<{ id: string; name: string }[]>([]);
    const [serviceAreaId, setServiceAreaId] = useState<string>("");
    const [start, setStart] = useState<string>("");
    const [end, setEnd] = useState<string>("");

    const [data, setData] = useState<Data | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");

    // Service areas for the filter dropdown — loaded once.
    useEffect(() => {
        getServiceAreas()
            .then((rows) => setAreas((rows || []).map((a: any) => ({ id: a.id, name: a.name }))))
            .catch(() => setAreas([]));
    }, []);

    useEffect(() => {
        let cancelled = false;
        setLoading(true);
        setError("");
        getReferralAnalytics({ source, serviceAreaId: serviceAreaId || null, start: start || null, end: end || null })
            .then((d) => { if (!cancelled) setData(d); })
            .catch((e) => { if (!cancelled) setError(e?.message || "Failed to load referral analytics"); })
            .finally(() => { if (!cancelled) setLoading(false); });
        return () => { cancelled = true; };
    }, [source, serviceAreaId, start, end]);

    // Failed referral claims (e.g. the pre-198 constraint-bug backlog) — review + re-queue.
    const [failed, setFailed] = useState<FailedReferralClaim[]>([]);
    const [requeuing, setRequeuing] = useState<string | null>(null);
    useEffect(() => {
        let cancelled = false;
        getFailedReferralClaims({ source, limit: 200 })
            .then((d) => { if (!cancelled) setFailed(d?.claims ?? []); })
            .catch(() => { if (!cancelled) setFailed([]); });
        return () => { cancelled = true; };
    }, [source]);
    const requeue = async (refereeUserId: string) => {
        setRequeuing(refereeUserId);
        try {
            await requeueFailedReferral(refereeUserId);
            setFailed((prev) => prev.filter((c) => c.referee_user_id !== refereeUserId));
        } finally {
            setRequeuing(null);
        }
    };

    const f = data?.funnel;
    const areaFiltered = !!serviceAreaId;
    const ratePct = f?.redemption_rate != null ? `${Math.round(f.redemption_rate * 100)}%` : "—";

    return (
        <div className="space-y-5">
            {/* Filters */}
            <div className="flex flex-wrap items-end gap-3">
                <Field label="Service area">
                    <select
                        className="border rounded-lg px-3 py-2 text-sm bg-background min-w-[160px]"
                        value={serviceAreaId}
                        onChange={(e) => setServiceAreaId(e.target.value)}
                    >
                        <option value="">All areas</option>
                        {areas.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
                    </select>
                </Field>
                <Field label="From">
                    <input type="date" className="border rounded-lg px-3 py-2 text-sm bg-background" value={start} onChange={(e) => setStart(e.target.value)} />
                </Field>
                <Field label="To">
                    <input type="date" className="border rounded-lg px-3 py-2 text-sm bg-background" value={end} onChange={(e) => setEnd(e.target.value)} />
                </Field>
                {(serviceAreaId || start || end) && (
                    <button
                        onClick={() => { setServiceAreaId(""); setStart(""); setEnd(""); }}
                        className="text-sm text-muted-foreground hover:text-foreground underline underline-offset-2 pb-2"
                    >
                        Clear filters
                    </button>
                )}
            </div>

            {error ? (
                <div className="text-sm text-red-600 dark:text-red-400 py-10 text-center">{error}</div>
            ) : loading || !f ? (
                <div className="text-sm text-muted-foreground py-10 text-center">Loading referral analytics…</div>
            ) : (
                <>
                    {/* Redemption funnel + amounts */}
                    <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
                        <Stat icon={Users} label="Referred (sign-ups)" accent="text-violet-600 dark:text-violet-400"
                            value={areaFiltered ? "—" : (f.total_referred ?? 0).toLocaleString()}
                            hint={areaFiltered ? "Not area-tagged until they qualify" : undefined} />
                        <Stat icon={Gift} label="Qualified" accent="text-sky-600 dark:text-sky-400" value={f.qualified.toLocaleString()} />
                        <Stat icon={CheckCircle2} label="Redeemed (paid)" accent="text-emerald-600 dark:text-emerald-400" value={f.redeemed.toLocaleString()} />
                        <Stat icon={Percent} label="Redemption rate" accent="text-amber-600 dark:text-amber-400" value={ratePct}
                            hint={areaFiltered ? "Needs all-areas referred count" : undefined} />
                        <Stat icon={Clock} label="In progress" accent="text-blue-600 dark:text-blue-400" value={f.processing.toLocaleString()} />
                        <Stat icon={XCircle} label="Failed" accent="text-red-600 dark:text-red-400" value={f.failed.toLocaleString()} />
                        <Stat icon={DollarSign} label="Total paid out" accent="text-emerald-600 dark:text-emerald-400" value={formatCurrency(Number(f.total_paid))} />
                        <Stat icon={DollarSign} label="Referrer paid" accent="text-emerald-600 dark:text-emerald-400" value={formatCurrency(Number(f.referrer_paid ?? 0))}
                            hint="Actual paid to referrers" />
                        <Stat icon={DollarSign} label="Referee paid" accent="text-emerald-600 dark:text-emerald-400" value={formatCurrency(Number(f.referee_paid ?? 0))}
                            hint="Actual paid to referees (not the configured reward)" />
                        <Stat icon={DollarSign} label="Avg per redemption" accent="text-emerald-600 dark:text-emerald-400" value={formatCurrency(Number(f.avg_paid))} />
                    </div>

                    {/* Redemption trend */}
                    <Card className="border-border/50">
                        <CardContent className="p-4">
                            <div className="flex items-center gap-2 mb-3">
                                <TrendingUp className="h-4 w-4 text-emerald-500" />
                                <h3 className="text-sm font-semibold">Redemptions over time</h3>
                            </div>
                            {data!.trend.length === 0 ? (
                                <p className="text-sm text-muted-foreground py-8 text-center">No redemptions in this range.</p>
                            ) : (
                                <ResponsiveContainer width="100%" height={240}>
                                    <LineChart data={data!.trend.map((t) => ({ ...t, paidNum: Number(t.paid) }))} margin={{ top: 8, right: 8, left: -8, bottom: 0 }}>
                                        <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" vertical={false} />
                                        <XAxis dataKey="date" tick={{ fontSize: 10, fill: "hsl(var(--muted-foreground))" }} axisLine={false} tickLine={false} />
                                        <YAxis yAxisId="left" allowDecimals={false} tick={{ fontSize: 10, fill: "hsl(var(--muted-foreground))" }} axisLine={false} tickLine={false} />
                                        <YAxis yAxisId="right" orientation="right" tick={{ fontSize: 10, fill: "hsl(var(--muted-foreground))" }} axisLine={false} tickLine={false} />
                                        <Tooltip
                                            contentStyle={{ background: "hsl(var(--background))", border: "1px solid hsl(var(--border))", borderRadius: 8, fontSize: 12 }}
                                            formatter={(v: any, name: any) => (name === "Paid" ? formatCurrency(Number(v || 0)) : v)}
                                        />
                                        <Legend wrapperStyle={{ fontSize: 12 }} />
                                        <Line yAxisId="left" type="monotone" dataKey="redeemed" name="Redeemed" stroke="hsl(var(--primary))" strokeWidth={2} dot={false} />
                                        <Line yAxisId="right" type="monotone" dataKey="paidNum" name="Paid" stroke="#10b981" strokeWidth={2} dot={false} />
                                    </LineChart>
                                </ResponsiveContainer>
                            )}
                        </CardContent>
                    </Card>

                    {/* Failed claims — review + re-queue (e.g. the pre-198 backlog) */}
                    {failed.length > 0 && (
                        <Card className="border-border/50">
                            <CardContent className="p-4">
                                <div className="flex items-center gap-2 mb-1">
                                    <XCircle className="h-4 w-4 text-red-500" />
                                    <h3 className="text-sm font-semibold">Failed claims ({failed.length})</h3>
                                </div>
                                <p className="text-[11px] text-muted-foreground mb-3">
                                    Re-queue replays a claim on the next payout tick (~5 min). Safe for the
                                    constraint-bug backlog (the credit was reversed); do not re-queue a claim
                                    whose logs show a failed reversal — it would double-credit.
                                </p>
                                <div className="space-y-2">
                                    {failed.map((c) => (
                                        <div key={c.id} className="flex items-center justify-between gap-3 rounded-md border border-border/50 p-2 text-sm">
                                            <div className="min-w-0">
                                                <div className="truncate font-medium">{c.referrer_name} → {c.referee_name}</div>
                                                <div className="text-[11px] text-muted-foreground">
                                                    {c.kind} · referrer ${c.referrer_reward} / referee ${c.referee_reward} · {String(c.created_at).slice(0, 10)}
                                                </div>
                                            </div>
                                            <button
                                                onClick={() => requeue(c.referee_user_id)}
                                                disabled={requeuing === c.referee_user_id}
                                                className="shrink-0 inline-flex items-center gap-1 rounded-md border border-border px-2 py-1 text-xs font-medium hover:bg-muted disabled:opacity-50"
                                            >
                                                <RefreshCw className={`h-3 w-3 ${requeuing === c.referee_user_id ? "animate-spin" : ""}`} />
                                                Re-queue
                                            </button>
                                        </div>
                                    ))}
                                </div>
                            </CardContent>
                        </Card>
                    )}
                </>
            )}
        </div>
    );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
    return (
        <div className="flex flex-col gap-1">
            <span className="text-[11px] uppercase tracking-wide text-muted-foreground font-medium">{label}</span>
            {children}
        </div>
    );
}

function Stat({ icon: Icon, label, value, accent, hint }: { icon: any; label: string; value: string; accent: string; hint?: string }) {
    return (
        <Card className="border-border/50">
            <CardContent className="p-4">
                <div className="flex items-center gap-2">
                    <Icon className={`h-4 w-4 ${accent}`} />
                    <p className="text-[11px] uppercase tracking-wide text-muted-foreground font-medium">{label}</p>
                </div>
                <p className="text-2xl font-bold mt-1.5">{value}</p>
                {hint && <p className="text-[11px] text-muted-foreground mt-0.5">{hint}</p>}
            </CardContent>
        </Card>
    );
}
