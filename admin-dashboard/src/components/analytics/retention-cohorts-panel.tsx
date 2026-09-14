"use client";

// Rider/driver retention by signup cohort week — W1/W4/W12. CLAUDE.md lists
// "weekly active driver retention >= 80%" as a KPI target, but nothing
// computed any cohort/retention concept anywhere before this panel (backend
// migration 421). "Retained" = completed >= 1 ride in that later week, same
// rule for riders and drivers — an explicit product decision, not the only
// possible definition (see the endpoint's own docstring).

import { useEffect, useState, useCallback } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Users, Car } from "lucide-react";
import { getRetentionCohorts } from "@/lib/api";
import { KpiCard, type KpiReading } from "./kpi-tile";

export interface RetentionCohortsPanelProps {
    dateRange: string;
    serviceAreaId?: string;
    refreshToken?: number;
}

interface CohortRow {
    cohort_week: string;
    horizon_weeks: number;
    cohort_size: number;
    retained: number;
    retained_pct: number;
}

const HORIZONS = [1, 4, 12];

/** Pivot the flat (cohort_week, horizon_weeks) rows the API returns into one
 *  row per cohort week with a column per horizon — a horizon with no entry
 *  means it hasn't elapsed yet for that cohort (the API omits it, it is
 *  never zero-filled), rendered as an em-dash, not 0%. */
function pivotByCohortWeek(rows: CohortRow[]): Map<string, Map<number, CohortRow>> {
    const byWeek = new Map<string, Map<number, CohortRow>>();
    for (const row of rows) {
        if (!byWeek.has(row.cohort_week)) byWeek.set(row.cohort_week, new Map());
        byWeek.get(row.cohort_week)!.set(row.horizon_weeks, row);
    }
    return byWeek;
}

function pctToneClass(pct: number): string {
    if (pct >= 60) return "bg-success/15 text-success";
    if (pct >= 35) return "bg-warning/15 text-warning";
    return "bg-destructive/10 text-destructive";
}

function CohortTable({ title, Icon, rows }: { title: string; Icon: typeof Users; rows: CohortRow[] }) {
    const byWeek = pivotByCohortWeek(rows);
    const weeks = Array.from(byWeek.keys()).sort().reverse(); // newest cohort first

    return (
        <Card>
            <CardHeader>
                <CardTitle className="text-base flex items-center gap-2">
                    <Icon className="h-4 w-4 text-muted-foreground" />
                    {title}
                </CardTitle>
            </CardHeader>
            <CardContent>
                {weeks.length === 0 ? (
                    <p className="py-8 text-center text-sm text-muted-foreground">
                        No cohort old enough to have a retention reading in this window yet.
                    </p>
                ) : (
                    <div className="overflow-x-auto">
                        <table className="w-full text-sm">
                            <thead>
                                <tr className="text-left text-xs text-muted-foreground">
                                    <th className="pb-2 pr-4 font-medium">Signup week</th>
                                    <th className="pb-2 pr-4 font-medium">Cohort size</th>
                                    {HORIZONS.map((h) => (
                                        <th key={h} className="pb-2 pr-4 font-medium">W{h}</th>
                                    ))}
                                </tr>
                            </thead>
                            <tbody>
                                {weeks.map((week) => {
                                    const cols = byWeek.get(week)!;
                                    // Cohort size is the same across every horizon row for a
                                    // given week — take it from whichever horizon exists.
                                    const size = Array.from(cols.values())[0]?.cohort_size ?? 0;
                                    return (
                                        <tr key={week} className="border-t border-border">
                                            <td className="py-2 pr-4 font-mono text-xs">{week}</td>
                                            <td className="py-2 pr-4 tabular-nums">{size}</td>
                                            {HORIZONS.map((h) => {
                                                const cell = cols.get(h);
                                                return (
                                                    <td key={h} className="py-2 pr-4">
                                                        {cell ? (
                                                            <span
                                                                className={`inline-block rounded px-2 py-0.5 font-mono font-medium tabular-nums ${pctToneClass(cell.retained_pct)}`}
                                                            >
                                                                {cell.retained_pct}%
                                                            </span>
                                                        ) : (
                                                            <span className="text-muted-foreground">—</span>
                                                        )}
                                                    </td>
                                                );
                                            })}
                                        </tr>
                                    );
                                })}
                            </tbody>
                        </table>
                    </div>
                )}
            </CardContent>
        </Card>
    );
}

export function RetentionCohortsPanel({ dateRange, serviceAreaId, refreshToken = 0 }: RetentionCohortsPanelProps) {
    const [data, setData] = useState<any>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    const fetchData = useCallback(async () => {
        setLoading(true);
        setError(null);
        try {
            setData(await getRetentionCohorts(dateRange, serviceAreaId));
        } catch (e: any) {
            setData(null);
            setError(e?.message || "Could not load retention metrics.");
        } finally {
            setLoading(false);
        }
    }, [dateRange, serviceAreaId, refreshToken]);

    useEffect(() => { void fetchData(); }, [fetchData]);

    if (loading) return <div className="py-16 text-center text-sm text-muted-foreground">Loading retention cohorts…</div>;
    if (error) {
        return (
            <div className="py-16 text-center space-y-3">
                <p className="text-sm text-destructive">{error}</p>
                <button onClick={fetchData} className="text-xs font-semibold border rounded-lg px-3 py-1.5 hover:bg-muted transition-colors">Retry</button>
            </div>
        );
    }

    const kpis: KpiReading[] = data?.kpis || [];
    const riders: CohortRow[] = data?.riders || [];
    const drivers: CohortRow[] = data?.drivers || [];

    return (
        <div className="space-y-6">
            <p className="text-xs text-muted-foreground">
                “Retained” = completed at least one ride in that later week. A cohort needs W1/W4/W12
                weeks to actually pass before that column has a reading — an em-dash means not enough
                time has elapsed yet, not 0% retention.
                {(dateRange === "today" || dateRange === "7d" || dateRange === "30d") && (
                    <> Pick 90 Days or 1 Year above for a fuller picture — cohorts from a {dateRange} window are too recent to have W4/W12 readings yet.</>
                )}
            </p>

            {kpis.length > 0 && (
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
                    {kpis.map((k) => <KpiCard key={k.key} kpi={k} />)}
                </div>
            )}

            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                <CohortTable title="Rider retention" Icon={Users} rows={riders} />
                <CohortTable title="Driver retention" Icon={Car} rows={drivers} />
            </div>
        </div>
    );
}

export default RetentionCohortsPanel;
