"use client";

import { useState } from "react";
import ReferralLeaderboard from "@/components/referral-leaderboard";
import ReferralAnalytics from "@/components/referral-analytics";
import ReferralSpendSummary from "@/components/referral-spend-summary";
import ReferralPairs from "@/components/referral-pairs";
import { useRequireModule } from "@/hooks/useRequireModule";

export default function ReferralsPage() {
    const { allowed } = useRequireModule("drivers");
    const [source, setSource] = useState<"driver" | "rider">("driver");
    if (!allowed) return null;

    return (
        <div className="space-y-6">
            <div>
                <h1 className="text-3xl font-bold tracking-tight">Referrals</h1>
                <p className="text-muted-foreground mt-1">Referral program — redemption funnel, payouts, trends, and top referrers</p>
            </div>

            {/* Combined rider+driver spend at a glance (independent of the source toggle). */}
            <ReferralSpendSummary />

            <div className="flex gap-1 bg-muted rounded-xl p-1 w-fit">
                <button onClick={() => setSource("driver")}
                    className={`px-5 py-2 rounded-lg text-sm font-semibold transition ${source === "driver" ? "bg-background text-foreground shadow-sm" : "text-muted-foreground"}`}>
                    Drivers
                </button>
                <button onClick={() => setSource("rider")}
                    className={`px-5 py-2 rounded-lg text-sm font-semibold transition ${source === "rider" ? "bg-background text-foreground shadow-sm" : "text-muted-foreground"}`}>
                    Riders
                </button>
            </div>

            <ReferralAnalytics source={source} />
            <ReferralLeaderboard limit={50} source={source} />
            <ReferralPairs source={source} />
        </div>
    );
}
