"use client";

import { useEffect, useState, useCallback } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";
import {
  Radio, RefreshCw, Car, MapPin, Users, Clock, Activity, Search,
  Navigation, CheckCircle,
} from "lucide-react";
import { getActiveRides } from "@/lib/api";

const STATUS_CONFIG: Record<string, { label: string; color: string; bg: string }> = {
  searching: { label: "Searching", color: "text-amber-600", bg: "bg-amber-100" },
  driver_assigned: { label: "Assigned", color: "text-blue-600", bg: "bg-blue-100" },
  driver_accepted: { label: "Accepted", color: "text-blue-600", bg: "bg-blue-100" },
  driver_arrived: { label: "Arrived", color: "text-green-600", bg: "bg-green-100" },
  in_progress: { label: "In Progress", color: "text-purple-600", bg: "bg-purple-100" },
};

export default function MonitoringPage() {
  const [rides, setRides] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);

  const fetchRides = useCallback(async () => {
    try {
      const data = await getActiveRides();
      setRides(data?.rides || []);
      setLastUpdated(new Date());
    } catch {
      console.error("Failed to fetch active rides");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchRides();
    if (!autoRefresh) return;
    const interval = setInterval(fetchRides, 10000);
    return () => clearInterval(interval);
  }, [fetchRides, autoRefresh]);

  // Stats
  const searching = rides.filter(r => r.status === "searching").length;
  const assigned = rides.filter(r => r.status === "driver_assigned" || r.status === "driver_accepted").length;
  const arrived = rides.filter(r => r.status === "driver_arrived").length;
  const inProgress = rides.filter(r => r.status === "in_progress").length;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <Radio className="h-6 w-6 text-red-500" />
            Live Ride Monitoring
            <span className="relative flex h-3 w-3 ml-1">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-red-400 opacity-75" />
              <span className="relative inline-flex rounded-full h-3 w-3 bg-red-500" />
            </span>
          </h1>
          <p className="text-muted-foreground mt-1">
            Real-time view of all active rides
            {lastUpdated && (
              <span className="ml-2 text-xs">
                · Updated {lastUpdated.toLocaleTimeString()}
              </span>
            )}
          </p>
        </div>
        <div className="flex items-center gap-3">
          <label className="flex items-center gap-2 text-sm cursor-pointer">
            <input
              type="checkbox"
              checked={autoRefresh}
              onChange={e => setAutoRefresh(e.target.checked)}
              className="rounded"
            />
            Auto-refresh (10s)
          </label>
          <Button variant="outline" size="sm" onClick={fetchRides} disabled={loading}>
            <RefreshCw className={`h-4 w-4 mr-1 ${loading ? "animate-spin" : ""}`} />
            Refresh
          </Button>
        </div>
      </div>

      {/* Live Stats */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
        <Card>
          <CardContent className="pt-4">
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <Activity className="h-4 w-4" /> Total Active
            </div>
            <div className="text-3xl font-bold">{rides.length}</div>
          </CardContent>
        </Card>
        <Card className="border-amber-200/50">
          <CardContent className="pt-4">
            <div className="flex items-center gap-2 text-sm text-amber-600">
              <Search className="h-4 w-4" /> Searching
            </div>
            <div className="text-3xl font-bold text-amber-600">{searching}</div>
          </CardContent>
        </Card>
        <Card className="border-blue-200/50">
          <CardContent className="pt-4">
            <div className="flex items-center gap-2 text-sm text-blue-600">
              <Car className="h-4 w-4" /> Assigned
            </div>
            <div className="text-3xl font-bold text-blue-600">{assigned}</div>
          </CardContent>
        </Card>
        <Card className="border-green-200/50">
          <CardContent className="pt-4">
            <div className="flex items-center gap-2 text-sm text-green-600">
              <MapPin className="h-4 w-4" /> Arrived
            </div>
            <div className="text-3xl font-bold text-green-600">{arrived}</div>
          </CardContent>
        </Card>
        <Card className="border-purple-200/50">
          <CardContent className="pt-4">
            <div className="flex items-center gap-2 text-sm text-purple-600">
              <Navigation className="h-4 w-4" /> In Progress
            </div>
            <div className="text-3xl font-bold text-purple-600">{inProgress}</div>
          </CardContent>
        </Card>
      </div>

      {/* Map Placeholder + Active Rides Table */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Map Area */}
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <MapPin className="h-5 w-5" /> Live Map
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="bg-blue-50 dark:bg-blue-950/20 rounded-xl h-[400px] flex items-center justify-center relative overflow-hidden">
              {rides.length === 0 ? (
                <div className="text-center z-10">
                  <CheckCircle className="h-12 w-12 text-green-400 mx-auto mb-3" />
                  <p className="text-lg font-semibold text-muted-foreground">All clear</p>
                  <p className="text-sm text-muted-foreground">No active rides right now</p>
                </div>
              ) : (
                <div className="text-center z-10 space-y-3">
                  <div className="flex items-center justify-center gap-2">
                    <span className="text-4xl">🗺️</span>
                  </div>
                  <p className="text-lg font-semibold">{rides.length} Active Rides</p>
                  <div className="flex flex-wrap justify-center gap-2 max-w-md">
                    {rides.filter(r => r.driver_lat && r.driver_lng).slice(0, 8).map((r, i) => (
                      <div key={r.id} className="bg-white/80 dark:bg-zinc-900/80 rounded-lg px-2 py-1 text-xs shadow-sm">
                        <span className="font-mono">{r.driver_lat?.toFixed(3)}, {r.driver_lng?.toFixed(3)}</span>
                        <Badge className={`ml-1 text-[10px] ${STATUS_CONFIG[r.status]?.bg || "bg-gray-100"} ${STATUS_CONFIG[r.status]?.color || ""}`}>
                          {STATUS_CONFIG[r.status]?.label || r.status}
                        </Badge>
                      </div>
                    ))}
                    {rides.filter(r => r.driver_lat).length > 8 && (
                      <p className="text-xs text-muted-foreground">+{rides.filter(r => r.driver_lat).length - 8} more</p>
                    )}
                  </div>
                  <p className="text-xs text-muted-foreground">
                    Integrate Leaflet/Mapbox for full map visualization
                  </p>
                </div>
              )}
            </div>
          </CardContent>
        </Card>

        {/* Ride List */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <Activity className="h-4 w-4" /> Active Rides
            </CardTitle>
          </CardHeader>
          <CardContent className="p-0 max-h-[440px] overflow-y-auto">
            {rides.length === 0 ? (
              <p className="text-center py-8 text-muted-foreground text-sm">No active rides</p>
            ) : (
              <div className="divide-y">
                {rides.map(r => (
                  <div key={r.id} className="px-4 py-3 hover:bg-muted/50 transition">
                    <div className="flex items-center justify-between mb-1">
                      <Badge className={`text-[10px] ${STATUS_CONFIG[r.status]?.bg || "bg-gray-100"} ${STATUS_CONFIG[r.status]?.color || ""}`}>
                        {STATUS_CONFIG[r.status]?.label || r.status}
                      </Badge>
                      <span className="text-xs text-muted-foreground font-mono">
                        ${Number(r.total_fare || 0).toFixed(2)}
                      </span>
                    </div>
                    <p className="text-xs truncate">{r.pickup_address}</p>
                    <p className="text-xs text-muted-foreground truncate">→ {r.dropoff_address}</p>
                    <div className="flex items-center gap-2 mt-1 text-[11px] text-muted-foreground">
                      <span className="flex items-center gap-0.5">
                        <Users className="h-3 w-3" /> {r.rider_name || "—"}
                      </span>
                      {r.driver_name && (
                        <span className="flex items-center gap-0.5">
                          <Car className="h-3 w-3" /> {r.driver_name}
                        </span>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
