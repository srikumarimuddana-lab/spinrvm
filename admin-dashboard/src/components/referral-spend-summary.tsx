"use client";

import { useEffect, useState } from "react";
import { getReferralAnalytics } from "@/lib/api";
import { Card, CardContent } from "@/components/ui/card";
import { formatCurrency } from "@/lib/utils";
import { DollarSign, UserPlus, Gift } from "lucide-react";

/**
 * Referral spend at a glance — how much the platform has actually paid out in
 * referral rewards, COMBINED across rider + driver, split into what referrers
 * earned vs what referees earned, with the per-source breakdown beneath. Reads
 * the same PAID referral_payouts ledger as the funnel (referrer_paid /
 * referee_paid), summed over both sources.
 */
export default function ReferralSpendSummary() {
    const [data, setData] = useState<{ rider: Funnel; driver: Funnel } | null>(null);
    useEffect(() => {
        let cancelled = false;
        Promise.all([getReferralAnalytics({ source: "rider" }), getReferralAnalytics({ source: "driver" })])
            .then(([rider, driver]) => {
                if (!cancelled) setData({ rider: rider.funnel, driver: driver.funnel });
            })
            .catch(() => {
                if (!cancelled) setData(null);
            });
        return () => {
            cancelled = true;
        };
    }, []);

    const n = (v: unknown) => Number(v ?? 0);
    const r = data?.rider;
    const d = data?.driver;
    const referrer = n(r?.referrer_paid) + n(d?.referrer_paid);
    const referee = n(r?.referee_paid) + n(d?.referee_paid);
    const total = referrer + referee;

    return (
        <Card className="border-border/50">
            <CardContent className="p-4">
                <div className="flex items-center gap-2 mb-3">
                    <DollarSign className="h-4 w-4 text-emerald-500" />
                    <h3 className="text-sm font-semibold">Referral spend — rider + driver combined</h3>
                </div>
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                    <Big icon={DollarSign} label="Total paid out" value={formatCurrency(total)} accent="text-emerald-600 dark:text-emerald-400" />
                    <Big icon={UserPlus} label="Referrers earned" value={formatCurrency(referrer)} accent="text-sky-600 dark:text-sky-400" />
                    <Big icon={Gift} label="Referees earned" value={formatCurrency(referee)} accent="text-violet-600 dark:text-violet-400" />
                </div>
                <div className="mt-3 grid grid-cols-1 sm:grid-cols-2 gap-2 text-xs text-muted-foreground">
                    <div>
                        Riders: <span className="font-medium text-foreground">{formatCurrency(n(r?.total_paid))}</span>{" "}
                        <span className="opacity-70">(referrer {formatCurrency(n(r?.referrer_paid))} / referee {formatCurrency(n(r?.referee_paid))})</span>
                    </div>
                    <div>
                        Drivers: <span className="font-medium text-foreground">{formatCurrency(n(d?.total_paid))}</span>{" "}
                        <span className="opacity-70">(referrer {formatCurrency(n(d?.referrer_paid))} / referee {formatCurrency(n(d?.referee_paid))})</span>
                    </div>
                </div>
            </CardContent>
        </Card>
    );
}

type Funnel = { total_paid: string; referrer_paid: string; referee_paid: string };

function Big({
    icon: Icon,
    label,
    value,
    accent,
}: {
    icon: React.ComponentType<{ className?: string }>;
    label: string;
    value: string;
    accent: string;
}) {
    return (
        <div className="rounded-lg border border-border/50 p-3">
            <div className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
                <Icon className="h-3.5 w-3.5" />
                {label}
            </div>
            <div className={`text-xl font-bold ${accent}`}>{value}</div>
        </div>
    );
}
