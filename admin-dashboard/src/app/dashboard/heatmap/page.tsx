"use client";

import { useEffect, useState, useCallback } from "react";
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
import { Loader2, RefreshCw, Users, Car, Building2, AlertTriangle, TrendingUp, TrendingDown } from "lucide-react";

interface AreaDemand {
    area_id: string;
    name: string;
    demand_count: number;
    supply_count: number;
    ratio: number;
    multiplier: number;
    surge_active: boolean;
    source: string;
    gap: number;
}

interface ForecastSlot {
    hour: string;
    predicted_demand: number;
    label?: string;
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

    // Unmet demand state
    const [demandAreas, setDemandAreas] = useState<AreaDemand[]>([]);
    const [demandLoading, setDemandLoading] = useState(false);
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

    // Fetch unmet demand data
    const fetchDemandData = useCallback(() => {
        setDemandLoading(true);
        const areaIdParam = serviceAreaId === "all" ? undefined : serviceAreaId;
        Promise.all([
            getSurgeStatus(),
            getDemandForecast(6, areaIdParam),
        ])
            .then(([surgeRes, forecastRes]) => {
                const areas: AreaDemand[] = (surgeRes || []).map((a: any) => ({
                    area_id: a.area_id,
                    name: a.name,
                    demand_count: a.demand_count ?? 0,
                    supply_count: a.supply_count ?? 0,
                    ratio: a.ratio ?? 0,
                    multiplier: a.multiplier ?? 1.0,
                    surge_active: a.surge_active ?? false,
                    source: a.source ?? "auto",
                    gap: Math.max(0, (a.demand_count ?? 0) - (a.supply_count ?? 0)),
                }));
                areas.sort((a, b) => b.ratio - a.ratio);
                setDemandAreas(areas);
                const slots: ForecastSlot[] = (forecastRes?.forecast || forecastRes?.slots || []).map((s: any) => ({
                    hour: s.hour ?? s.time ?? "",
                    predicted_demand: s.predicted_demand ?? s.demand ?? 0,
                    label: s.label,
                }));
                setForecast(slots);
            })
            .catch(console.error)
            .finally(() => setDemandLoading(false));
    }, [serviceAreaId]);

    useEffect(() => {
        fetchDemandData();
        const interval = setInterval(fetchDemandData, 120_000);
        return () => clearInterval(interval);
    }, [fetchDemandData]);

    // Convert API data to HeatMap component format
    const pickupPoints = heatMapData?.pickup_points?.map((p) => ({
        lat: p[0],
        lng: p[1],
        intensity: p[2],
    })) || [];

    const dropoffPoints = heatMapData?.dropoff_points?.map((p) => ({
        lat: p[0],
        lng: p[1],
        intensity: p[2],
    })) || [];

    const stats = heatMapData?.stats || { total_rides: 0, corporate_rides: 0, regular_rides: 0 };

    return (
        <div className="space-y-6">
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-3xl font-bold tracking-tight">Heat Map</h1>
                    <p className="text-muted-foreground mt-1">
                        View ride density patterns across the platform.
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
                            settings={{
                                radius: settings?.heat_map_radius || 25,
                                blur: settings?.heat_map_blur || 15,
                            }}
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
                        <h2 className="text-xl font-semibold tracking-tight">Unmet Demand</h2>
                        <p className="text-sm text-muted-foreground">
                            Live demand/supply gaps across service areas. Updates every 2 minutes.
                        </p>
                    </div>
                    <Button
                        variant="outline"
                        size="sm"
                        onClick={fetchDemandData}
                        disabled={demandLoading}
                    >
                        <RefreshCw className={`mr-2 h-4 w-4 ${demandLoading ? "animate-spin" : ""}`} />
                        Refresh
                    </Button>
                </div>

                {/* Summary stats */}
                {demandAreas.length > 0 && (
                    <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
                        <Card>
                            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                                <CardTitle className="text-sm font-medium">Total Demand</CardTitle>
                                <TrendingUp className="h-4 w-4 text-orange-500" />
                            </CardHeader>
                            <CardContent>
                                <div className="text-2xl font-bold">
                                    {demandAreas.reduce((s, a) => s + a.demand_count, 0)}
                                </div>
                                <p className="text-xs text-muted-foreground">active ride requests</p>
                            </CardContent>
                        </Card>
                        <Card>
                            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                                <CardTitle className="text-sm font-medium">Total Supply</CardTitle>
                                <Car className="h-4 w-4 text-green-500" />
                            </CardHeader>
                            <CardContent>
                                <div className="text-2xl font-bold">
                                    {demandAreas.reduce((s, a) => s + a.supply_count, 0)}
                                </div>
                                <p className="text-xs text-muted-foreground">available drivers</p>
                            </CardContent>
                        </Card>
                        <Card>
                            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                                <CardTitle className="text-sm font-medium">Unmet Gap</CardTitle>
                                <AlertTriangle className="h-4 w-4 text-destructive" />
                            </CardHeader>
                            <CardContent>
                                <div className="text-2xl font-bold text-destructive">
                                    {demandAreas.reduce((s, a) => s + a.gap, 0)}
                                </div>
                                <p className="text-xs text-muted-foreground">unfulfilled requests</p>
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

                {/* Per-area demand cards */}
                {demandLoading && demandAreas.length === 0 ? (
                    <div className="flex items-center justify-center py-8">
                        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
                    </div>
                ) : demandAreas.length === 0 ? (
                    <Card>
                        <CardContent className="py-8 text-center text-sm text-muted-foreground">
                            No demand data available. Surge engine may not be running.
                        </CardContent>
                    </Card>
                ) : (
                    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                        {demandAreas.map((area) => (
                            <Card key={area.area_id} className={
                                area.ratio >= 3.0
                                    ? "border-destructive/50 bg-destructive/5"
                                    : area.ratio >= 2.0
                                    ? "border-orange-500/50 bg-orange-500/5"
                                    : area.ratio >= 1.2
                                    ? "border-amber-500/50 bg-amber-500/5"
                                    : ""
                            }>
                                <CardHeader className="pb-2">
                                    <div className="flex items-center justify-between">
                                        <CardTitle className="text-sm font-medium">{area.name}</CardTitle>
                                        <div className="flex items-center gap-1.5">
                                            {area.surge_active && (
                                                <Badge variant="outline" className="text-xs bg-amber-500/10 text-amber-600 border-amber-500/30">
                                                    {area.multiplier.toFixed(2)}x surge
                                                </Badge>
                                            )}
                                            {area.source === "manual" && (
                                                <Badge variant="outline" className="text-xs">Manual</Badge>
                                            )}
                                        </div>
                                    </div>
                                </CardHeader>
                                <CardContent>
                                    <div className="space-y-2">
                                        <div className="flex items-center justify-between text-sm">
                                            <span className="text-muted-foreground">Demand / Supply</span>
                                            <span className="font-mono font-medium">
                                                {area.demand_count} / {area.supply_count}
                                            </span>
                                        </div>
                                        <div className="flex items-center justify-between text-sm">
                                            <span className="text-muted-foreground">Ratio</span>
                                            <span className={`font-mono font-medium ${
                                                area.ratio >= 3.0 ? "text-destructive" :
                                                area.ratio >= 2.0 ? "text-orange-600" :
                                                area.ratio >= 1.2 ? "text-amber-600" :
                                                area.ratio >= 0.5 ? "text-green-600" :
                                                "text-purple-600"
                                            }`}>
                                                {area.ratio.toFixed(2)}
                                            </span>
                                        </div>
                                        {area.gap > 0 && (
                                            <div className="flex items-center justify-between text-sm">
                                                <span className="text-muted-foreground">Unmet gap</span>
                                                <span className="font-mono font-medium text-destructive">
                                                    {area.gap} requests
                                                </span>
                                            </div>
                                        )}
                                        {/* Demand bar */}
                                        <div className="mt-1">
                                            <div className="flex h-2 w-full overflow-hidden rounded-full bg-muted">
                                                <div
                                                    className="h-full bg-green-500 transition-all"
                                                    style={{ width: `${Math.min(100, area.supply_count > 0 ? (area.supply_count / Math.max(area.demand_count, area.supply_count)) * 100 : (area.demand_count === 0 ? 100 : 0))}%` }}
                                                />
                                                <div
                                                    className="h-full bg-destructive transition-all"
                                                    style={{ width: `${Math.min(100, area.gap > 0 ? (area.gap / Math.max(area.demand_count, area.supply_count)) * 100 : 0)}%` }}
                                                />
                                            </div>
                                            <div className="mt-1 flex justify-between text-[10px] text-muted-foreground">
                                                <span>Supply (green)</span>
                                                <span>Unmet (red)</span>
                                            </div>
                                        </div>
                                    </div>
                                </CardContent>
                            </Card>
                        ))}
                    </div>
                )}

                {/* Demand forecast preview */}
                {forecast.length > 0 && (
                    <Card>
                        <CardHeader>
                            <CardTitle className="text-lg">6-Hour Demand Forecast</CardTitle>
                        </CardHeader>
                        <CardContent>
                            <div className="flex items-end gap-2 h-32">
                                {forecast.slice(0, 12).map((slot, i) => {
                                    const max = Math.max(...forecast.slice(0, 12).map(s => s.predicted_demand), 1);
                                    const pct = (slot.predicted_demand / max) * 100;
                                    return (
                                        <div key={i} className="flex flex-1 flex-col items-center gap-1">
                                            <div
                                                className="w-full rounded-t bg-orange-500/80 transition-all"
                                                style={{ height: `${Math.max(4, pct)}%` }}
                                                title={`${slot.hour}: ${slot.predicted_demand} predicted`}
                                            />
                                            <span className="text-[9px] text-muted-foreground truncate w-full text-center">
                                                {slot.label || slot.hour}
                                            </span>
                                        </div>
                                    );
                                })}
                            </div>
                            <p className="mt-2 text-xs text-muted-foreground text-center">
                                Predicted ride demand by hour (next 6 hours)
                            </p>
                        </CardContent>
                    </Card>
                )}
            </div>
        </div>
    );
}
