"use client";

import { useEffect, useState, useCallback } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import {
  BarChart3, TrendingUp, TrendingDown, XCircle, CheckCircle, Users,
  Clock, RefreshCw, Activity, Car, DollarSign, Target,
} from "lucide-react";
import {
  BarChart, Bar, PieChart, Pie, Cell, LineChart, Line,
  XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend,
} from "recharts";
import {
  getAnalyticsOverview, getCancellationBreakdown, getDriverAcceptanceRates,
} from "@/lib/api";

const DATE_RANGES = [
  { value: "today", label: "Today" },
  { value: "7d", label: "7 Days" },
  { value: "30d", label: "30 Days" },
  { value: "90d", label: "90 Days" },
  { value: "1y", label: "1 Year" },
];

const REASON_COLORS: Record<string, string> = {
  rider_cancelled: "#3B82F6",
  no_drivers_available: "#EF4444",
  driver_cancelled: "#F59E0B",
  search_timeout: "#8B5CF6",
  scheduled_cancelled: "#6B7280",
  unspecified: "#D1D5DB",
  other: "#9CA3AF",
};

const REASON_LABELS: Record<string, string> = {
  rider_cancelled: "Rider Cancelled",
  no_drivers_available: "No Drivers",
  driver_cancelled: "Driver Cancelled",
  search_timeout: "Search Timeout",
  scheduled_cancelled: "Scheduled Cancelled",
  unspecified: "Unspecified",
  other: "Other",
};

export default function AnalyticsPage() {
  const [dateRange, setDateRange] = useState("30d");
  const [loading, setLoading] = useState(true);
  const [overview, setOverview] = useState<any>(null);
  const [cancellations, setCancellations] = useState<any>(null);
  const [driverRates, setDriverRates] = useState<any>(null);

  const fetchAll = useCallback(async () => {
    setLoading(true);
    try {
      const [ov, cancel, drivers] = await Promise.all([
        getAnalyticsOverview(dateRange).catch(() => null),
        getCancellationBreakdown(dateRange).catch(() => null),
        getDriverAcceptanceRates(dateRange).catch(() => null),
      ]);
      setOverview(ov);
      setCancellations(cancel);
      setDriverRates(drivers);
    } finally {
      setLoading(false);
    }
  }, [dateRange]);

  useEffect(() => { fetchAll(); }, [fetchAll]);

  const pieData = (cancellations?.reasons || []).map((r: any) => ({
    name: REASON_LABELS[r.reason] || r.reason,
    value: r.count,
    color: REASON_COLORS[r.reason] || "#9CA3AF",
  }));

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <BarChart3 className="h-6 w-6 text-blue-500" />
            Operational Analytics
          </h1>
          <p className="text-muted-foreground mt-1">
            Acceptance rates, cancellation breakdown, and operational insights
          </p>
        </div>
        <div className="flex gap-2 items-center">
          <Select value={dateRange} onValueChange={setDateRange}>
            <SelectTrigger className="w-32">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {DATE_RANGES.map((r) => (
                <SelectItem key={r.value} value={r.value}>{r.label}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button variant="outline" size="sm" onClick={fetchAll} disabled={loading}>
            <RefreshCw className={`h-4 w-4 mr-1 ${loading ? "animate-spin" : ""}`} />
            Refresh
          </Button>
        </div>
      </div>

      {/* KPI Cards */}
      {overview && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <Card>
            <CardContent className="pt-4">
              <div className="flex items-center gap-2 text-sm text-muted-foreground">
                <Car className="h-4 w-4" /> Total Rides
              </div>
              <div className="text-2xl font-bold mt-1">{overview.total_rides}</div>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="pt-4">
              <div className="flex items-center gap-2 text-sm text-muted-foreground">
                <CheckCircle className="h-4 w-4 text-green-500" /> Completion Rate
              </div>
              <div className="text-2xl font-bold mt-1 text-green-600">{overview.completion_rate}%</div>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="pt-4">
              <div className="flex items-center gap-2 text-sm text-muted-foreground">
                <XCircle className="h-4 w-4 text-red-500" /> Cancellation Rate
              </div>
              <div className="text-2xl font-bold mt-1 text-red-600">{overview.cancellation_rate}%</div>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="pt-4">
              <div className="flex items-center gap-2 text-sm text-muted-foreground">
                <DollarSign className="h-4 w-4 text-amber-500" /> Revenue
              </div>
              <div className="text-2xl font-bold mt-1">${overview.total_revenue?.toLocaleString()}</div>
            </CardContent>
          </Card>
        </div>
      )}

      {/* Daily Trend Chart */}
      {overview?.daily_chart?.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Activity className="h-5 w-5" /> Daily Ride Trend
            </CardTitle>
          </CardHeader>
          <CardContent>
            <ResponsiveContainer width="100%" height={300}>
              <BarChart data={overview.daily_chart}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="date" fontSize={12} />
                <YAxis fontSize={12} />
                <Tooltip />
                <Legend />
                <Bar dataKey="completed" fill="#10B981" name="Completed" radius={[4, 4, 0, 0]} />
                <Bar dataKey="cancelled" fill="#EF4444" name="Cancelled" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>
      )}

      <Tabs defaultValue="cancellations">
        <TabsList>
          <TabsTrigger value="cancellations">Cancellation Breakdown</TabsTrigger>
          <TabsTrigger value="acceptance">Driver Acceptance Rates</TabsTrigger>
        </TabsList>

        {/* Cancellation Breakdown Tab */}
        <TabsContent value="cancellations" className="space-y-4">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {/* Pie Chart */}
            <Card>
              <CardHeader>
                <CardTitle>By Reason</CardTitle>
              </CardHeader>
              <CardContent>
                {pieData.length === 0 ? (
                  <div className="text-center py-8 text-muted-foreground">No cancellation data</div>
                ) : (
                  <ResponsiveContainer width="100%" height={300}>
                    <PieChart>
                      <Pie
                        data={pieData}
                        dataKey="value"
                        nameKey="name"
                        cx="50%" cy="50%"
                        outerRadius={100}
                        label={(props: any) => `${props.name || ''} (${((props.percent ?? 0) * 100).toFixed(0)}%)`}
                      >
                        {pieData.map((entry: any, i: number) => (
                          <Cell key={i} fill={entry.color} />
                        ))}
                      </Pie>
                      <Tooltip />
                    </PieChart>
                  </ResponsiveContainer>
                )}
              </CardContent>
            </Card>

            {/* Hourly Distribution */}
            <Card>
              <CardHeader>
                <CardTitle>Cancellations by Hour</CardTitle>
              </CardHeader>
              <CardContent>
                <ResponsiveContainer width="100%" height={300}>
                  <BarChart data={cancellations?.hourly_distribution || []}>
                    <CartesianGrid strokeDasharray="3 3" />
                    <XAxis dataKey="hour" fontSize={11} tickFormatter={(h) => `${h}:00`} />
                    <YAxis fontSize={11} />
                    <Tooltip labelFormatter={(h) => `${h}:00`} />
                    <Bar dataKey="count" fill="#EF4444" radius={[3, 3, 0, 0]} name="Cancellations" />
                  </BarChart>
                </ResponsiveContainer>
              </CardContent>
            </Card>
          </div>

          {/* Reason Table */}
          <Card>
            <CardHeader>
              <CardTitle>
                Cancellation Reasons ({cancellations?.total_cancellations || 0} total)
              </CardTitle>
            </CardHeader>
            <CardContent>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Reason</TableHead>
                    <TableHead>Count</TableHead>
                    <TableHead>Percentage</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {(cancellations?.reasons || []).map((r: any) => (
                    <TableRow key={r.reason}>
                      <TableCell className="flex items-center gap-2">
                        <div
                          className="w-3 h-3 rounded-full"
                          style={{ backgroundColor: REASON_COLORS[r.reason] || "#9CA3AF" }}
                        />
                        {REASON_LABELS[r.reason] || r.reason}
                      </TableCell>
                      <TableCell className="font-mono">{r.count}</TableCell>
                      <TableCell>{r.pct}%</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        </TabsContent>

        {/* Driver Acceptance Tab */}
        <TabsContent value="acceptance" className="space-y-4">
          <div className="grid grid-cols-3 gap-4">
            <Card>
              <CardContent className="pt-4">
                <div className="text-sm text-muted-foreground">Avg Acceptance Rate</div>
                <div className="text-2xl font-bold text-green-600">
                  {driverRates?.avg_acceptance_rate || 0}%
                </div>
              </CardContent>
            </Card>
            <Card>
              <CardContent className="pt-4">
                <div className="text-sm text-muted-foreground">Total Active Drivers</div>
                <div className="text-2xl font-bold">{driverRates?.total_drivers || 0}</div>
              </CardContent>
            </Card>
            <Card>
              <CardContent className="pt-4">
                <div className="text-sm text-muted-foreground flex items-center gap-1">
                  <TrendingDown className="h-3 w-3 text-red-500" /> Low Performers (&lt;70%)
                </div>
                <div className="text-2xl font-bold text-red-600">
                  {driverRates?.low_performer_count || 0}
                </div>
              </CardContent>
            </Card>
          </div>

          <Card>
            <CardHeader>
              <CardTitle>
                <div className="flex items-center gap-2">
                  <Target className="h-5 w-5" />
                  Driver Acceptance Rankings
                </div>
              </CardTitle>
            </CardHeader>
            <CardContent>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>#</TableHead>
                    <TableHead>Driver</TableHead>
                    <TableHead>Acceptance Rate</TableHead>
                    <TableHead>Total Rides</TableHead>
                    <TableHead>Completed</TableHead>
                    <TableHead>Rating</TableHead>
                    <TableHead>Status</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {(driverRates?.drivers || []).map((d: any, i: number) => (
                    <TableRow key={d.driver_id}>
                      <TableCell className="text-muted-foreground">{i + 1}</TableCell>
                      <TableCell className="font-medium">{d.name || 'Unknown'}</TableCell>
                      <TableCell>
                        <div className="flex items-center gap-2">
                          <div className="w-16 h-2 bg-gray-200 rounded-full overflow-hidden">
                            <div
                              className="h-full rounded-full"
                              style={{
                                width: `${d.acceptance_rate}%`,
                                backgroundColor: d.acceptance_rate >= 80 ? '#10B981'
                                  : d.acceptance_rate >= 60 ? '#F59E0B' : '#EF4444',
                              }}
                            />
                          </div>
                          <span className="text-sm font-mono">{d.acceptance_rate}%</span>
                        </div>
                      </TableCell>
                      <TableCell>{d.total_rides}</TableCell>
                      <TableCell>{d.completed}</TableCell>
                      <TableCell>{d.rating?.toFixed(1) || '-'}</TableCell>
                      <TableCell>
                        <Badge className={d.is_online ? "bg-green-100 text-green-700" : "bg-gray-100 text-gray-500"}>
                          {d.is_online ? "Online" : "Offline"}
                        </Badge>
                      </TableCell>
                    </TableRow>
                  ))}
                  {(!driverRates?.drivers || driverRates.drivers.length === 0) && (
                    <TableRow>
                      <TableCell colSpan={7} className="text-center py-8 text-muted-foreground">
                        No driver data available for this period
                      </TableCell>
                    </TableRow>
                  )}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}
