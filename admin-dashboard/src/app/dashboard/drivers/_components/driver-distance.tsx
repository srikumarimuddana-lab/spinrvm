"use client";

import { useCallback, useEffect, useState } from "react";
import {
    fetchDriverDistanceExport,
    getDriverDistanceLogs,
    getDriverDistanceTravelled,
} from "@/lib/api";

// Insurance/ops distances (GPS-derived, revision-current) — never billing
// figures. Days are America/Regina business days, matching the backend.
const TZ = "America/Regina";

function fmtDuration(secs?: number | null): string {
    if (!secs || secs <= 0) return "0m";
    const h = Math.floor(secs / 3600);
    const m = Math.round((secs % 3600) / 60);
    return h > 0 ? `${h}h ${m}m` : `${m}m`;
}

function fmtTime(iso?: string | null): string {
    if (!iso) return "—";
    try {
        return new Date(iso).toLocaleTimeString("en-CA", { hour: "2-digit", minute: "2-digit", timeZone: TZ });
    } catch {
        return "—";
    }
}

function todayRegina(): string {
    return new Date().toLocaleDateString("en-CA", { timeZone: TZ }); // YYYY-MM-DD
}

function shiftDate(d: string, days: number): string {
    const dt = new Date(`${d}T12:00:00Z`);
    dt.setUTCDate(dt.getUTCDate() + days);
    return dt.toISOString().slice(0, 10);
}

const RANGE_DAYS = 30;

interface DayRow {
    date: string;
    driving_around_km: number;
    driving_around_seconds: number;
    on_pickup_way_km: number;
    on_pickup_way_seconds: number;
    on_ride_km: number;
    on_ride_seconds: number;
    total_km: number;
    online_minutes: number;
    rides_completed: number | null;
    day_source: string;
}

interface LogRow {
    from: string;
    to: string | null;
    seconds: number;
    phase: string;
    period: number;
    ride_id: string | null;
    ride_code: string | null;
    distance_km: number | null;
    distance_source: string | null;
    open: boolean;
    is_reconstructed: boolean;
}

// Insurance periods 1/2/3 (available / en route / passenger aboard) — mirrors
// the P1/P2/P3 neutral/warn/good treatment used in analytics/supply-panel.tsx.
const PHASE_TINT: Record<number, string> = {
    1: "bg-muted text-muted-foreground",
    2: "bg-warning/15 text-warning",
    3: "bg-success/15 text-success",
};

function DayLogs({ driverId, date }: { driverId: string; date: string }) {
    const [logs, setLogs] = useState<LogRow[] | null>(null);
    const [error, setError] = useState(false);

    useEffect(() => {
        let alive = true;
        getDriverDistanceLogs(driverId, date)
            .then((d) => alive && setLogs(d?.logs ?? []))
            .catch(() => alive && setError(true));
        return () => {
            alive = false;
        };
    }, [driverId, date]);

    if (error) return <p className="px-3 py-2 text-xs text-destructive">Could not load distance logs.</p>;
    if (logs === null) return <p className="px-3 py-2 text-xs text-muted-foreground">Loading logs…</p>;
    if (logs.length === 0) return <p className="px-3 py-2 text-xs text-muted-foreground">No tracked activity this day.</p>;

    return (
        <div className="overflow-x-auto">
            <table className="w-full text-xs">
                <thead>
                    <tr className="text-muted-foreground border-b">
                        <th className="text-left font-medium px-3 py-1.5">From</th>
                        <th className="text-left font-medium px-3 py-1.5">To</th>
                        <th className="text-left font-medium px-3 py-1.5">Booking</th>
                        <th className="text-left font-medium px-3 py-1.5">Phase</th>
                        <th className="text-right font-medium px-3 py-1.5">Distance</th>
                        <th className="text-right font-medium px-3 py-1.5">Time</th>
                    </tr>
                </thead>
                <tbody>
                    {logs.map((l, i) => (
                        <tr key={i} className="border-b last:border-0">
                            <td className="px-3 py-1.5 tabular-nums">{fmtTime(l.from)}</td>
                            <td className="px-3 py-1.5 tabular-nums">{l.open ? "ongoing" : fmtTime(l.to)}</td>
                            <td className="px-3 py-1.5 font-mono">{l.ride_code || (l.ride_id ? l.ride_id.slice(0, 8) : "—")}</td>
                            <td className="px-3 py-1.5">
                                <span className={`inline-block rounded px-1.5 py-0.5 text-[10px] font-medium ${PHASE_TINT[l.period] || ""}`}>{l.phase}</span>
                                {l.is_reconstructed && (
                                    <span
                                        className="ml-1 inline-block rounded px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground bg-muted"
                                        title="Backfilled from timestamps during legacy migration — not logged live"
                                        aria-label="Reconstructed: backfilled from timestamps during legacy migration, not logged live"
                                    >
                                        Reconstructed
                                    </span>
                                )}
                            </td>
                            <td className="px-3 py-1.5 text-right tabular-nums" title={l.distance_source || undefined}>
                                {l.distance_km == null ? "—" : `${l.distance_km.toFixed(2)} km`}
                            </td>
                            <td className="px-3 py-1.5 text-right tabular-nums">{fmtDuration(l.seconds)}</td>
                        </tr>
                    ))}
                </tbody>
            </table>
        </div>
    );
}

export default function DriverDistance({ driverId }: { driverId: string }) {
    const [end, setEnd] = useState<string>(todayRegina());
    const [data, setData] = useState<any | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState(false);
    const [openDay, setOpenDay] = useState<string | null>(null);
    const [exporting, setExporting] = useState<string | null>(null);

    const start = shiftDate(end, -(RANGE_DAYS - 1));

    const load = useCallback(async () => {
        setLoading(true);
        setError(false);
        try {
            setData(await getDriverDistanceTravelled(driverId, start, end));
        } catch {
            setData(null);
            setError(true);
        } finally {
            setLoading(false);
        }
    }, [driverId, start, end]);

    useEffect(() => {
        load();
    }, [load]);

    const days: DayRow[] = data?.days ?? [];
    const totals = data?.totals;
    const atToday = end >= todayRegina();

    const doExport = async (format: "csv" | "pdf" | "xlsx") => {
        setExporting(format);
        try {
            const blob = await fetchDriverDistanceExport(driverId, start, end, format);
            const url = URL.createObjectURL(blob);
            const a = document.createElement("a");
            a.href = url;
            a.download = `distance-travelled_${start}_${end}.${format}`;
            a.click();
            URL.revokeObjectURL(url);
        } catch {
            // surfaced by the button returning to idle; a toast layer isn't
            // wired into this Sheet — consistent with the sibling tabs.
        } finally {
            setExporting(null);
        }
    };

    return (
        <div className="space-y-3">
            {/* Range pager + exports */}
            <div className="flex items-center justify-between gap-2 flex-wrap">
                <div className="flex items-center gap-2">
                    <button onClick={() => setEnd((d) => shiftDate(d, -RANGE_DAYS))} className="px-2 py-1 text-sm rounded hover:bg-muted" aria-label="Previous 30 days">◄</button>
                    <div className="text-sm font-medium tabular-nums">{start} → {end}</div>
                    <button onClick={() => setEnd((d) => shiftDate(d, RANGE_DAYS))} disabled={atToday} className="px-2 py-1 text-sm rounded hover:bg-muted disabled:opacity-30" aria-label="Next 30 days">►</button>
                    <span className="text-[11px] text-muted-foreground">Regina days</span>
                </div>
                <div className="flex items-center gap-1">
                    {(["csv", "xlsx", "pdf"] as const).map((f) => (
                        <button
                            key={f}
                            onClick={() => doExport(f)}
                            disabled={exporting !== null || days.length === 0}
                            className="px-2 py-1 text-[11px] uppercase rounded border hover:bg-muted disabled:opacity-40"
                        >
                            {exporting === f ? "…" : f}
                        </button>
                    ))}
                </div>
            </div>

            {loading && <p className="text-xs text-muted-foreground">Loading…</p>}
            {error && !loading && <p className="text-xs text-destructive">Could not load distance data.</p>}
            {!loading && !error && days.length === 0 && (
                <p className="text-xs text-muted-foreground">No tracked activity in this range.</p>
            )}

            {!loading && days.length > 0 && (
                <div className="rounded-lg border overflow-x-auto">
                    <table className="w-full text-xs">
                        <thead>
                            <tr className="text-muted-foreground border-b bg-muted/40">
                                <th className="text-left font-medium px-3 py-2">Date</th>
                                <th className="text-right font-medium px-3 py-2">Driving around</th>
                                <th className="text-right font-medium px-3 py-2">On pickup way</th>
                                <th className="text-right font-medium px-3 py-2">On ride</th>
                                <th className="text-right font-medium px-3 py-2">Total</th>
                            </tr>
                        </thead>
                        <tbody>
                            {days.map((d) => (
                                <>
                                    <tr
                                        key={d.date}
                                        onClick={() => setOpenDay((o) => (o === d.date ? null : d.date))}
                                        className="border-b cursor-pointer hover:bg-muted/50"
                                    >
                                        <td className="px-3 py-2 font-medium tabular-nums">
                                            {d.date}
                                            {d.day_source === "live" && (
                                                <span className="ml-1.5 text-[10px] rounded bg-primary/10 text-primary px-1 py-0.5">live</span>
                                            )}
                                        </td>
                                        <td className="px-3 py-2 text-right tabular-nums">
                                            {d.driving_around_km.toFixed(2)} km
                                            <span className="text-muted-foreground"> · {fmtDuration(d.driving_around_seconds)}</span>
                                        </td>
                                        <td className="px-3 py-2 text-right tabular-nums">
                                            {d.on_pickup_way_km.toFixed(2)} km
                                            <span className="text-muted-foreground"> · {fmtDuration(d.on_pickup_way_seconds)}</span>
                                        </td>
                                        <td className="px-3 py-2 text-right tabular-nums">
                                            {d.on_ride_km.toFixed(2)} km
                                            <span className="text-muted-foreground"> · {fmtDuration(d.on_ride_seconds)}</span>
                                        </td>
                                        <td className="px-3 py-2 text-right font-semibold tabular-nums">{d.total_km.toFixed(2)} km</td>
                                    </tr>
                                    {openDay === d.date && (
                                        <tr key={`${d.date}-logs`} className="border-b bg-muted/20">
                                            <td colSpan={5} className="p-0">
                                                <DayLogs driverId={driverId} date={d.date} />
                                            </td>
                                        </tr>
                                    )}
                                </>
                            ))}
                        </tbody>
                        {totals && (
                            <tfoot>
                                <tr className="font-semibold bg-muted/40">
                                    <td className="px-3 py-2">Total</td>
                                    <td className="px-3 py-2 text-right tabular-nums">
                                        {Number(totals.driving_around_km).toFixed(2)} km
                                        <span className="text-muted-foreground font-normal"> · {fmtDuration(totals.driving_around_seconds)}</span>
                                    </td>
                                    <td className="px-3 py-2 text-right tabular-nums">
                                        {Number(totals.on_pickup_way_km).toFixed(2)} km
                                        <span className="text-muted-foreground font-normal"> · {fmtDuration(totals.on_pickup_way_seconds)}</span>
                                    </td>
                                    <td className="px-3 py-2 text-right tabular-nums">
                                        {Number(totals.on_ride_km).toFixed(2)} km
                                        <span className="text-muted-foreground font-normal"> · {fmtDuration(totals.on_ride_seconds)}</span>
                                    </td>
                                    <td className="px-3 py-2 text-right tabular-nums">{Number(totals.total_km).toFixed(2)} km</td>
                                </tr>
                            </tfoot>
                        )}
                    </table>
                </div>
            )}

            <p className="text-[11px] text-muted-foreground">
                GPS-derived insurance/ops figures (anomaly-filtered, revision-current) — not billing. Click a day for its
                phase-by-phase log.
            </p>
        </div>
    );
}
