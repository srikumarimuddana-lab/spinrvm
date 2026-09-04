// Referrals tab of the drivers detail slideout. Pure code motion out of
// drivers/page.tsx (design-audit follow-up,
// docs/change-log/2026-09-04-breakup-drivers-page-god-component.md) — no
// logic changes.

import { useState } from "react";
import type { DriverReferralSummary } from "@/lib/api";
import { Pagination } from "@/components/ui/pagination";
import { formatCurrency } from "@/lib/utils";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

export function DriverReferralsTab({ data, loading, fmtDate }: {
    data: DriverReferralSummary | null;
    loading: boolean;
    fmtDate: (d: string) => string;
}) {
    const [refPage, setRefPage] = useState(0);
    const [refPageSize, setRefPageSize] = useState<number>(25);

    if (loading) {
        return <div className="text-sm text-muted-foreground py-10 text-center">Loading referrals…</div>;
    }
    if (!data) {
        return <div className="text-sm text-muted-foreground py-10 text-center">No referral data.</div>;
    }
    const referees = data.referees || [];
    const pagedReferees = referees.slice(refPage * refPageSize, (refPage + 1) * refPageSize);
    return (
        <div className="space-y-5">
            {/* Summary */}
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                <div className="rounded-xl border border-border/50 p-3">
                    <p className="text-[11px] uppercase tracking-wide text-muted-foreground font-medium">Referrals</p>
                    <p className="text-xl font-bold mt-0.5">{data.total_referrals}</p>
                </div>
                <div className="rounded-xl border border-border/50 p-3">
                    <p className="text-[11px] uppercase tracking-wide text-muted-foreground font-medium">Rewarded</p>
                    <p className="text-xl font-bold mt-0.5 text-success">{data.qualified_referrals}</p>
                </div>
                <div className="rounded-xl border border-border/50 p-3">
                    <p className="text-[11px] uppercase tracking-wide text-muted-foreground font-medium">Pending</p>
                    <p className="text-xl font-bold mt-0.5 text-warning">{data.pending_referrals}</p>
                </div>
                <div className="rounded-xl border border-border/50 p-3">
                    <p className="text-[11px] uppercase tracking-wide text-muted-foreground font-medium">Earned</p>
                    <p className="text-xl font-bold mt-0.5">{formatCurrency(data.referral_earnings)}</p>
                </div>
            </div>
            <p className="text-xs text-muted-foreground">
                Code <span className="font-mono font-semibold">{data.referral_code}</span> · reward {formatCurrency(data.reward_amount)} once a referee completes {data.rides_required} rides.
            </p>
            {data.referred_by ? (
                <p className="text-xs text-muted-foreground">
                    Referred by <span className="font-semibold text-foreground">{data.referred_by.name}</span> ({data.referred_by.code})
                </p>
            ) : null}

            {/* Referee list */}
            {referees.length === 0 ? (
                <p className="text-sm text-muted-foreground py-6 text-center">No one has signed up with this driver&apos;s code yet.</p>
            ) : (
                <>
                <div className="space-y-2">
                    {pagedReferees.map((r, i) => {
                        const pct = Math.min(100, Math.round(((r.completed_rides || 0) / (r.rides_required || 1)) * 100));
                        return (
                            <div key={i} className="rounded-xl border border-border/50 p-3 flex items-center gap-3">
                                <div className="flex-1 min-w-0">
                                    <div className="flex items-center gap-2">
                                        <p className="text-sm font-medium truncate">{r.name}</p>
                                        <span className="text-[11px] text-muted-foreground">{fmtDate(r.referred_at)}</span>
                                    </div>
                                    {r.qualified ? (
                                        <p className="text-xs text-success font-medium mt-0.5">Reward earned</p>
                                    ) : (
                                        <>
                                            <p className="text-xs text-muted-foreground mt-0.5">
                                                {r.completed_rides}/{r.rides_required} rides
                                                {r.rides_remaining > 0 ? ` · ${r.rides_remaining} to go` : ""}
                                                {!r.is_driver ? " · not a driver yet" : ""}
                                            </p>
                                            <div className="h-1.5 rounded-full bg-muted overflow-hidden mt-1.5 max-w-[200px]">
                                                <div className="h-full bg-primary rounded-full" style={{ width: `${pct}%` }} />
                                            </div>
                                        </>
                                    )}
                                </div>
                                <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-wide shrink-0 ${r.qualified ? "bg-success/15 text-success" : "bg-warning/15 text-warning"}`}>
                                    {r.qualified ? "Earned" : "In progress"}
                                </span>
                            </div>
                        );
                    })}
                </div>
                <div className="flex items-center justify-between gap-3 flex-wrap">
                    <div className="flex items-center gap-2">
                        <span className="text-xs text-muted-foreground">Show</span>
                        <Select value={String(refPageSize)} onValueChange={(v) => { setRefPageSize(Number(v)); setRefPage(0); }}>
                            <SelectTrigger className="h-8 text-xs w-[70px]">
                                <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                                {[25, 50, 100].map(n => (
                                    <SelectItem key={n} value={String(n)} className="text-xs">{n}</SelectItem>
                                ))}
                            </SelectContent>
                        </Select>
                        <span className="text-xs text-muted-foreground">per page</span>
                    </div>
                    {referees.length > refPageSize && (
                        <Pagination
                            page={refPage}
                            pageSize={refPageSize}
                            hasNextPage={referees.length > (refPage + 1) * refPageSize}
                            totalCount={referees.length}
                            onPageChange={setRefPage}
                        />
                    )}
                </div>
                </>
            )}
        </div>
    );
}
