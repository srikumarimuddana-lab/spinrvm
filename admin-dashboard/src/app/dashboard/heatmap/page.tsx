"use client";

import { useEffect, useState, useCallback, useMemo, useRef } from "react";
import { getHeatMapData, getHeatMapSettings, getServiceAreas, getSurgeStatus, getDemandForecast, HeatMapData, HeatMapSettings } from "@/lib/api";
import dynamic from "next/dynamic";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Loader2, RefreshCw, Users, Car, Building2, AlertTriangle, TrendingUp, TrendingDown, MapPin } from "lucide-react";
import Link from "next/link";
import { useAuthStore } from "@/store/authStore";
import {
    DEMAND_BANDS,
    bandForRatio,
    bandRangeLabel,
    demandBarWidths,
    demandPressure,
    isDormant,
} from "@/lib/demand-bands";
import { ForecastSlot, toForecastSlots, forecastBarHeightPct } from "@/lib/demand-forecast-transform";

interface AreaDemand {
    area_id: string;
    name: string;
    demand_count: number;
    supply_count: number;
    ratio: number;
    multiplier: number;
    surge_active: boolean;
    surge_enabled: boolean;
    source: string;
    /** Active demand above idle supply — a pressure signal, NOT stranded riders. */
    pressure: number;
}

// Dynamic import — MapLibre GL needs window, so defer to client.
const HeatMap = dynamic(() => import("@/components/heat-map"), {
    ssr: false,
    loading: () => (
        <div className="w-full h-[600px] bg-muted animate-pulse rounded-lg flex items-center justify-center">
            <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
        </div>
    ),
});

const DATE_RANGE_PRESETS = [
    { label: "Today", value: "today" },
    { label: "7 Days", value: "7d" },
    { label: "30 Days", value: "30d" },
    { label: "90 Days", value: "90d" },
    { label: "1 Year", value: "1y" },
];

export default function HeatMapPage() {
    // The Live Monitoring page is gated by the `rides` module while this page
    // is gated by `heatmap` — different grants. Linking there unconditionally
    // would send a heatmap-only admin to /403, which reads as a broken link
    // rather than a permission they don't have. Mirrors sidebar.tsx: super_admin
    // bypasses, every other role (including "admin") needs the module.
    const authUser = useAuthStore((s) => s.user);
    const canOpenMonitoring =
        authUser?.role === "super_admin" || (authUser?.modules ?? []).includes("rides");

    const [loading, setLoading] = useState(true);
    const [heatMapData, setHeatMapData] = useState<HeatMapData | null>(null);
    const [settings, setSettings] = useState<HeatMapSettings | null>(null);
    const [serviceAreas, setServiceAreas] = useState<any[]>([]);

    // Filter state
    const [filter, setFilter] = useState<"all" | "corporate" | "regular">("all");
    const [dateRange, setDateRange] = useState("30d");
    const [startDate, setStartDate] = useState("");
    const [endDate, setEndDate] = useState("");
    const [serviceAreaId, setServiceAreaId] = useState<string>("all");
    const [groupBy, setGroupBy] = useState<"pickup" | "dropoff" | "both">("both");
    const [dateError, setDateError] = useState<string | null>(null);

    // Display toggles
    const [showPickups, setShowPickups] = useState(true);
    const [showDropoffs, setShowDropoffs] = useState(true);

    // Live demand state. `showDemand` gates the 2-minute poll: without it the
    // page hit two of the most expensive admin endpoints forever from mount,
    // for every open tab, whether or not anyone was looking at the section.
    const [showDemand, setShowDemand] = useState(false);
    const [demandAreas, setDemandAreas] = useState<AreaDemand[]>([]);
    const [demandLoading, setDemandLoading] = useState(true);
    const [demandError, setDemandError] = useState<string | null>(null);
    const [demandFetchedAt, setDemandFetchedAt] = useState<Date | null>(null);
    const [forecast, setForecast] = useState<ForecastSlot[]>([]);

    // Fetch initial data
    useEffect(() => {
        Promise.all([
            getHeatMapSettings(),
            getServiceAreas()
        ])
            .then(([settingsData, areasData]) => {
                setSettings(settingsData);
                setServiceAreas(Array.isArray(areasData) ? areasData : []);

                // Set defaults from settings
                if (settingsData.heat_map_default_range) {
                    setDateRange(settingsData.heat_map_default_range);
                }
                setShowPickups(settingsData.heat_map_show_pickups ?? true);
                setShowDropoffs(settingsData.heat_map_show_dropoffs ?? true);
            })
            .catch(console.error)
            .finally(() => setLoading(false));
    }, []);

    // Calculate date range based on preset
    const getDateRange = useCallback(() => {
        const now = new Date();
        let start: Date;
        let end = now;

        switch (dateRange) {
            case "today":
                start = new Date(now.getFullYear(), now.getMonth(), now.getDate());
                break;
            case "7d":
                start = new Date(now.getTime() - 7 * 24 * 60 * 60 * 1000);
                break;
            case "30d":
                start = new Date(now.getTime() - 30 * 24 * 60 * 60 * 1000);
                break;
            case "90d":
                start = new Date(now.getTime() - 90 * 24 * 60 * 60 * 1000);
                break;
            case "1y":
                start = new Date(now.getFullYear() - 1, now.getMonth(), now.getDate());
                break;
            default:
                start = new Date(now.getTime() - 30 * 24 * 60 * 60 * 1000);
        }

        return {
            start_date: startDate || start.toISOString().split("T")[0],
            end_date: endDate || end.toISOString().split("T")[0],
        };
    }, [dateRange, startDate, endDate]);

    // Fetch heat map data when filters change
    const fetchHeatMapData = useCallback(() => {
        if (startDate && endDate && startDate > endDate) {
            setDateError("Start date must be before end date.");
            return;
        }
        setDateError(null); // clear previous error when dates are valid
        setLoading(true);
        const { start_date, end_date } = getDateRange();

        getHeatMapData({
            filter,
            start_date,
            end_date,
            service_area_id: serviceAreaId === "all" ? undefined : serviceAreaId,
            group_by: groupBy,
        })
            .then(setHeatMapData)
            .catch(console.error)
            .finally(() => setLoading(false));
    }, [filter, dateRange, startDate, endDate, serviceAreaId, groupBy, getDateRange]);

    useEffect(() => {
        fetchHeatMapData();
    }, [fetchHeatMapData]);

    // Fetch live demand + forecast.
    //
    // The two calls are settled independently rather than via Promise.all:
    // they are gated by different admin modules (service_areas vs dashboard),
    // so an all-or-nothing await meant a module-limited admin lost the half
    // they were entitled to see — and the empty state then blamed the surge
    // engine for what was really a permissions gap.
    const fetchDemandData = useCallback(() => {
        setDemandLoading(true);
        const areaIdParam = serviceAreaId === "all" ? undefined : serviceAreaId;

        const surgeP = getSurgeStatus()
            .then((surgeRes: any) => {
                const areas: AreaDemand[] = (surgeRes || []).map((a: any) => ({
                    area_id: a.area_id,
                    name: a.name,
                    demand_count: a.demand_count ?? 0,
                    supply_count: a.supply_count ?? 0,
                    ratio: a.ratio ?? 0,
                    multiplier: a.multiplier ?? 1.0,
                    surge_active: a.surge_active ?? false,
                    // Areas with surge administratively off must not be
                    // presented as if their ratio were driving pricing.
                    surge_enabled: a.surge_enabled ?? true,
                    source: a.source ?? "auto",
                    pressure: demandPressure(a.demand_count ?? 0, a.supply_count ?? 0),
                }));
                areas.sort((a, b) => b.ratio - a.ratio);
                setDemandAreas(areas);
                setDemandError(null);
                setDemandFetchedAt(new Date());
            })
            .catch((err) => {
                // Never swallow: stale colours with no signal invite bad calls.
                console.error("demand status fetch failed", err);
                setDemandError(
                    err?.status === 403
                        ? "You don't have the Service Areas module, so live demand can't be shown."
                        : "Couldn't refresh live demand."
                );
            });

        const forecastP = getDemandForecast(6, areaIdParam)
            .then((forecastRes: any) => setForecast(toForecastSlots(forecastRes)))
            .catch((err) => {
                console.error("demand forecast fetch failed", err);
                setForecast([]);
            });

        Promise.allSettled([surgeP, forecastP]).then(() => setDemandLoading(false));
    }, [serviceAreaId]);

    useEffect(() => {
        if (!showDemand) return;
        fetchDemandData();

        // Jittered rather than a fixed 120s interval. Every open tab that
        // enabled this at the same time (a shift handover, an incident everyone
        // opens at once) would otherwise land its polls in the same instant,
        // and each poll fans out to the surge-status and forecast endpoints.
        // ±10% is enough to spread them without changing the effective cadence.
        let cancelled = false;
        let timer: ReturnType<typeof setTimeout>;
        const scheduleNext = () => {
            const delay = 120_000 * (1 + (Math.random() * 0.2 - 0.1));
            timer = setTimeout(() => {
                if (cancelled) return;
                fetchDemandData();
                scheduleNext();
            }, delay);
        };
        scheduleNext();

        return () => { cancelled = true; clearTimeout(timer); };
    }, [fetchDemandData, showDemand]);

    // Convert API data to HeatMap component format.
    //
    // Memoised on heatMapData, not recreated per render. <HeatMap> keys its
    // data-sync effect on prop identity and calls an animated fitBounds() at
    // the end of it, so fresh array/object literals on every render meant the
    // 2-minute demand poll silently yanked an operator's zoomed-in map back to
    // full extent — a re-render caused by state this map does not consume.
    const pickupPoints = useMemo(
        () => heatMapData?.pickup_points?.map((p) => ({ lat: p[0], lng: p[1], intensity: p[2] })) ?? [],
        [heatMapData],
    );

    const dropoffPoints = useMemo(
        () => heatMapData?.dropoff_points?.map((p) => ({ lat: p[0], lng: p[1], intensity: p[2] })) ?? [],
        [heatMapData],
    );

    const heatMapSettings = useMemo(
        () => ({ radius: settings?.heat_map_radius || 25, blur: settings?.heat_map_blur || 15 }),
        [settings?.heat_map_radius, settings?.heat_map_blur],
    );

    const stats = {
        total_rides: 0,
        corporate_rides: 0,
        regular_rides: 0,
        ...(heatMapData?.stats || {}),
    };

    return (
        <div className="space-y-6">
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-3xl font-bold tracking-tight">Heat Map</h1>
                    <p className="text-muted-foreground mt-1">
                        Historical ride density —{" "}
                        {DATE_RANGE_PRESETS.find((p) => p.value === dateRange)?.label ?? "custom range"}.
                        Live demand is in the section below.
                    </p>
                </div>
                <div className="flex items-center gap-2">
                    <Button
                        variant="outline"
                        size="sm"
                        onClick={fetchHeatMapData}
                        disabled={loading || !!dateError}
                    >
                        <RefreshCw className={`mr-2 h-4 w-4 ${loading ? "animate-spin" : ""}`} />
                        Refresh
                    </Button>
                </div>
            </div>

            {/* Stats Cards */}
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                <Card>
                    <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                        <CardTitle className="text-sm font-medium">Total Rides</CardTitle>
                        <Car className="h-4 w-4 text-muted-foreground" />
                    </CardHeader>
                    <CardContent>
                        <div className="text-2xl font-bold">{stats.total_rides.toLocaleString()}</div>
                    </CardContent>
                </Card>
                <Card>
                    <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                        <CardTitle className="text-sm font-medium">Corporate Rides</CardTitle>
                        <Building2 className="h-4 w-4 text-muted-foreground" />
                    </CardHeader>
                    <CardContent>
                        <div className="text-2xl font-bold">{stats.corporate_rides.toLocaleString()}</div>
                        <p className="text-xs text-muted-foreground">
                            {stats.total_rides > 0
                                ? `${((stats.corporate_rides / stats.total_rides) * 100).toFixed(1)}% of total`
                                : "0% of total"}
                        </p>
                    </CardContent>
                </Card>
                <Card>
                    <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                        <CardTitle className="text-sm font-medium">Regular Rides</CardTitle>
                        <Users className="h-4 w-4 text-muted-foreground" />
                    </CardHeader>
                    <CardContent>
                        <div className="text-2xl font-bold">{stats.regular_rides.toLocaleString()}</div>
                        <p className="text-xs text-muted-foreground">
                            {stats.total_rides > 0
                                ? `${((stats.regular_rides / stats.total_rides) * 100).toFixed(1)}% of total`
                                : "0% of total"}
                        </p>
                    </CardContent>
                </Card>
            </div>

            {/* Filters */}
            <Card>
                <CardHeader>
                    <CardTitle className="text-lg">Filters</CardTitle>
                </CardHeader>
                <CardContent>
                    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
                        {/* Filter Type */}
                        <div className="space-y-2">
                            <Label>Ride Type</Label>
                            <Tabs value={filter} onValueChange={(v) => setFilter(v as any)}>
                                <TabsList className="w-full">
                                    <TabsTrigger value="all" className="flex-1">All</TabsTrigger>
                                    <TabsTrigger value="corporate" className="flex-1">Corporate</TabsTrigger>
                                    <TabsTrigger value="regular" className="flex-1">Regular</TabsTrigger>
                                </TabsList>
                            </Tabs>
                        </div>

                        {/* Date Range */}
                        <div className="space-y-2">
                            <Label>Date Range</Label>
                            <Select value={dateRange} onValueChange={setDateRange}>
                                <SelectTrigger>
                                    <SelectValue placeholder="Select range" />
                                </SelectTrigger>
                                <SelectContent>
                                    {DATE_RANGE_PRESETS.map((preset) => (
                                        <SelectItem key={preset.value} value={preset.value}>
                                            {preset.label}
                                        </SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                        </div>

                        {/* Service Area */}
                        <div className="space-y-2">
                            <Label>Service Area</Label>
                            <Select value={serviceAreaId} onValueChange={setServiceAreaId}>
                                <SelectTrigger>
                                    <SelectValue placeholder="All Areas" />
                                </SelectTrigger>
                                <SelectContent>
                                    <SelectItem value="all">All Areas</SelectItem>
                                    {serviceAreas.map((area) => (
                                        <SelectItem key={area.id} value={area.id}>
                                            {area.name}
                                        </SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                        </div>

                        {/* Group By */}
                        <div className="space-y-2">
                            <Label>Show</Label>
                            <Select value={groupBy} onValueChange={(v) => setGroupBy(v as any)}>
                                <SelectTrigger>
                                    <SelectValue placeholder="Select" />
                                </SelectTrigger>
                                <SelectContent>
                                    <SelectItem value="both">Pickups & Dropoffs</SelectItem>
                                    <SelectItem value="pickup">Pickups Only</SelectItem>
                                    <SelectItem value="dropoff">Dropoffs Only</SelectItem>
                                </SelectContent>
                            </Select>
                        </div>
                    </div>

                    {/* Custom Date Range */}
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mt-4">
                        <div className="space-y-2">
                            <Label>Start Date (Custom)</Label>
                            <Input
                                type="date"
                                value={startDate}
                                onChange={(e) => setStartDate(e.target.value)}
                            />
                        </div>
                        <div className="space-y-2">
                            <Label>End Date (Custom)</Label>
                            <Input
                                type="date"
                                value={endDate}
                                onChange={(e) => setEndDate(e.target.value)}
                            />
                        </div>
                    </div>
                    {dateError && (
                        <p className="text-sm text-destructive mt-2">{dateError}</p>
                    )}

                    {/* Display Toggles */}
                    <div className="flex items-center gap-6 mt-4 pt-4 border-t">
                        <div className="flex items-center space-x-2">
                            <Switch
                                id="show-pickups"
                                checked={showPickups}
                                onCheckedChange={setShowPickups}
                            />
                            <Label htmlFor="show-pickups">Show Pickups (Blue)</Label>
                        </div>
                        <div className="flex items-center space-x-2">
                            <Switch
                                id="show-dropoffs"
                                checked={showDropoffs}
                                onCheckedChange={setShowDropoffs}
                            />
                            <Label htmlFor="show-dropoffs">Show Dropoffs (Red/Green)</Label>
                        </div>
                    </div>
                </CardContent>
            </Card>

            {/* Heat Map */}
            <Card>
                <CardContent className="p-0">
                    {loading ? (
                        <div className="w-full h-[600px] bg-muted animate-pulse flex items-center justify-center">
                            <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
                        </div>
                    ) : (
                        <HeatMap
                            pickupPoints={pickupPoints}
                            dropoffPoints={dropoffPoints}
                            showPickups={showPickups}
                            showDropoffs={showDropoffs}
                            settings={heatMapSettings}
                            height="600px"
                        />
                    )}
                </CardContent>
            </Card>

            {/* Legend */}
            <Card>
                <CardContent className="pt-6">
                    <div className="flex flex-wrap items-center justify-center gap-8">
                        <div className="flex items-center gap-2">
                            <div className="w-4 h-4 rounded" style={{ background: "linear-gradient(to right, #00ffff, #0000aa)" }} />
                            <span className="text-sm text-muted-foreground">Pickups</span>
                        </div>
                        <div className="flex items-center gap-2">
                            <div className="w-4 h-4 rounded" style={{ background: "linear-gradient(to right, #00ff00, #ffff00, #ff0000)" }} />
                            <span className="text-sm text-muted-foreground">Dropoffs (Intensity)</span>
                        </div>
                        <div className="flex items-center gap-4 text-xs text-muted-foreground">
                            <span>Low</span>
                            <div className="w-24 h-2 rounded" style={{ background: "linear-gradient(to right, #00ff00, #ffff00, #ff0000)" }} />
                            <span>High</span>
                        </div>
                    </div>
                </CardContent>
            </Card>

            {/* ── Unmet Demand Section ─────────────────────────── */}
            <div className="space-y-4">
                <div className="flex items-center justify-between">
                    <div>
                        <h2 className="text-xl font-semibold tracking-tight">Live Demand Pressure</h2>
                        <p className="text-sm text-muted-foreground">
                            Current demand vs idle drivers per service area — separate from the
                            historical ride-density map above. Refreshes every 2 minutes while on.
                        </p>
                    </div>
                    <div className="flex items-center gap-3">
                        <div className="flex items-center gap-2">
                            <Switch id="show-live-demand" checked={showDemand} onCheckedChange={setShowDemand} />
                            <Label htmlFor="show-live-demand" className="text-sm">Live updates</Label>
                        </div>
                        <Button
                            variant="outline"
                            size="sm"
                            onClick={fetchDemandData}
                            disabled={demandLoading || !showDemand}
                        >
                            <RefreshCw className={`mr-2 h-4 w-4 ${demandLoading ? "animate-spin" : ""}`} />
                            Refresh
                        </Button>
                    </div>
                </div>

                {/* Staleness / error banner. Silent failure here means an operator
                    reads minutes-old colours as live and acts on them. */}
                {showDemand && demandError && (
                    <div role="alert" className="rounded-lg border border-destructive/40 bg-destructive/5 px-3 py-2 text-sm text-destructive">
                        {demandError}
                        {demandFetchedAt && (
                            <span className="text-muted-foreground">
                                {" "}Showing data from {demandFetchedAt.toLocaleTimeString()}.
                            </span>
                        )}
                    </div>
                )}
                {showDemand && !demandError && demandFetchedAt && (
                    <p className="text-xs text-muted-foreground" aria-live="polite">
                        Demand data as of {demandFetchedAt.toLocaleTimeString()}
                    </p>
                )}
                {!showDemand && (
                    <Card>
                        <CardContent className="py-8 text-center text-sm text-muted-foreground">
                            Live updates are off. Turn them on to poll current demand and supply.
                        </CardContent>
                    </Card>
                )}

                {/* Summary stats */}
                {showDemand && demandAreas.length > 0 && (
                    <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
                        <Card>
                            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                                <CardTitle className="text-sm font-medium">Active Demand</CardTitle>
                                <TrendingUp className="h-4 w-4 text-orange-500" />
                            </CardHeader>
                            <CardContent>
                                <div className="text-2xl font-bold">
                                    {demandAreas.reduce((s, a) => s + a.demand_count, 0)}
                                </div>
                                <p className="text-xs text-muted-foreground">
                                    rides in the last 10 min, incl. already matched
                                </p>
                            </CardContent>
                        </Card>
                        <Card>
                            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                                <CardTitle className="text-sm font-medium">Idle Supply</CardTitle>
                                <Car className="h-4 w-4 text-success" />
                            </CardHeader>
                            <CardContent>
                                <div className="text-2xl font-bold">
                                    {demandAreas.reduce((s, a) => s + a.supply_count, 0)}
                                </div>
                                <p className="text-xs text-muted-foreground">drivers online and available now</p>
                            </CardContent>
                        </Card>
                        <Card>
                            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                                <CardTitle className="text-sm font-medium">Demand Pressure</CardTitle>
                                <AlertTriangle className="h-4 w-4 text-destructive" />
                            </CardHeader>
                            <CardContent>
                                <div className="text-2xl font-bold text-destructive">
                                    {demandAreas.reduce((s, a) => s + a.pressure, 0)}
                                </div>
                                {/* Deliberately NOT "unfulfilled requests": demand_count
                                    includes rides that already have a driver, so this
                                    figure overstates stranded riders — a busy but
                                    fully-served market reads as a crisis. */}
                                <p className="text-xs text-muted-foreground">
                                    demand above idle supply — not stranded riders
                                </p>
                            </CardContent>
                        </Card>
                        <Card>
                            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                                <CardTitle className="text-sm font-medium">Surge Active</CardTitle>
                                <TrendingDown className="h-4 w-4 text-amber-500" />
                            </CardHeader>
                            <CardContent>
                                <div className="text-2xl font-bold">
                                    {demandAreas.filter(a => a.surge_active).length} / {demandAreas.length}
                                </div>
                                <p className="text-xs text-muted-foreground">areas with surge pricing</p>
                            </CardContent>
                        </Card>
                    </div>
                )}

                {/* Band legend — the same scale the monitoring map uses, so the
                    two screens can't drift apart in an operator's head. */}
                {showDemand && demandAreas.length > 0 && (
                    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs">
                        <span className="font-medium">Demand : supply ratio</span>
                        {DEMAND_BANDS.map((band) => (
                            <span key={band.key} className="flex items-center gap-1.5 text-muted-foreground">
                                <span
                                    aria-hidden="true"
                                    className="inline-block h-2.5 w-2.5 rounded-sm"
                                    style={{ backgroundColor: band.color }}
                                />
                                {band.label} ({bandRangeLabel(band)}) → {band.multiplier.toFixed(2)}× fare
                            </span>
                        ))}
                    </div>
                )}

                {/* Per-area demand cards */}
                {showDemand && (demandLoading && demandAreas.length === 0 ? (
                    <div className="flex items-center justify-center py-8">
                        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
                        <span className="sr-only">Loading demand data</span>
                    </div>
                ) : demandAreas.length === 0 ? (
                    <Card>
                        <CardContent className="py-8 text-center text-sm text-muted-foreground">
                            {demandError
                                ? "Demand data could not be loaded — see the message above."
                                : "No active service areas reported demand data."}
                        </CardContent>
                    </Card>
                ) : (
                    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                        {demandAreas.map((area) => {
                            const band = bandForRatio(area.ratio);
                            const dormant = isDormant(area.demand_count, area.supply_count);
                            const bar = demandBarWidths(area.demand_count, area.supply_count);
                            return (
                                <Card
                                    key={area.area_id}
                                    style={dormant ? undefined : { borderColor: `${band.color}80` }}
                                    className={dormant ? "opacity-70" : ""}
                                >
                                    <CardHeader className="pb-2">
                                        <div className="flex items-center justify-between">
                                            <CardTitle className="text-sm font-medium">{area.name}</CardTitle>
                                            <div className="flex items-center gap-1.5">
                                                {area.surge_active && (
                                                    <Badge variant="outline" className="text-xs bg-warning/10 text-warning border-warning/30">
                                                        {area.multiplier.toFixed(2)}× surge
                                                    </Badge>
                                                )}
                                                {area.source === "manual" && (
                                                    <Badge variant="outline" className="text-xs">Manual</Badge>
                                                )}
                                                {/* An area with surge switched off is not a
                                                    candidate for "go enable surge" — say so
                                                    rather than colouring it like one. */}
                                                {!area.surge_enabled && (
                                                    <Badge variant="outline" className="text-xs text-muted-foreground">
                                                        Surge off
                                                    </Badge>
                                                )}
                                            </div>
                                        </div>
                                    </CardHeader>
                                    <CardContent>
                                        {dormant ? (
                                            <p className="py-2 text-sm text-muted-foreground">
                                                No activity — no ride requests and no drivers online.
                                            </p>
                                        ) : (
                                            <div className="space-y-2">
                                                <div className="flex items-center justify-between text-sm">
                                                    <span className="text-muted-foreground">Demand / idle supply</span>
                                                    <span className="font-mono font-medium">
                                                        {area.demand_count} / {area.supply_count}
                                                    </span>
                                                </div>
                                                <div className="flex items-center justify-between text-sm">
                                                    <span className="text-muted-foreground">Ratio</span>
                                                    <span className={`font-mono font-medium ${band.textClass}`}>
                                                        {area.ratio.toFixed(2)}
                                                        <span className="ml-1 font-sans text-xs font-normal">
                                                            ({band.label})
                                                        </span>
                                                    </span>
                                                </div>
                                                {area.pressure > 0 && (
                                                    <div className="flex items-center justify-between text-sm">
                                                        <span className="text-muted-foreground">Demand pressure</span>
                                                        <span className="font-mono font-medium">
                                                            +{area.pressure} over idle drivers
                                                        </span>
                                                    </div>
                                                )}
                                                <div className="mt-1">
                                                    <div className="flex h-2 w-full overflow-hidden rounded-full bg-muted">
                                                        <div
                                                            className="h-full bg-success transition-all"
                                                            style={{ width: `${bar.supplyPct}%` }}
                                                        />
                                                        <div
                                                            className="h-full bg-destructive transition-all"
                                                            style={{ width: `${bar.gapPct}%` }}
                                                        />
                                                    </div>
                                                    <div className="mt-1 flex justify-between text-[11px] text-muted-foreground">
                                                        <span>Idle drivers</span>
                                                        <span>Demand above supply</span>
                                                    </div>
                                                </div>
                                            </div>
                                        )}
                                        {/* A ratio tells an operator an area is
                                            under pressure but not where in it, or
                                            which drivers are idle nearby — the two
                                            things any response needs. Deep-links
                                            with the area preselected and the demand
                                            overlay already on. */}
                                        {canOpenMonitoring && (
                                            <Link
                                                href={`/dashboard/monitoring?area=${encodeURIComponent(area.area_id)}&demand=1`}
                                                className="mt-3 inline-flex items-center gap-1.5 text-xs font-medium text-primary hover:underline"
                                            >
                                                <MapPin className="h-3.5 w-3.5" aria-hidden="true" />
                                                View {area.name} on the live map
                                            </Link>
                                        )}
                                    </CardContent>
                                </Card>
                            );
                        })}
                    </div>
                ))}

                {/* Demand forecast preview */}
                {showDemand && forecast.length > 0 && (
                    <Card>
                        <CardHeader>
                            <CardTitle className="text-lg">
                                6-Hour Demand Forecast
                                <span className="ml-2 text-sm font-normal text-muted-foreground">
                                    {serviceAreaId === "all"
                                        ? "— all areas"
                                        : `— ${serviceAreas.find((a) => a.id === serviceAreaId)?.name ?? "selected area"}`}
                                </span>
                            </CardTitle>
                        </CardHeader>
                        <CardContent>
                            <div className="flex items-end gap-2 h-32">
                                {forecast.slice(0, 12).map((slot) => (
                                    <div key={slot.hour} className="flex flex-1 flex-col items-center gap-1">
                                        <div
                                            className={`w-full rounded-t transition-all ${slot.isPeak ? "bg-orange-600" : "bg-orange-500/70"}`}
                                            style={{ height: `${forecastBarHeightPct(slot, forecast.slice(0, 12))}%` }}
                                        />
                                        {/* The value lives in real text, not only a
                                            hover tooltip, so keyboard and screen-reader
                                            users get the chart's actual content. */}
                                        <span className="sr-only">
                                            {slot.label}: {slot.predictedRides} predicted rides
                                            {slot.isPeak ? " (peak)" : ""}
                                        </span>
                                        <span aria-hidden="true" className="text-[11px] text-muted-foreground">
                                            {slot.label}
                                        </span>
                                    </div>
                                ))}
                            </div>
                            <p className="mt-2 text-xs text-muted-foreground text-center">
                                Predicted rides per hour over the next 6 hours
                            </p>
                        </CardContent>
                    </Card>
                )}
            </div>
        </div>
    );
}
