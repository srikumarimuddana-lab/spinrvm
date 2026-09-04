"use client";

import { useEffect, useState } from "react";
import { AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ReferenceLine } from "recharts";
import { useTheme } from "next-themes";
import { chartColors } from "@/components/analytics/chart-palette";
import { getSurgeHistory } from "@/lib/api/analytics-payouts";

// ─── Surge History Chart (AD-02) ───
// Extracted verbatim from service-areas/page.tsx.

export default function SurgeHistoryChart({ areaId, areaName }: { areaId: string; areaName: string }) {
  const [data, setData] = useState<any[]>([]);
  const [hours, setHours] = useState(24);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [truncated, setTruncated] = useState(false);

  useEffect(() => {
    // `cancelled` guards two real races: expanding area B while A's request is
    // in flight (B's chart would render A's data under B's name), and switching
    // 48h -> 6h where the slower 48h response lands last and overwrites the
    // newer view while the selector still reads 6h.
    let cancelled = false;
    setLoading(true);
    setError(null);
    getSurgeHistory(areaId, hours).then((res: any) => {
      if (cancelled) return;
      const rows = (res.history || []).map((r: any) => ({
        time: new Date(r.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
        fullTime: new Date(r.created_at).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }),
        multiplier: r.multiplier,
        demand: r.demand_count,
        supply: r.supply_count,
        source: r.source,
      }));
      setData(rows);
      // The backend caps the response at 500 rows and the engine appends one
      // row per area every 2 minutes, so a 24h/48h/7d selection silently shows
      // only the newest ~16.7 hours. Say so rather than mislabelling the axis.
      setTruncated(rows.length >= 500);
      setLoading(false);
    }).catch((e: any) => {
      if (cancelled) return;
      // Distinct from "no surge recorded": an operator investigating a rider
      // complaint must be able to tell "surge never fired" from "the chart is
      // broken right now".
      console.error("surge history fetch failed", e);
      setData([]);
      setError(
        e?.status === 403
          ? "No Analytics module access — surge history unavailable."
          : "Couldn't load surge history."
      );
      setLoading(false);
    });
    return () => { cancelled = true; };
  }, [areaId, hours]);

  const { resolvedTheme } = useTheme();
  const c = chartColors(resolvedTheme === "dark");

  return (
    <div className="mt-6 pt-6 border-t">
      <div className="flex items-center justify-between mb-3">
        <div>
          <h4 className="font-bold text-foreground">Surge History</h4>
          <p className="text-sm text-muted-foreground">Multiplier over time for {areaName}. Auto-captured every 2 minutes by the surge engine.</p>
        </div>
        <select
          aria-label={`Surge history time range for ${areaName}`}
          className="border rounded-lg px-3 py-1.5 text-sm"
          value={hours}
          onChange={e => setHours(Number(e.target.value))}
        >
          <option value={6}>Last 6 hours</option>
          <option value={12}>Last 12 hours</option>
          <option value={24}>Last 24 hours</option>
          <option value={48}>Last 48 hours</option>
          <option value={168}>Last 7 days</option>
        </select>
      </div>

      {truncated && (
        <p className="mb-2 text-xs text-warning">
          Showing the most recent 500 readings — the selected range is longer than
          the server returns in one response, so earlier readings are not charted.
        </p>
      )}

      {loading ? (
        <div className="h-64 flex items-center justify-center text-muted-foreground">Loading chart…</div>
      ) : error ? (
        <div role="alert" className="h-40 flex flex-col items-center justify-center gap-2 rounded-xl border border-destructive/30 bg-destructive/5">
          <p className="text-sm text-destructive">{error}</p>
          <button
            onClick={() => setHours(h => h)}
            className="text-xs underline text-muted-foreground hover:text-foreground"
          >
            Retry
          </button>
        </div>
      ) : data.length === 0 ? (
        <div className="h-40 flex items-center justify-center text-muted-foreground bg-muted rounded-xl">
          <p>No surge data recorded for this period.</p>
        </div>
      ) : (
        // Recharts renders to SVG with no text alternative; without a label the
        // whole trend is invisible to assistive tech. The summary below carries
        // the same numbers, so describe the shape here.
        <div
          className="h-72"
          role="img"
          aria-label={
            `Surge multiplier for ${areaName} over the last ${hours} hours: ` +
            `peak ${Math.max(...data.map(d => d.multiplier)).toFixed(1)} times, ` +
            `average ${(data.reduce((s, d) => s + d.multiplier, 0) / data.length).toFixed(2)} times, ` +
            `${data.length} readings.`
          }
        >
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={data}>
              <defs>
                <linearGradient id={`surgeGrad-${areaId}`} x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#F97316" stopOpacity={0.3} />
                  <stop offset="95%" stopColor="#F97316" stopOpacity={0.02} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis
                // < 24, not <= 24: a full 24-hour window spans two dates, so a
                // bare clock label repeats ("14:30" appears for both yesterday
                // and today) and the chart reads as if it doubled back on
                // itself. Anything at or above a day carries the date.
                dataKey={hours < 24 ? "time" : "fullTime"}
                fontSize={11}
                interval="preserveStartEnd"
                angle={hours > 24 ? -30 : 0}
                textAnchor={hours > 24 ? "end" : "middle"}
                height={hours > 24 ? 60 : 30}
              />
              <YAxis fontSize={11} domain={[0.8, 'auto']} tickFormatter={v => `${v}×`} />
              <Tooltip
                contentStyle={c.tooltip}
                formatter={(value, name) => {
                  if (name === 'Multiplier') return [`${value}×`, name];
                  return [String(value), name];
                }}
                labelFormatter={(label) => label}
              />
              {/* eslint-disable-next-line no-restricted-syntax -- neutral baseline marker, not part of the categorical/semantic palette (#2816) */}
              <ReferenceLine y={1.0} stroke="#94a3b8" strokeDasharray="4 4" label={{ value: '1.0× (normal)', position: 'insideTopLeft', fontSize: 10, fill: '#94a3b8' }} />
              <ReferenceLine y={2.5} stroke={c.bad} strokeDasharray="4 4" label={{ value: '2.5× (cap)', position: 'insideTopLeft', fontSize: 10, fill: c.bad }} />
              {/* eslint-disable-next-line no-restricted-syntax -- surge line keeps its own distinct orange, deliberately outside the categorical palette (#2816) */}
              <Area type="monotone" dataKey="multiplier" stroke="#F97316" strokeWidth={2}
                fill={`url(#surgeGrad-${areaId})`} name="Multiplier" dot={false} />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      )}

      {data.length > 0 && (
        <div className="flex gap-4 mt-2 text-xs text-muted-foreground">
          <span>Peak: <span className="font-semibold text-foreground">{Math.max(...data.map(d => d.multiplier)).toFixed(1)}×</span></span>
          <span>Avg: <span className="font-semibold text-foreground">{(data.reduce((s, d) => s + d.multiplier, 0) / data.length).toFixed(2)}×</span></span>
          <span>Points: {data.length}</span>
          {data.some(d => d.source === 'manual') && (
            <span className="text-warning font-semibold">Contains manual overrides</span>
          )}
        </div>
      )}
    </div>
  );
}
