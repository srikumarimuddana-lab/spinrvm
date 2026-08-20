"use client";

// Standalone Demand Forecast page. The body lives in DemandForecastPanel,
// which the Analytics page's "Demand Forecast" tab also renders — this page
// owns only its own filter bar so the two views cannot drift apart.

import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import { TrendingUp, RefreshCw } from "lucide-react";
import { getServiceAreas } from "@/lib/api";
import { DemandForecastPanel } from "@/components/analytics/demand-forecast-panel";

export default function ForecastPage() {
  const [areaId, setAreaId] = useState<string>("all");
  const [areas, setAreas] = useState<any[]>([]);
  const [refreshToken, setRefreshToken] = useState(0);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getServiceAreas().then((a) => setAreas(Array.isArray(a) ? a : [])).catch(() => setAreas([]));
  }, []);

  return (
    <div className="space-y-6">
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
            <SelectTrigger className="w-44" aria-label="All Areas">
              <SelectValue placeholder="All Areas" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All Areas</SelectItem>
              {areas.filter((a: any) => a.is_active && !a.parent_service_area_id).map((a: any) => (
                <SelectItem key={a.id} value={a.id}>{a.name}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button variant="outline" size="sm" onClick={() => setRefreshToken((t) => t + 1)} disabled={loading}>
            <RefreshCw className={`h-4 w-4 mr-1 ${loading ? "animate-spin" : ""}`} />
            Refresh
          </Button>
        </div>
      </div>

      <DemandForecastPanel
        serviceAreaId={areaId === "all" ? undefined : areaId}
        refreshToken={refreshToken}
        onLoadingChange={setLoading}
      />
    </div>
  );
}
