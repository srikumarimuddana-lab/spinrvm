"use client";

import { useEffect, useState } from "react";
import { getReferralPairs, type ReferralPair } from "@/lib/api";
import { Card, CardContent } from "@/components/ui/card";
import { formatCurrency } from "@/lib/utils";
import { Users } from "lucide-react";

/**
 * Referrer → referee pairs (who referred whom) from the referral_payouts ledger,
 * with the reward split + claim status. Complements the aggregate leaderboard,
 * which only shows top-referrer counts. Respects the rider/driver toggle.
 */
const STATUS_COLOR: Record<string, string> = {
    paid: "text-emerald-600 dark:text-emerald-400",
    processing: "text-blue-600 dark:text-blue-400",
    failed: "text-red-600 dark:text-red-400",
    expired: "text-muted-foreground",
};

export default function ReferralPairs({ source }: { source: "driver" | "rider" }) {
    const [pairs, setPairs] = useState<ReferralPair[]>([]);
    useEffect(() => {
        let cancelled = false;
        getReferralPairs({ source, limit: 200 })
            .then((d) => {
                if (!cancelled) setPairs(d?.pairs ?? []);
            })
            .catch(() => {
                if (!cancelled) setPairs([]);
            });
        return () => {
            cancelled = true;
        };
    }, [source]);

    if (pairs.length === 0) return null;
    return (
        <Card className="border-border/50">
            <CardContent className="p-4">
                <div className="flex items-center gap-2 mb-3">
                    <Users className="h-4 w-4 text-violet-500" />
                    <h3 className="text-sm font-semibold">Referrer → referee ({pairs.length})</h3>
                </div>
                <div className="space-y-2 max-h-96 overflow-y-auto">
                    {pairs.map((p) => (
                        <div key={p.id} className="flex items-center justify-between gap-3 rounded-md border border-border/50 p-2 text-sm">
                            <div className="min-w-0">
                                <div className="truncate font-medium">
                                    {p.referrer_name} <span className="text-muted-foreground">→</span> {p.referee_name}
                                </div>
                                <div className="text-[11px] text-muted-foreground">
                                    referrer {formatCurrency(Number(p.referrer_reward ?? 0))} / referee {formatCurrency(Number(p.referee_reward ?? 0))} ·{" "}
                                    {String(p.created_at).slice(0, 10)}
                                </div>
                            </div>
                            <span className={`shrink-0 text-xs font-semibold capitalize ${STATUS_COLOR[p.status] ?? "text-muted-foreground"}`}>
                                {p.status}
                            </span>
                        </div>
                    ))}
                </div>
            </CardContent>
        </Card>
    );
}
