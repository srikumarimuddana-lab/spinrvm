"use client";

import { useState } from "react";
import ReferralLeaderboard from "@/components/referral-leaderboard";
import { useRequireModule } from "@/hooks/useRequireModule";

export default function ReferralsPage() {
    const { allowed } = useRequireModule("drivers");
    const [source, setSource] = useState<"driver" | "rider">("driver");
    if (!allowed) return null;

    return (
        <div className="space-y-6">
            <div>
                <h1 className="text-3xl font-bold tracking-tight">Referrals</h1>
                <p className="text-muted-foreground mt-1">Referral program — top referrers and fleet totals</p>
            </div>

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

            <ReferralLeaderboard limit={50} source={source} />
        </div>
    );
}
