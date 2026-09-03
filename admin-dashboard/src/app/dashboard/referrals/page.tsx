"use client";

import ReferralsPanel from "@/components/referrals-panel";
import { PageHeader } from "@/components/page-header";
import { useRequireModule } from "@/hooks/useRequireModule";

export default function ReferralsPage() {
    const { allowed } = useRequireModule("drivers");
    if (!allowed) return null;

    return (
        <div className="space-y-6">
            <PageHeader
                title="Referrals"
                description="Referral program — redemption funnel, payouts, trends, and top referrers"
            />

            <ReferralsPanel />
        </div>
    );
}
