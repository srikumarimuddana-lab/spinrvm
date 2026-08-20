"use client";

// Standalone Driver Offers page. The body lives in DriverOffersPanel, which
// the Analytics page's "Dispatch Offers" tab also renders — this page owns
// only its own filter bar so the two views cannot drift apart.

import { useEffect, useState } from "react";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import { RefreshCw, MapPin } from "lucide-react";
import { getServiceAreas } from "@/lib/api";
import { DriverOffersPanel } from "@/components/analytics/driver-offers-panel";

const DATE_RANGES = [
  { value: "today", label: "Today" },
  { value: "7d", label: "7 Days" },
  { value: "30d", label: "30 Days" },
  { value: "90d", label: "90 Days" },
  { value: "1y", label: "1 Year" },
];

const ALL_AREAS = "__all__";

export default function DriverOffersPage() {
  const [dateRange, setDateRange] = useState("30d");
  const [areaId, setAreaId] = useState<string>(ALL_AREAS);
  const [areas, setAreas] = useState<any[]>([]);
  const [refreshToken, setRefreshToken] = useState(0);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getServiceAreas().then((a) => setAreas(Array.isArray(a) ? a : [])).catch(() => setAreas([]));
  }, []);

  const svcArea = areaId === ALL_AREAS ? undefined : areaId;

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-bold">Driver Offer Analytics</h1>
          <p className="text-sm text-muted-foreground">
            Dispatch funnel per driver — who accepts, declines, ignores, or is preempted.
          </p>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <Select value={areaId} onValueChange={setAreaId}>
            <SelectTrigger className="w-44" aria-label="Filter by service area">
              <span className="flex items-center gap-1.5 truncate"><MapPin className="h-3.5 w-3.5 shrink-0" /><SelectValue /></span>
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL_AREAS}>All service areas</SelectItem>
              {areas.map((a) => (
                <SelectItem key={a.id} value={a.id}>{a.name || a.id}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select value={dateRange} onValueChange={setDateRange}>
            <SelectTrigger className="w-32" aria-label="Filter by date range"><SelectValue /></SelectTrigger>
            <SelectContent>
              {DATE_RANGES.map((r) => (
                <SelectItem key={r.value} value={r.value}>{r.label}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          <button
            onClick={() => setRefreshToken((t) => t + 1)}
            className="flex items-center gap-1.5 text-sm border rounded-lg px-3 py-2 hover:bg-muted transition-colors"
          >
            <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} /> Refresh
          </button>
        </div>
      </div>

      <DriverOffersPanel
        dateRange={dateRange}
        serviceAreaId={svcArea}
        refreshToken={refreshToken}
        onLoadingChange={setLoading}
      />
    </div>
  );
}
