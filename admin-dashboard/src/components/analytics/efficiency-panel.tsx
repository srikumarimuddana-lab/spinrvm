"use client";

// Rider experience and driver economics: how long a rider waits, whether the
// promised ETA held, and how much unpaid distance drivers absorb.
//
// These lead the headline rates — time-to-match climbing is a supply signal
// that appears well before the cancellation rate moves.

import { useEffect, useState, useCallback } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Timer, MapPin, Route, Gauge } from "lucide-react";
import { getEfficiencyMetrics } from "@/lib/api";
import { StatTile, SampleNote, fmtSecs } from "./kpi-tile";

export interface EfficiencyPanelProps {
    dateRange: string;
    serviceAreaId?: string;
    refreshToken?: number;
}

/** P50/P95 pair with its sample size. Percentiles are a table, not a chart —
 *  four numbers plotted would be less legible than four numbers written. */
function PercentileRow({
    label, hint, p50, p95, sample, noun = "rides",
}: {
    label: string; hint?: string;
    p50: number | null; p95: number | null; sample: number; noun?: string;
}) {
    return (
        <div className="flex flex-wrap items-baseline justify-between gap-2 border-b border-border py-3 last:border-0">
            <div className="min-w-[180px]">
                <div className="text-sm font-medium">{label}</div>
                {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
                <SampleNote n={sample} noun={noun} />
            </div>
            <div className="flex gap-6">
                <div className="text-right">
                    <div className="text-xs text-muted-foreground">Median</div>
                    <div className="text-lg font-bold tabular-nums">{fmtSecs(p50)}</div>
                </div>
                <div className="text-right">
                    <div className="text-xs text-muted-foreground">P95</div>
                    <div className="text-lg font-bold tabular-nums">{fmtSecs(p95)}</div>
                </div>
            </div>
        </div>
    );
}

export function EfficiencyPanel({ dateRange, serviceAreaId, refreshToken = 0 }: EfficiencyPanelProps) {
    const [data, setData] = useState<any>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    const fetchData = useCallback(async () => {
        setLoading(true);
        setError(null);
        try {
            setData(await getEfficiencyMetrics(dateRange, serviceAreaId));
        } catch (e: any) {
            setData(null);
            setError(e?.message || "Could not load efficiency metrics.");
        } finally {
            setLoading(false);
        }
    }, [dateRange, serviceAreaId, refreshToken]);

    useEffect(() => { void fetchData(); }, [fetchData]);

    if (loading) return <div className="py-16 text-center text-sm text-muted-foreground">Loading efficiency metrics…</div>;
    if (error) {
        return (
            <div className="py-16 text-center space-y-3">
                <p className="text-sm text-red-600 dark:text-red-400">{error}</p>
                <button onClick={fetchData} className="text-xs font-semibold border rounded-lg px-3 py-1.5 hover:bg-muted transition-colors">Retry</button>
            </div>
        );
    }

    const ttm = data?.time_to_match || {};
    const ttp = data?.assignment_to_trip_start || {};
    const eta = data?.pickup_eta_error || {};
    const dh = data?.deadhead || {};

    return (
        <div className="space-y-6">
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
                <StatTile label="Median time to match" value={fmtSecs(ttm.p50_secs)} Icon={Timer}
                          hint="request → driver assigned" />
                <StatTile label="Pickups on time" value={`${eta.on_time_pct ?? 0}%`} Icon={MapPin}
                          hint="vs the ETA promised at offer"
                          tone={(eta.on_time_pct ?? 0) >= 70 ? "good" : (eta.on_time_pct ?? 0) >= 50 ? "warn" : "bad"} />
                <StatTile label="Deadhead ratio" value={`${dh.ratio_pct ?? 0}%`} Icon={Route}
                          hint="unpaid approach km per paid km"
                          tone={(dh.ratio_pct ?? 0) > 30 ? "warn" : "neutral"} />
                <StatTile label="Unpaid km" value={(dh.unpaid_km ?? 0).toLocaleString()} Icon={Gauge}
                          hint={`of ${(dh.paid_km ?? 0).toLocaleString()} paid km`} />
            </div>

            <Card>
                <CardHeader>
                    <CardTitle className="text-base">Wait times</CardTitle>
                    <p className="text-xs text-muted-foreground">
                        Rides that never matched are excluded from these percentiles — unmatched demand is
                        measured by match rate on the Overview tab, not hidden in a median here.
                    </p>
                </CardHeader>
                <CardContent className="pt-0">
                    <PercentileRow
                        label="Time to match"
                        hint="Ride requested → driver assigned"
                        p50={ttm.p50_secs} p95={ttm.p95_secs} sample={ttm.sample ?? 0}
                    />
                    <PercentileRow
                        label="Assignment → trip start"
                        hint="Includes driver acceptance and the drive to pickup — rides carry no separate arrival timestamp, so this is an upper bound on drive time"
                        p50={ttp.p50_secs} p95={ttp.p95_secs} sample={ttp.sample ?? 0}
                    />
                    <PercentileRow
                        label="Pickup ETA error"
                        hint="Actual minus promised. Negative means the driver beat the ETA."
                        p50={eta.p50_secs} p95={eta.p95_secs} sample={eta.sample ?? 0}
                    />
                </CardContent>
            </Card>

            <Card>
                <CardHeader>
                    <CardTitle className="text-base">Why deadhead matters here</CardTitle>
                </CardHeader>
                <CardContent>
                    <p className="text-sm text-muted-foreground">
                        Spinr takes no commission — drivers keep 100% of the fare — so unpaid approach
                        distance comes directly out of driver earnings rather than being absorbed by a
                        platform cut. A rising deadhead ratio is a driver-retention risk before it is
                        anything else. Current: <span className="font-semibold text-foreground tabular-nums">
                        {dh.unpaid_km ?? 0} km</span> unpaid against{" "}
                        <span className="font-semibold text-foreground tabular-nums">{dh.paid_km ?? 0} km</span> paid.
                    </p>
                </CardContent>
            </Card>
        </div>
    );
}

export default EfficiencyPanel;
