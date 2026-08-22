"use client";

import { useCallback, useEffect, useState } from "react";
import { getDriverDailyActivity } from "@/lib/api";

// All timestamps from the API are ISO UTC; render times in Regina (SK).
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

const PHASE_LABEL: Record<string, string> = {
    navigating_to_pickup: "To pickup",
    arrived_at_pickup: "Waiting",
    trip_in_progress: "Trip (passenger)",
};

interface Phase { phase: string; km: number; seconds: number | null; from: string | null; to: string | null }
interface Ride { ride_id: string; total_fare: number; empty_before_seconds: number | null; phases: Phase[] }
interface Activity {
    summary: {
        online_seconds: number; riding_seconds: number; empty_seconds: number; utilization: number;
        phase_km: Record<string, number>; total_km: number; ride_count: number; total_fare: number;
    };
    rides: Ride[];
}

export default function DriverActivity({ driverId }: { driverId: string }) {
    const [date, setDate] = useState<string>(todayRegina());
    const [data, setData] = useState<Activity | null>(null);
    const [loading, setLoading] = useState(false);
    const [openRide, setOpenRide] = useState<string | null>(null);

    const load = useCallback(async () => {
        setLoading(true);
        try {
            setData(await getDriverDailyActivity(driverId, date));
        } catch {
            setData(null);
        } finally {
            setLoading(false);
        }
    }, [driverId, date]);

    useEffect(() => {
        load();
    }, [load]);

    const s = data?.summary;
    const rides = data?.rides ?? [];
    const isToday = date >= todayRegina();

    return (
        <div className="space-y-4">
            {/* Date stepper (Regina day) */}
            <div className="flex items-center justify-between">
                <button onClick={() => setDate((d) => shiftDate(d, -1))} className="px-2 py-1 text-sm rounded hover:bg-muted" aria-label="Previous day">◄</button>
                <div className="text-sm font-medium">{date}</div>
                <button onClick={() => setDate((d) => shiftDate(d, 1))} disabled={isToday} className="px-2 py-1 text-sm rounded hover:bg-muted disabled:opacity-30" aria-label="Next day">►</button>
            </div>

            {loading && <p className="text-xs text-muted-foreground">Loading…</p>}

            {!loading && s && (
                <>
                    {/* Time split: empty (P1+P2) vs riding (P3) */}
                    <div className="grid grid-cols-3 gap-2 text-center">
                        <div className="rounded-lg border p-2"><div className="text-[11px] text-muted-foreground">Online</div><div className="text-sm font-semibold">{fmtDuration(s.online_seconds)}</div></div>
                        <div className="rounded-lg border p-2"><div className="text-[11px] text-muted-foreground">Riding (P3)</div><div className="text-sm font-semibold">{fmtDuration(s.riding_seconds)}</div></div>
                        <div className="rounded-lg border p-2"><div className="text-[11px] text-muted-foreground">Empty (P1+P2)</div><div className="text-sm font-semibold">{fmtDuration(s.empty_seconds)}</div></div>
                    </div>
                    <div className="text-xs text-muted-foreground">
                        Utilization <span className="font-semibold text-foreground">{Math.round((s.utilization || 0) * 100)}%</span>
                        {" · "}{s.ride_count} rides{" · "}{Number(s.total_km).toFixed(1)} km{" · "}${Number(s.total_fare).toFixed(2)}
                    </div>

                    {/* Per-phase km for the day */}
                    <div className="text-xs space-y-1">
                        <div className="flex justify-between"><span className="text-muted-foreground">Trip (P3, passenger)</span><span>{Number(s.phase_km?.trip_in_progress || 0).toFixed(1)} km</span></div>
                        <div className="flex justify-between"><span className="text-muted-foreground">To pickup (P2, en route)</span><span>{Number(s.phase_km?.navigating_to_pickup || 0).toFixed(1)} km</span></div>
                    </div>

                    {/* Rides dropdown */}
                    <div className="border-t pt-2">
                        <div className="text-xs font-medium mb-1">Rides ({rides.length})</div>
                        {rides.length === 0 && <p className="text-xs text-muted-foreground">No rides this day.</p>}
                        <div className="space-y-1">
                            {rides.map((r) => (
                                <div key={r.ride_id} className="rounded-lg border">
                                    <button
                                        onClick={() => setOpenRide((o) => (o === r.ride_id ? null : r.ride_id))}
                                        className="w-full flex items-center justify-between px-2 py-1.5 text-xs hover:bg-muted"
                                    >
                                        <span className="font-mono">{r.ride_id?.slice(0, 8)}</span>
                                        <span className="text-muted-foreground">${Number(r.total_fare).toFixed(2)}</span>
                                    </button>
                                    {openRide === r.ride_id && (
                                        <div className="px-2 pb-2 space-y-1">
                                            {r.empty_before_seconds != null && (
                                                <div className="text-[11px] text-warning">empty before: {fmtDuration(r.empty_before_seconds)}</div>
                                            )}
                                            {(r.phases || []).map((p) => (
                                                <div key={p.phase} className="flex items-center justify-between text-[11px]">
                                                    <span className="text-muted-foreground">{PHASE_LABEL[p.phase] || p.phase}</span>
                                                    <span>{Number(p.km).toFixed(1)} km · {fmtDuration(p.seconds)} · {fmtTime(p.from)}→{fmtTime(p.to)}</span>
                                                </div>
                                            ))}
                                        </div>
                                    )}
                                </div>
                            ))}
                        </div>
                    </div>
                </>
            )}

            {!loading && !s && <p className="text-xs text-muted-foreground">No activity data for this day.</p>}
        </div>
    );
}
