"use client";

import { useState } from "react";
import ReferralLeaderboard from "@/components/referral-leaderboard";
import ReferralAnalytics from "@/components/referral-analytics";
import ReferralSpendSummary from "@/components/referral-spend-summary";
import ReferralPairs from "@/components/referral-pairs";

/**
 * The full referral program view — spend summary, a driver/rider source
 * toggle, redemption funnel, leaderboard, and referrer→referee pairs.
 *
 * Extracted so the exact same panel renders in two places without diverging:
 *   • the standalone /dashboard/referrals page, and
 *   • the Referrals tab on the Earnings & Payouts page.
 * Access gating stays in each container (both require the `drivers` module).
 */
export default function ReferralsPanel() {
    const [source, setSource] = useState<"driver" | "rider">("driver");

    return (
        <div className="space-y-6">
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
