"use client";

import { useEffect, useState, useCallback } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import {
  AreaChart, Area, BarChart, Bar,
  XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ReferenceLine,
} from "recharts";
import {
  TrendingUp, Clock, Zap, RefreshCw, Sun, Moon, Activity,
} from "lucide-react";
import {
  getDemandForecast, getDemandForecastSummary, getServiceAreas,
} from "@/lib/api";

const HOURS_OPTIONS = [
  { value: "12", label: "Next 12h" },
  { value: "24", label: "Next 24h" },
  { value: "48", label: "Next 48h" },
  { value: "72", label: "Next 72h" },
];

const CONFIDENCE_COLORS: Record<string, string> = {
  high: "bg-green-100 text-green-700",
  medium: "bg-amber-100 text-amber-700",
  low: "bg-gray-100 text-gray-500",
};

export default function ForecastPage() {
  const [hoursAhead, setHoursAhead] = useState("24");
  const [areaId, setAreaId] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const [summary, setSummary] = useState<any>(null);
  const [forecast, setForecast] = useState<any[]>([]);
  const [areas, setAreas] = useState<any[]>([]);

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const [fc, summ, areaList] = await Promise.all([
        getDemandForecast(Number(hoursAhead), areaId || undefined).catch(() => null),
        getDemandForecastSummary(areaId || undefined).catch(() => null),
        areas.length ? Promise.resolve(areas) : getServiceAreas().catch(() => []),
      ]);
      if (fc?.forecast) setForecast(fc.forecast);
      if (summ) setSummary(summ);
      if (Array.isArray(areaList) && !areas.length) setAreas(areaList);
    } finally {
      setLoading(false);
    }
  }, [hoursAhead, areaId]);

  useEffect(() => { fetchData(); }, [fetchData]);

  // Prepare chart data
  const chartData = forecast.map((f: any) => ({
    label: `${f.day_name} ${f.hour}:00`,
    rides: f.predicted_rides,
    isPeak: f.is_peak,
  }));

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <TrendingUp className="h-6 w-6 text-violet-500" />
            Demand Forecast
          </h1>
          <p className="text-muted-foreground mt-1">
            Predict ride demand by hour to optimise driver availability and surge pricing
          </p>
        </div>
        <div className="flex gap-2 items-center">
          <Select value={areaId} onValueChange={setAreaId}>
            <SelectTrigger className="w-44">
              <SelectValue placeholder="All Areas" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="">All Areas</SelectItem>
              {areas.filter((a: any) => a.is_active && !a.parent_service_area_id).map((a: any) => (
                <SelectItem key={a.id} value={a.id}>{a.name}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select value={hoursAhead} onValueChange={setHoursAhead}>
            <SelectTrigger className="w-32">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {HOURS_OPTIONS.map((o) => (
                <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button variant="outline" size="sm" onClick={fetchData} disabled={loading}>
            <RefreshCw className={`h-4 w-4 mr-1 ${loading ? "animate-spin" : ""}`} />
            Refresh
          </Button>
        </div>
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
              <Badge className={CONFIDENCE_COLORS[summary.confidence] || "bg-gray-100"}>
                {summary.confidence} confidence
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
              <div className="text-2xl font-bold mt-1 text-amber-600">{summary.peak_hours_count || 0}</div>
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
                    f.is_peak ? "bg-amber-50 border-amber-200" : "bg-gray-50 border-gray-200"
                  }`}
                >
                  <p className="text-[10px] text-muted-foreground font-medium">{f.day_name}</p>
                  <p className="text-xs font-bold">{f.hour}:00</p>
                  <p className={`text-sm font-bold mt-1 ${f.is_peak ? "text-amber-600" : "text-gray-700"}`}>
                    {f.predicted_rides}
                  </p>
                  {f.is_peak && (
                    <Zap className="h-3 w-3 text-amber-500 mx-auto mt-0.5" />
                  )}
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
