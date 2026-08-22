"use client";

// Demand forecast. Extracted from /dashboard/forecast so the standalone page
// and the Analytics "Demand Forecast" tab render one implementation.
//
// The service area is passed in by the parent (shared across Analytics tabs).
// "Hours ahead" stays local: the forecast is forward-looking, so the shared
// backward-looking date-range filter does not apply to it.

import { useEffect, useState, useCallback } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
} from "recharts";
import { TrendingUp, Clock, Zap, Sun, Activity } from "lucide-react";
import { getDemandForecast, getDemandForecastSummary } from "@/lib/api";

const HOURS_OPTIONS = [
  { value: "12", label: "Next 12h" },
  { value: "24", label: "Next 24h" },
  { value: "48", label: "Next 48h" },
  { value: "72", label: "Next 72h" },
];

// The forecast is a historical-average lookup (see backend/utils/
// demand_forecast.py's module docstring), not a trained model with a
// real uncertainty estimate — so the badge describes what the number is
// GROUNDED IN (data provenance), not a "confidence" level, which would
// overstate the rigor behind an average-of-past-Tuesdays calculation.
// Corporate + admin portal review, Admin #3.
const DATA_BASIS_LABELS: Record<string, string> = {
  historical_average: "Based on historical data",
  limited_history: "Based on limited history",
  default_pattern: "Estimated (no history yet)",
};
const DATA_BASIS_COLORS: Record<string, string> = {
  historical_average: "bg-success/15 text-success",
  limited_history: "bg-warning/15 text-warning",
  default_pattern: "bg-muted text-muted-foreground",
};

export interface DemandForecastPanelProps {
  /** Undefined means "all service areas". */
  serviceAreaId?: string;
  /** Bump to force a refetch from a parent Refresh button. */
  refreshToken?: number;
  onLoadingChange?: (loading: boolean) => void;
}

export function DemandForecastPanel({
  serviceAreaId,
  refreshToken = 0,
  onLoadingChange,
}: DemandForecastPanelProps) {
  const [hoursAhead, setHoursAhead] = useState("24");
  const [loading, setLoading] = useState(true);
  const [summary, setSummary] = useState<any>(null);
  const [forecast, setForecast] = useState<any[]>([]);

  const fetchData = useCallback(async () => {
    setLoading(true);
    onLoadingChange?.(true);
    try {
      const [fc, summ] = await Promise.all([
        getDemandForecast(Number(hoursAhead), serviceAreaId).catch(() => null),
        getDemandForecastSummary(serviceAreaId).catch(() => null),
      ]);
      // Replace rather than merge — a stale area's forecast must not survive
      // an area switch that returned nothing.
      setForecast(fc?.forecast ?? []);
      setSummary(summ ?? null);
    } finally {
      setLoading(false);
      onLoadingChange?.(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hoursAhead, serviceAreaId, refreshToken]);

  useEffect(() => { void fetchData(); }, [fetchData]);

  const chartData = forecast.map((f: any) => ({
    label: `${f.day_name} ${f.hour}:00`,
    rides: f.predicted_rides,
    isPeak: f.is_peak,
  }));

  return (
    <div className="space-y-6">
      <div className="flex justify-end">
        <Select value={hoursAhead} onValueChange={setHoursAhead}>
          <SelectTrigger className="w-32" aria-label="Hours ahead">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {HOURS_OPTIONS.map((o) => (
              <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {/* Summary KPIs */}
      {summary?.available && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <Card>
            <CardContent className="pt-4">
              <div className="flex items-center gap-2 text-sm text-muted-foreground">
                <Activity className="h-4 w-4" /> Current Hour
              </div>
              <div className="text-2xl font-bold mt-1">
                {summary.current_hour?.predicted_rides || 0} rides
              </div>
              <Badge className={DATA_BASIS_COLORS[summary.data_basis] || "bg-gray-100 dark:bg-gray-800"}>
                {DATA_BASIS_LABELS[summary.data_basis] || summary.data_basis}
              </Badge>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="pt-4">
              <div className="flex items-center gap-2 text-sm text-muted-foreground">
                <Zap className="h-4 w-4 text-amber-500" /> Next Peak
              </div>
              <div className="text-2xl font-bold mt-1">
                {summary.next_peak ? `${summary.next_peak.day_name} ${summary.next_peak.hour}:00` : "None"}
              </div>
              {summary.next_peak && (
                <p className="text-xs text-muted-foreground">{summary.next_peak.predicted_rides} predicted rides</p>
              )}
            </CardContent>
          </Card>
          <Card>
            <CardContent className="pt-4">
              <div className="flex items-center gap-2 text-sm text-muted-foreground">
                <Sun className="h-4 w-4 text-amber-500" /> 24h Total
              </div>
              <div className="text-2xl font-bold mt-1">{summary.total_predicted_24h || 0}</div>
              <p className="text-xs text-muted-foreground">predicted rides</p>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="pt-4">
              <div className="flex items-center gap-2 text-sm text-muted-foreground">
                <Clock className="h-4 w-4" /> Peak Hours
              </div>
              <div className="text-2xl font-bold mt-1 text-amber-600 dark:text-amber-400">{summary.peak_hours_count || 0}</div>
              <p className="text-xs text-muted-foreground">of next 24h</p>
            </CardContent>
          </Card>
        </div>
      )}

      {/* Forecast Chart */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <TrendingUp className="h-5 w-5" />
            Hourly Demand Prediction
          </CardTitle>
        </CardHeader>
        <CardContent>
          {loading ? (
            <div className="text-center py-12 text-muted-foreground">Loading forecast...</div>
          ) : chartData.length === 0 ? (
            <div className="text-center py-12 text-muted-foreground">No forecast data available</div>
          ) : (
            <ResponsiveContainer width="100%" height={350}>
              <AreaChart data={chartData}>
                <defs>
                  <linearGradient id="demandGradient" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#8B5CF6" stopOpacity={0.3} />
                    <stop offset="95%" stopColor="#8B5CF6" stopOpacity={0.02} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis
                  dataKey="label"
                  fontSize={11}
                  interval={Math.max(0, Math.floor(chartData.length / 12) - 1)}
                  angle={-30}
                  textAnchor="end"
                  height={60}
                />
                <YAxis fontSize={11} />
                <Tooltip />
                <Area
                  type="monotone"
                  dataKey="rides"
                  stroke="#8B5CF6"
                  strokeWidth={2}
                  fill="url(#demandGradient)"
                  name="Predicted Rides"
                />
              </AreaChart>
            </ResponsiveContainer>
          )}
        </CardContent>
      </Card>

      {/* Peak / Off-Peak Breakdown */}
      {forecast.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Hour-by-Hour Breakdown</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-6 md:grid-cols-8 lg:grid-cols-12 gap-2">
              {forecast.slice(0, 24).map((f: any, i: number) => (
                <div
                  key={i}
                  className={`text-center p-2 rounded-lg border ${
                    f.is_peak
                      ? "bg-amber-50 border-amber-200 dark:bg-amber-950 dark:border-amber-800"
                      : "bg-muted border-border"
                  }`}
                >
                  <p className="text-[10px] text-muted-foreground font-medium">{f.day_name}</p>
                  <p className="text-xs font-bold">{f.hour}:00</p>
                  <p className={`text-sm font-bold mt-1 ${f.is_peak ? "text-amber-600 dark:text-amber-400" : "text-foreground"}`}>
                    {f.predicted_rides}
                  </p>
                  {f.is_peak && <Zap className="h-3 w-3 text-amber-500 dark:text-amber-400 mx-auto mt-0.5" />}
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

export default DemandForecastPanel;
