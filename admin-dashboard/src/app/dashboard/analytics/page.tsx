"use client";

import { useEffect, useState, useCallback, useMemo, Suspense } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";
import { useTableSort, SortableHead } from "@/components/ui/sortable-table";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import {
  BarChart3, TrendingDown, XCircle, CheckCircle,
  RefreshCw, Activity, Car, DollarSign, Target, Search, AlertTriangle,
  MapPin, Send, TrendingUp, Users, Timer, LayoutDashboard,
} from "lucide-react";
import { useTheme } from "next-themes";
import { Input } from "@/components/ui/input";
import { CHART_PALETTE_DARK, CHART_PALETTE_LIGHT } from "@/components/analytics/chart-palette";
import { DriverOffersPanel } from "@/components/analytics/driver-offers-panel";
import { MarketplaceOverviewPanel } from "@/components/analytics/marketplace-overview-panel";
import { SupplyPanel } from "@/components/analytics/supply-panel";
import { EfficiencyPanel } from "@/components/analytics/efficiency-panel";
import { FinancialPanel } from "@/components/analytics/financial-panel";
import { DemandForecastPanel } from "@/components/analytics/demand-forecast-panel";
import { Pagination } from "@/components/ui/pagination";
import {
  BarChart, Bar, PieChart, Pie, Cell,
  XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend,
} from "recharts";
import {
  getAnalyticsOverview, getCancellationBreakdown, getDriverAcceptanceRates, getServiceAreas,
} from "@/lib/api";

const DRIVER_PAGE_SIZE = 25;

/** Sentinel for "no area filter" — Radix Select cannot hold an empty value. */
const ALL_AREAS = "__all__";

/** Tab ids are part of the URL contract — /dashboard/driver-offers and
 *  /dashboard/forecast redirect to ?tab=offers / ?tab=forecast, so renaming
 *  one of these breaks those redirects and any bookmark. */
const TAB_IDS = [
  "overview", "supply", "efficiency", "financial",
  "cancellations", "acceptance", "offers", "forecast",
] as const;
const DEFAULT_TAB = "overview";

/** Server-side sortable columns on /driver-acceptance. Sorting must be
 *  server-side here: a client-side sort only reorders the rows already on
 *  the page, which is what let the worst performers stay invisible. */
type DriverSort =
  | "completion_rate" | "cancellation_rate" | "total_rides"
  | "completed" | "rating" | "name";

const DATE_RANGES = [
  { value: "today", label: "Today" },
  { value: "7d", label: "7 Days" },
  { value: "30d", label: "30 Days" },
  { value: "90d", label: "90 Days" },
  { value: "1y", label: "1 Year" },
];

// Cancellation-reason hues come from the same validated categorical palette
// every other Analytics chart uses (see chart-palette.ts), so the four real
// reasons carry palette slots and the three residual buckets are greys — the
// "Other" treatment, rather than inventing three more hues.
//
// Dark mode uses the palette's selected deeper steps: the light amber and
// emerald steps fail the lightness band against the dark surface.
function reasonColors(isDark: boolean): Record<string, string> {
  const p = isDark ? CHART_PALETTE_DARK : CHART_PALETTE_LIGHT;
  return {
    rider_cancelled: p[0],       // blue
    no_drivers_available: p[4],  // red
    driver_cancelled: p[2],      // amber
    search_timeout: p[3],        // violet
    scheduled_cancelled: isDark ? "#6B7280" : "#9CA3AF",
    unspecified: isDark ? "#4B5563" : "#D1D5DB",
    other: isDark ? "#52525B" : "#9CA3AF",
  };
}

const REASON_LABELS: Record<string, string> = {
  rider_cancelled: "Rider Cancelled",
  no_drivers_available: "No Drivers",
  driver_cancelled: "Driver Cancelled",
  search_timeout: "Search Timeout",
  scheduled_cancelled: "Scheduled Cancelled",
  unspecified: "Unspecified",
  other: "Other",
};

/** Rendered in place of a chart whose request failed. Deliberately distinct
 *  from the empty state: "we don't know" and "there were none" are different
 *  answers, and on this page the second one reads as good news. */
function SectionError({ onRetry }: { onRetry: () => void }) {
  return (
    <div className="py-8 text-center space-y-3">
      <p className="text-sm text-red-600 dark:text-red-400">
        Couldn&apos;t load this data — it isn&apos;t zero, it&apos;s unknown.
      </p>
      <Button variant="outline" size="sm" onClick={onRetry}>
        <RefreshCw className="h-3 w-3 mr-1" /> Retry
      </Button>
    </div>
  );
}

/** Next.js requires any component calling useSearchParams() to sit under a
 *  Suspense boundary, or the build fails on the prerender pass. */
export default function AnalyticsPage() {
  return (
    <Suspense fallback={<div className="py-16 text-center text-sm text-muted-foreground">Loading analytics…</div>}>
      <AnalyticsPageInner />
    </Suspense>
  );
}

function AnalyticsPageInner() {
  const [dateRange, setDateRange] = useState("30d");
  // One service-area filter shared by every tab, so "Saskatoon" means the
  // same thing on the funnel, the cancellations, and the offer ledger.
  const [areaId, setAreaId] = useState<string>(ALL_AREAS);
  const [areas, setAreas] = useState<any[]>([]);
  // Bumped by Refresh; the embedded panels watch it to refetch.
  const [refreshToken, setRefreshToken] = useState(0);
  const [loading, setLoading] = useState(true);
  // Per-section, not one global flag. A failed request rendered as
  // "No data for selected period" is a lie that reads as good news on a
  // page whose whole job is spotting bad news.
  const [overviewError, setOverviewError] = useState(false);
  const [cancelError, setCancelError] = useState(false);
  const [driverError, setDriverError] = useState(false);
  const fetchError = overviewError || cancelError || driverError;
  const [overview, setOverview] = useState<any>(null);
  const [cancellations, setCancellations] = useState<any>(null);
  const [driverRates, setDriverRates] = useState<any>(null);

  // Driver-table query lives server-side (page, search, sort, low-performer
  // filter) so the table can reach every driver the summary cards count —
  // not just the first page of the default descending sort.
  const [driverPage, setDriverPage] = useState(0);
  const [driverSort, setDriverSort] = useState<DriverSort>("completion_rate");
  const [driverOrder, setDriverOrder] = useState<"asc" | "desc">("desc");
  const [lowOnly, setLowOnly] = useState(false);
  const [searchInput, setSearchInput] = useState("");
  const [driverSearch, setDriverSearch] = useState("");
  const [driversLoading, setDriversLoading] = useState(true);

  const svcArea = areaId === ALL_AREAS ? undefined : areaId;

  // Tab selection lives in the URL so a tab can be linked, bookmarked and
  // reached by the redirects from the old standalone routes.
  const router = useRouter();
  const searchParams = useSearchParams();
  const requestedTab = searchParams.get("tab");
  const tab = (TAB_IDS as readonly string[]).includes(requestedTab ?? "")
    ? (requestedTab as string)
    : DEFAULT_TAB;

  const onTabChange = useCallback(
    (next: string) => {
      const sp = new URLSearchParams(searchParams.toString());
      if (next === DEFAULT_TAB) sp.delete("tab");
      else sp.set("tab", next);
      const qs = sp.toString();
      // replace, not push — flipping tabs shouldn't fill the back button.
      router.replace(qs ? `?${qs}` : "/dashboard/analytics", { scroll: false });
    },
    [router, searchParams],
  );

  const { resolvedTheme } = useTheme();
  const REASON_COLORS = useMemo(() => reasonColors(resolvedTheme === "dark"), [resolvedTheme]);

  // Buckets are America/Regina business time (migration 350), not UTC nor the
  // viewer's browser zone. Label it — a bare "14:00" is ambiguous, and it was
  // silently six hours out before that migration.
  const bucketTz: string = overview?.timezone || cancellations?.timezone || "America/Regina";
  const tzLabel = bucketTz.split("/").pop()?.replace(/_/g, " ") || bucketTz;

  useEffect(() => {
    getServiceAreas().then((a) => setAreas(Array.isArray(a) ? a : [])).catch(() => setAreas([]));
  }, []);

  // Debounce the search box so typing doesn't fire a request per keystroke.
  useEffect(() => {
    const t = setTimeout(() => setDriverSearch(searchInput.trim()), 300);
    return () => clearTimeout(t);
  }, [searchInput]);

  // Any change to what's being asked for restarts at the first page —
  // otherwise page 3 of a 2-page result silently renders empty.
  useEffect(() => {
    setDriverPage(0);
  }, [dateRange, svcArea, driverSearch, driverSort, driverOrder, lowOnly]);

  const fetchCore = useCallback(async () => {
    setLoading(true);
    try {
      const [ov, cancel] = await Promise.all([
        getAnalyticsOverview(dateRange, svcArea).catch(() => null),
        getCancellationBreakdown(dateRange, svcArea).catch(() => null),
      ]);
      setOverviewError(ov === null);
      setCancelError(cancel === null);
      setOverview(ov);
      setCancellations(cancel);
    } finally {
      setLoading(false);
    }
  }, [dateRange, svcArea]);

  const fetchDrivers = useCallback(async () => {
    setDriversLoading(true);
    try {
      const drivers = await getDriverAcceptanceRates(dateRange, {
        serviceAreaId: svcArea,
        limit: DRIVER_PAGE_SIZE,
        offset: driverPage * DRIVER_PAGE_SIZE,
        search: driverSearch || undefined,
        sortBy: driverSort,
        order: driverOrder,
        lowPerformersOnly: lowOnly || undefined,
      }).catch(() => null);
      setDriverError(drivers === null);
      setDriverRates(drivers);
    } finally {
      setDriversLoading(false);
    }
  }, [dateRange, svcArea, driverPage, driverSearch, driverSort, driverOrder, lowOnly]);

  const fetchAll = useCallback(() => {
    void fetchCore();
    void fetchDrivers();
    setRefreshToken((t) => t + 1); // embedded panels own their own fetches
  }, [fetchCore, fetchDrivers]);

  useEffect(() => { void fetchCore(); }, [fetchCore]);
  useEffect(() => { void fetchDrivers(); }, [fetchDrivers]);

  // Clicking a column header sorts server-side across the whole result set.
  const toggleDriverSort = useCallback((col: DriverSort) => {
    setDriverSort((prev) => {
      if (prev === col) {
        setDriverOrder((o) => (o === "asc" ? "desc" : "asc"));
        return prev;
      }
      // New column starts descending for rates/counts, ascending for names.
      setDriverOrder(col === "name" ? "asc" : "desc");
      return col;
    });
  }, []);

  const pieData = (cancellations?.reasons || []).map((r: any) => ({
    name: REASON_LABELS[r.reason] || r.reason,
    value: r.count,
    color: REASON_COLORS[r.reason] || "#9CA3AF",
  }));

  const { sorted: sortedReasons, sort: reasonSort, toggle: toggleReason } =
    useTableSort<any>(cancellations?.reasons || []);
  // Driver rows arrive already sorted + paged by the server — see fetchDrivers.
  const driverRows: any[] = driverRates?.drivers || [];

  // Adapt the server-side sort state to SortableHead's {key, dir} shape so the
  // headers keep their usual affordance while sorting the full result set.
  const headSort = useMemo(
    () => ({ key: driverSort as string, dir: driverOrder }),
    [driverSort, driverOrder],
  );
  const onHeadSort = useCallback(
    (key: string) => toggleDriverSort(key as DriverSort),
    [toggleDriverSort],
  );

  // Threshold comes from the server so the row highlight, the summary count,
  // and the `low_performers_only` filter can't disagree.
  const isLowPerformer = useCallback(
    (d: any) =>
      Number(d?.completion_rate ?? d?.acceptance_rate ?? 0) <
        (driverRates?.low_performer_threshold?.rate_below ?? 70) &&
      Number(d?.total_rides ?? 0) >= (driverRates?.low_performer_threshold?.min_rides ?? 5),
    [driverRates],
  );

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
            {svcArea
              ? `${areas.find((a: any) => a.id === svcArea)?.name || "Selected area"} — acceptance, cancellations, dispatch and demand`
              : "All service areas — acceptance, cancellations, dispatch and demand"}
          </p>
        </div>
        <div className="flex gap-2 items-center flex-wrap">
          <Select value={areaId} onValueChange={setAreaId}>
            <SelectTrigger className="w-44" aria-label="Filter by service area">
              <span className="flex items-center gap-1.5 truncate">
                <MapPin className="h-3.5 w-3.5 shrink-0" />
                <SelectValue />
              </span>
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL_AREAS}>All service areas</SelectItem>
              {areas
                .filter((a: any) => a.is_active !== false && !a.parent_service_area_id)
                .map((a: any) => (
                  <SelectItem key={a.id} value={a.id}>{a.name || a.id}</SelectItem>
                ))}
            </SelectContent>
          </Select>
          <Select value={dateRange} onValueChange={setDateRange}>
            <SelectTrigger className="w-32" aria-label="Date range">
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

      {/* Backend error banner */}
      {fetchError && !loading && (
        <div className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 flex flex-wrap items-center justify-between gap-2 dark:border-red-800 dark:bg-red-950 dark:text-red-200">
          <span>Analytics data unavailable — the backend returned an error. Check service health and try again.</span>
          <Button variant="outline" size="sm" onClick={fetchAll} className="text-red-700 border-red-300 hover:bg-red-100 dark:text-red-200 dark:border-red-700 dark:hover:bg-red-900">
            <RefreshCw className="h-3 w-3 mr-1" /> Retry
          </Button>
        </div>
      )}

      {/* KPI Cards — hidden only when genuinely absent, never as a way of
          hiding a failure; the banner above plus SectionError cover that. */}
      {overview && !overviewError && (
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
              <div className="text-2xl font-bold mt-1 text-green-600 dark:text-green-400">{overview.completion_rate}%</div>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="pt-4">
              <div className="flex items-center gap-2 text-sm text-muted-foreground">
                <XCircle className="h-4 w-4 text-red-500" /> Cancellation Rate
              </div>
              <div className="text-2xl font-bold mt-1 text-red-600 dark:text-red-400">{overview.cancellation_rate}%</div>
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
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Activity className="h-5 w-5" /> Daily Ride Trend
            <span className="text-xs font-normal text-muted-foreground">({tzLabel} days)</span>
          </CardTitle>
        </CardHeader>
        <CardContent>
          {overviewError ? (
            <SectionError onRetry={fetchCore} />
          ) : overview?.daily_chart && overview.daily_chart.length > 0 ? (
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
          ) : (
            <p className="text-sm text-muted-foreground text-center py-8">No data for selected period</p>
          )}
        </CardContent>
      </Card>

      <Tabs value={tab} onValueChange={onTabChange}>
        {/* Horizontally scrollable so the tab row degrades gracefully on
            narrow screens instead of wrapping into the content below. */}
        <div className="overflow-x-auto">
          <TabsList>
            <TabsTrigger value="overview" className="gap-1.5">
              <LayoutDashboard className="h-3.5 w-3.5" /> Overview
            </TabsTrigger>
            <TabsTrigger value="supply" className="gap-1.5">
              <Users className="h-3.5 w-3.5" /> Supply
            </TabsTrigger>
            <TabsTrigger value="efficiency" className="gap-1.5">
              <Timer className="h-3.5 w-3.5" /> Efficiency
            </TabsTrigger>
            <TabsTrigger value="financial" className="gap-1.5">
              <DollarSign className="h-3.5 w-3.5" /> Financial
            </TabsTrigger>
            <TabsTrigger value="cancellations">Cancellations</TabsTrigger>
            <TabsTrigger value="acceptance">Driver Completion</TabsTrigger>
            <TabsTrigger value="offers" className="gap-1.5">
              <Send className="h-3.5 w-3.5" /> Dispatch Offers
            </TabsTrigger>
            <TabsTrigger value="forecast" className="gap-1.5">
              <TrendingUp className="h-3.5 w-3.5" /> Demand Forecast
            </TabsTrigger>
          </TabsList>
        </div>

        {/* Marketplace health — the funnel and the CLAUDE.md KPI targets. */}
        <TabsContent value="overview" className="space-y-4">
          <MarketplaceOverviewPanel
            dateRange={dateRange}
            serviceAreaId={svcArea}
            refreshToken={refreshToken}
          />
        </TabsContent>

        <TabsContent value="supply" className="space-y-4">
          <SupplyPanel dateRange={dateRange} serviceAreaId={svcArea} refreshToken={refreshToken} />
        </TabsContent>

        <TabsContent value="efficiency" className="space-y-4">
          <EfficiencyPanel dateRange={dateRange} serviceAreaId={svcArea} refreshToken={refreshToken} />
        </TabsContent>

        <TabsContent value="financial" className="space-y-4">
          <FinancialPanel dateRange={dateRange} serviceAreaId={svcArea} refreshToken={refreshToken} />
        </TabsContent>

        {/* Cancellation Breakdown Tab */}
        <TabsContent value="cancellations" className="space-y-4">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {/* Pie Chart */}
            <Card>
              <CardHeader>
                <CardTitle>By Reason</CardTitle>
              </CardHeader>
              <CardContent>
                {cancelError ? (
                  <SectionError onRetry={fetchCore} />
                ) : pieData.length === 0 ? (
                  <div className="text-center py-8 text-muted-foreground">
                    No cancellations in this period
                  </div>
                ) : (
                  <ResponsiveContainer width="100%" height={300}>
                    <PieChart>
                      <Pie
                        data={pieData}
                        dataKey="value"
                        nameKey="name"
                        cx="50%" cy="50%"
                        outerRadius={90}
                        /* Label only the slices big enough to read — labels on
                           every slice collide once several small reasons
                           appear. The table below carries exact values. */
                        label={(props: any) =>
                          (props.percent ?? 0) >= 0.08
                            ? `${((props.percent ?? 0) * 100).toFixed(0)}%`
                            : ""
                        }
                        labelLine={false}
                      >
                        {pieData.map((entry: any, i: number) => (
                          <Cell key={i} fill={entry.color} />
                        ))}
                      </Pie>
                      {/* Identity must not rest on colour alone. */}
                      <Legend wrapperStyle={{ fontSize: 12 }} />
                      <Tooltip
                        formatter={(v: any, n: any) => [Number(v).toLocaleString(), n]}
                        contentStyle={{
                          fontSize: 12, borderRadius: 10,
                          border: "1px solid hsl(var(--border))",
                          background: "hsl(var(--card))",
                        }}
                      />
                    </PieChart>
                  </ResponsiveContainer>
                )}
              </CardContent>
            </Card>

            {/* Hourly Distribution */}
            <Card>
              <CardHeader>
                <CardTitle className="flex items-baseline gap-2">
                  Cancellations by Hour
                  <span className="text-xs font-normal text-muted-foreground">({tzLabel} time)</span>
                </CardTitle>
              </CardHeader>
              <CardContent>
                {cancelError ? (
                  <SectionError onRetry={fetchCore} />
                ) : (cancellations?.hourly_distribution?.length ?? 0) > 0 ? (
                  <ResponsiveContainer width="100%" height={300}>
                    <BarChart data={cancellations.hourly_distribution}>
                      <CartesianGrid strokeDasharray="3 3" />
                      <XAxis dataKey="hour" fontSize={11} tickFormatter={(h) => `${h}:00`} />
                      <YAxis fontSize={11} />
                      <Tooltip labelFormatter={(h) => `${h}:00`} />
                      <Bar dataKey="count" fill="#EF4444" radius={[3, 3, 0, 0]} name="Cancellations" />
                    </BarChart>
                  </ResponsiveContainer>
                ) : (
                  <p className="text-sm text-muted-foreground text-center py-8">No cancellation data</p>
                )}
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
                    <SortableHead column="reason" sort={reasonSort} onSort={toggleReason}>Reason</SortableHead>
                    <SortableHead column="count" sort={reasonSort} onSort={toggleReason} align="right">Count</SortableHead>
                    <SortableHead column="pct" sort={reasonSort} onSort={toggleReason} align="right">Percentage</SortableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {sortedReasons.map((r: any) => (
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
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            <Card>
              <CardContent className="pt-4">
                <div className="text-sm text-muted-foreground">Avg Completion Rate</div>
                <div className="text-2xl font-bold text-green-600 dark:text-green-400">
                  {driverRates?.avg_completion_rate_active ?? 0}%
                </div>
                {/* Averaging over every registered driver (idle ones score 0)
                    made this read ~9% for a fleet actually running at ~90%. */}
                <p className="text-xs text-muted-foreground mt-0.5">
                  drivers with rides only
                </p>
              </CardContent>
            </Card>
            <Card>
              <CardContent className="pt-4">
                <div className="text-sm text-muted-foreground">Drivers</div>
                <div className="text-2xl font-bold">{driverRates?.total_drivers || 0}</div>
                <p className="text-xs text-muted-foreground mt-0.5">
                  {driverRates?.drivers_with_rides ?? 0} with rides this period
                </p>
              </CardContent>
            </Card>
            <Card className={lowOnly ? "ring-2 ring-red-500" : undefined}>
              <CardContent className="pt-4">
                <div className="text-sm text-muted-foreground flex items-center gap-1">
                  <TrendingDown className="h-3 w-3 text-red-500" />
                  Low completion (&lt;{driverRates?.low_performer_threshold?.rate_below ?? 70}%)
                </div>
                <div className="text-2xl font-bold text-red-600 dark:text-red-400">
                  {driverRates?.low_performer_count || 0}
                </div>
                {/* The count is meaningless if you can't see who it refers to —
                    this toggles the table to exactly those drivers. */}
                <Button
                  variant="link"
                  size="sm"
                  className="h-auto p-0 text-xs"
                  aria-pressed={lowOnly}
                  onClick={() => setLowOnly((v) => !v)}
                >
                  {lowOnly ? "Show all drivers" : "Show only these"}
                </Button>
              </CardContent>
            </Card>
          </div>

          <Card>
            <CardHeader className="gap-3">
              <CardTitle>
                <div className="flex items-center gap-2">
                  <Target className="h-5 w-5" />
                  Driver Completion Rankings
                </div>
              </CardTitle>
              {/* This tab measures completed/assigned. A driver who accepts
                  every offer but whose riders cancel scores low here — true
                  acceptance is offer-ledger data, on the Dispatch Offers tab. */}
              <p className="text-xs text-muted-foreground">
                Completed rides as a share of rides assigned. For true offer
                acceptance (accepted vs offered), see the Dispatch Offers tab.
              </p>
              <div className="flex flex-wrap items-center gap-2">
                <div className="relative flex-1 min-w-[200px]">
                  <Search className="absolute left-2 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                  <Input
                    className="pl-8"
                    placeholder="Search driver by name…"
                    aria-label="Search driver by name"
                    value={searchInput}
                    onChange={(e) => setSearchInput(e.target.value)}
                  />
                </div>
                {lowOnly && (
                  <Badge variant="outline" className="border-red-300 text-red-700 dark:text-red-400">
                    Low performers only
                    <button
                      type="button"
                      onClick={() => setLowOnly(false)}
                      aria-label="Clear low-performer filter"
                      className="ml-1 font-bold"
                    >
                      ×
                    </button>
                  </Badge>
                )}
              </div>
              {/* The driver list is capped server-side; say so rather than
                  presenting a partial list as the whole fleet. */}
              {driverRates?.scan_truncated && (
                <div className="flex items-center gap-2 rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:border-amber-700 dark:bg-amber-950 dark:text-amber-200">
                  <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
                  Driver list hit the server scan cap — totals below cover only the drivers scanned. Narrow by service area or search.
                </div>
              )}
            </CardHeader>
            <CardContent>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>#</TableHead>
                    <SortableHead column="name" sort={headSort} onSort={onHeadSort}>Driver</SortableHead>
                    <SortableHead column="completion_rate" sort={headSort} onSort={onHeadSort}>Completion Rate</SortableHead>
                    <SortableHead column="total_rides" sort={headSort} onSort={onHeadSort} align="right">Total Rides</SortableHead>
                    <SortableHead column="completed" sort={headSort} onSort={onHeadSort} align="right">Completed</SortableHead>
                    <SortableHead column="cancellation_rate" sort={headSort} onSort={onHeadSort} align="right">Cancel Rate</SortableHead>
                    <SortableHead column="rating" sort={headSort} onSort={onHeadSort} align="right">Rating</SortableHead>
                    <TableHead>Status</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {driverRows.map((d: any, i: number) => (
                    <TableRow key={d.driver_id} className={isLowPerformer(d) ? "bg-red-50/60 dark:bg-red-950/30" : undefined}>
                      {/* Absolute position in the current server-side sort,
                          not a per-page index. */}
                      <TableCell className="text-muted-foreground tabular-nums">
                        {driverPage * DRIVER_PAGE_SIZE + i + 1}
                      </TableCell>
                      <TableCell className="font-medium">{d.name || 'Unknown'}</TableCell>
                      <TableCell>
                        <div className="flex items-center gap-2">
                          <div className="w-16 h-2 bg-muted rounded-full overflow-hidden">
                            <div
                              className="h-full rounded-full"
                              style={{
                                width: `${Math.max(0, Math.min(100, Number(d.completion_rate) || 0))}%`,
                                backgroundColor: d.completion_rate >= 80 ? '#10B981'
                                  : d.completion_rate >= 60 ? '#F59E0B' : '#EF4444',
                              }}
                            />
                          </div>
                          <span className="text-sm font-mono">{d.completion_rate}%</span>
                        </div>
                      </TableCell>
                      <TableCell className="text-right tabular-nums">{d.total_rides}</TableCell>
                      <TableCell className="text-right tabular-nums">{d.completed}</TableCell>
                      <TableCell className="text-right tabular-nums">{d.cancellation_rate}%</TableCell>
                      <TableCell className="text-right tabular-nums">
                        {typeof d.rating === "number" && d.rating > 0 ? d.rating.toFixed(1) : '-'}
                      </TableCell>
                      <TableCell>
                        <Badge className={d.is_online
                          ? "bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-200"
                          : "bg-gray-100 text-gray-500 dark:bg-gray-800 dark:text-gray-400"}>
                          {d.is_online ? "Online" : "Offline"}
                        </Badge>
                      </TableCell>
                    </TableRow>
                  ))}
                  {driverRows.length === 0 && (
                    <TableRow>
                      <TableCell
                        colSpan={8}
                        className={`text-center py-8 ${
                          driverError ? "text-red-600 dark:text-red-400" : "text-muted-foreground"
                        }`}
                      >
                        {driverError
                          ? "Couldn't load drivers — this isn't an empty list, it's unknown."
                          : driversLoading
                          ? "Loading drivers…"
                          : driverSearch
                            ? `No driver matching “${driverSearch}” in this period`
                            : lowOnly
                              ? "No drivers below the low-performer threshold — nothing to action"
                              : "No driver data available for this period"}
                      </TableCell>
                    </TableRow>
                  )}
                </TableBody>
              </Table>
              {(driverRates?.total_drivers ?? 0) > 0 && (
                <Pagination
                  className="mt-4"
                  page={driverPage}
                  pageSize={DRIVER_PAGE_SIZE}
                  hasNextPage={Boolean(driverRates?.has_more)}
                  totalCount={driverRates?.total_drivers}
                  onPageChange={setDriverPage}
                />
              )}
            </CardContent>
          </Card>
        </TabsContent>

        {/* Dispatch Offers — the offer ledger's real accept/decline/ignore
            rates. Same component the standalone /dashboard/driver-offers
            page renders, driven by this page's shared filter bar. */}
        <TabsContent value="offers" className="space-y-4">
          <DriverOffersPanel
            dateRange={dateRange}
            serviceAreaId={svcArea}
            refreshToken={refreshToken}
          />
        </TabsContent>

        {/* Demand Forecast — forward-looking, so it keeps its own
            hours-ahead control and ignores the backward-looking date range. */}
        <TabsContent value="forecast" className="space-y-4">
          <DemandForecastPanel
            serviceAreaId={svcArea}
            refreshToken={refreshToken}
          />
        </TabsContent>
      </Tabs>
    </div>
  );
}
