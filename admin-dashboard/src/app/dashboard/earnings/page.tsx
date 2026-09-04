"use client";

import { useState } from "react";
import { getEarnings } from "@/lib/api";
import { statusColor } from "@/lib/utils";
import ReferralsPanel from "@/components/referrals-panel";
import { PageHeader } from "@/components/page-header";
import AutoPayoutsPanel from "@/components/auto-payouts-panel";
import { Car, CreditCard, Wallet, Gift, CalendarClock } from "lucide-react";
import { useRequireModule } from "@/hooks/useRequireModule";
import { useAuthStore } from "@/store/authStore";
import { RideEarningsTab } from "./_components/ride-earnings-tab";
import { SpinrPassRevenueTab } from "./_components/spinr-pass-revenue-tab";
import { PayoutsTab } from "./_components/payouts-tab";

export default function EarningsPage() {
    const { allowed } = useRequireModule("earnings");
    const [tab, setTab] = useState<"rides" | "spinr-pass" | "payouts" | "auto-payouts" | "referrals">("rides");

    // The Referrals tab calls /api/admin/referrals/leaderboard, which is gated by
    // the `drivers` module on the backend. Only show it to admins who actually
    // have drivers access — otherwise they'd see the tab and hit a 403.
    // Corporate + admin portal review, Admin #4: role === "admin" alone
    // does NOT grant drivers access on the backend (only super_admin
    // bypasses require_module), so it must not grant it here either —
    // the previous check let exactly the 403 this comment warns about
    // happen for any admin-role user without the drivers module.
    const user = useAuthStore((s) => s.user);
    const canSeeReferrals =
        user?.role === "super_admin" || (user?.modules ?? []).includes("drivers");

    if (!allowed) return null;
    return (
        <div className="space-y-6">
            <PageHeader
                title="Earnings & Payouts"
                description="Platform revenue, subscriptions, and driver payouts"
            />

            {/* Tabs */}
            <div className="flex gap-1 bg-muted rounded-xl p-1 w-fit">
                <button onClick={() => setTab("rides")}
                    className={`flex items-center gap-1.5 px-5 py-2 rounded-lg text-sm font-semibold transition ${tab === "rides" ? "bg-background text-foreground shadow-sm" : "text-muted-foreground"}`}>
                    <Car className="h-4 w-4" /> Ride Earnings
                </button>
                <button onClick={() => setTab("spinr-pass")}
                    className={`flex items-center gap-1.5 px-5 py-2 rounded-lg text-sm font-semibold transition ${tab === "spinr-pass" ? "bg-background text-foreground shadow-sm" : "text-muted-foreground"}`}>
                    <CreditCard className="h-4 w-4" /> Spinr Pass
                </button>
                <button onClick={() => setTab("payouts")}
                    className={`flex items-center gap-1.5 px-5 py-2 rounded-lg text-sm font-semibold transition ${tab === "payouts" ? "bg-background text-foreground shadow-sm" : "text-muted-foreground"}`}>
                    <Wallet className="h-4 w-4" /> Payouts
                </button>
                <button onClick={() => setTab("auto-payouts")}
                    className={`flex items-center gap-1.5 px-5 py-2 rounded-lg text-sm font-semibold transition ${tab === "auto-payouts" ? "bg-background text-foreground shadow-sm" : "text-muted-foreground"}`}>
                    <CalendarClock className="h-4 w-4" /> Weekly Payouts
                </button>
                {canSeeReferrals && (
                    <button onClick={() => setTab("referrals")}
                        className={`flex items-center gap-1.5 px-5 py-2 rounded-lg text-sm font-semibold transition ${tab === "referrals" ? "bg-background text-foreground shadow-sm" : "text-muted-foreground"}`}>
                        <Gift className="h-4 w-4" /> Referrals
                    </button>
                )}
            </div>

            {tab === "rides" && <RideEarningsTab />}
            {tab === "spinr-pass" && <SpinrPassRevenueTab />}
            {tab === "payouts" && <PayoutsTab />}
            {/* Spinr-controlled Sunday batch: run history + who is blocked
                right now. Same `earnings` module gate as this page. */}
            {tab === "auto-payouts" && <div className="pt-1"><AutoPayoutsPanel /></div>}
            {tab === "referrals" && canSeeReferrals && (
                <div className="pt-1">
                    {/* Full referral program, inlined here (no link-out) so the
                        redemption funnel, payouts, trends, and top referrers live
                        right in Earnings. Same ReferralsPanel the standalone page
                        renders — one source, no divergence. */}
                    <ReferralsPanel />
                </div>
            )}
        </div>
    );
}
