"use client";

import { useEffect, useState } from "react";
import { getReferralLeaderboard, getRiderReferralLeaderboard, type ReferralLeaderboard as Data } from "@/lib/api";
import { Card, CardContent } from "@/components/ui/card";
import { formatCurrency } from "@/lib/utils";
import { Gift, Users, Award, Trophy } from "lucide-react";

/**
 * Fleet-wide referral stats — top referrers leaderboard plus totals.
 * Shared by the standalone Referrals page and the Earnings → Referrals tab so
 * the two can never diverge.
 */
export default function ReferralLeaderboard({ limit = 20, source = "driver" }: { limit?: number; source?: "driver" | "rider" }) {
    const [data, setData] = useState<Data | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");

    useEffect(() => {
        let cancelled = false;
        setLoading(true);
        setError("");
        const fetcher = source === "rider" ? getRiderReferralLeaderboard : getReferralLeaderboard;
        fetcher(limit)
            .then((d) => { if (!cancelled) setData(d); })
            .catch((e) => { if (!cancelled) setError(e?.message || "Failed to load referral stats"); })
            .finally(() => { if (!cancelled) setLoading(false); });
        return () => { cancelled = true; };
    }, [limit, source]);

    if (loading) {
        return <div className="text-sm text-muted-foreground py-10 text-center">Loading referral stats…</div>;
    }
    if (error) {
        return <div className="text-sm text-destructive py-10 text-center">{error}</div>;
    }
    if (!data) return null;

    const leaders = data.leaders || [];

    return (
        <div className="space-y-5">
            {/* Fleet summary */}
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                <SummaryCard icon={Gift} label="Total Referrals" value={(data.fleet_total_referrals ?? 0).toLocaleString()} accent="text-violet-600 dark:text-violet-400" />
                <SummaryCard icon={Users} label="Active Referrers" value={(data.fleet_total_referrers ?? 0).toLocaleString()} accent="text-sky-600 dark:text-sky-400" />
                <SummaryCard
                    icon={Award}
                    label="Reward Terms"
                    value={`${formatCurrency(data.reward_amount)} / ${data.rides_required} rides`}
                    accent="text-emerald-600 dark:text-emerald-400"
                />
            </div>

            {/* Top referrers */}
            <Card className="border-border/50">
                <CardContent className="p-0">
                    <div className="flex items-center gap-2 px-4 py-3 border-b border-border/50">
                        <Trophy className="h-4 w-4 text-amber-500" />
                        <h3 className="text-sm font-semibold">Top Referrers</h3>
                    </div>
                    {leaders.length === 0 ? (
                        <p className="text-sm text-muted-foreground px-4 py-8 text-center">No referrals yet.</p>
                    ) : (
                        <div className="overflow-x-auto">
                            <table className="w-full text-sm">
                                <thead>
                                    <tr className="text-left text-[11px] uppercase tracking-wide text-muted-foreground border-b border-border/50">
                                        <th className="px-4 py-2 font-medium">#</th>
                                        <th className="px-4 py-2 font-medium">Driver</th>
                                        <th className="px-4 py-2 font-medium">Code</th>
                                        <th className="px-4 py-2 font-medium text-right">Referrals</th>
                                        <th className="px-4 py-2 font-medium text-right">Qualified</th>
                                        <th className="px-4 py-2 font-medium text-right">Earned</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {leaders.map((l, i) => (
                                        <tr key={l.driver_id} className="border-b border-border/30 last:border-0">
                                            <td className="px-4 py-2.5 text-muted-foreground">{i + 1}</td>
                                            <td className="px-4 py-2.5 font-medium">{l.name}</td>
                                            <td className="px-4 py-2.5 font-mono text-xs text-muted-foreground">{l.driver_code}</td>
                                            <td className="px-4 py-2.5 text-right">{l.total_referrals.toLocaleString()}</td>
                                            <td className="px-4 py-2.5 text-right text-success font-medium">{l.qualified_referrals.toLocaleString()}</td>
                                            <td className="px-4 py-2.5 text-right font-semibold">{formatCurrency(l.referral_earnings)}</td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    )}
                </CardContent>
            </Card>
        </div>
    );
}

function SummaryCard({ icon: Icon, label, value, accent }: { icon: any; label: string; value: string; accent: string }) {
    return (
        <Card className="border-border/50">
            <CardContent className="p-4 flex items-center gap-3">
                <div className="w-10 h-10 rounded-xl bg-muted flex items-center justify-center shrink-0">
                    <Icon className={`h-5 w-5 ${accent}`} />
                </div>
                <div className="min-w-0">
                    <p className="text-[11px] uppercase tracking-wide text-muted-foreground font-medium">{label}</p>
                    <p className="text-lg font-bold truncate">{value}</p>
                </div>
            </CardContent>
        </Card>
    );
}
