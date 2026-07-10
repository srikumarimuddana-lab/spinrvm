"use client";

import ReferralsPanel from "@/components/referrals-panel";
import { useRequireModule } from "@/hooks/useRequireModule";

export default function ReferralsPage() {
    const { allowed } = useRequireModule("drivers");
    if (!allowed) return null;

    return (
        <div className="space-y-6">
            <div>
                <h1 className="text-3xl font-bold tracking-tight">Referrals</h1>
                <p className="text-muted-foreground mt-1">Referral program — redemption funnel, payouts, trends, and top referrers</p>
            </div>

            <ReferralsPanel />
        </div>
    );
}
