"use client";

import ReferralLeaderboard from "@/components/referral-leaderboard";
import { useRequireModule } from "@/hooks/useRequireModule";

export default function ReferralsPage() {
    const { allowed } = useRequireModule("drivers");
    if (!allowed) return null;

    return (
        <div className="space-y-6">
            <div>
                <h1 className="text-3xl font-bold tracking-tight">Referrals</h1>
                <p className="text-muted-foreground mt-1">Driver referral program — top referrers and fleet totals</p>
            </div>
            <ReferralLeaderboard limit={50} />
        </div>
    );
}
