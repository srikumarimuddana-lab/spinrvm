"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Radio, X } from "lucide-react";
import { useAuthStore } from "@/store/authStore";
import { useToast } from "@/components/ui/use-toast";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

import { adminCancelRide, getFareConfigs, getMonitoringDrivers, getMonitoringRides, getServiceAreas, getVehicleTypes, getSurgeStatus } from "@/lib/api";
import { useMonitoringSocket } from "@/hooks/use-monitoring-socket";

import { MonitoringMap, MapHandles, MonitoringServiceArea } from "./monitoring-map";
import { MonitoringToolbar } from "./toolbar";
import { DriverPanel } from "./driver-panel";
import { RidePanel } from "./ride-panel";
import { AlertFeed } from "./alert-feed";
import { useRequireModule } from "@/hooks/useRequireModule";
import type {
  AlertEvent,
  AreaDemandSupply,
  MonitoringCounts,
  MonitoringDriver,
  MonitoringFilters,
  MonitoringRide,
  MonitoringWsEvent,
  SelectedItem,
} from "./types";

// With a healthy WebSocket the REST poll is only a drift-reconciliation
// sweep — snapshots + incremental events carry the live state — so 5 min
// keeps Supabase egress down. The degraded interval is the real fallback
// while the socket reconnects.
const POLL_INTERVAL_MS = 300_000;
const DEGRADED_POLL_INTERVAL_MS = 10_000;

export default function MonitoringPage() {
  const { allowed } = useRequireModule("rides");
  const { toast } = useToast();
  // ── Refs: source-of-truth maps (never trigger re-renders) ──────────
  const driversMapRef = useRef<Map<string, MonitoringDriver>>(new Map());
  const ridesMapRef = useRef<Map<string, MonitoringRide>>(new Map());
  const mapHandlesRef = useRef<MapHandles | null>(null);

  // ── State ───────────────────────────────────────────────────────────
  const [counts, setCounts] = useState<MonitoringCounts>({
    online: 0, onRide: 0, offline: 0, activeRides: 0,
  });
  const [filters, setFilters] = useState<MonitoringFilters>({
    showOnline: true,
    showOffline: false,
    showRides: true,
    showDemand: false,
    serviceAreaId: null,
    vehicleTypeId: null,
  });
  const [searchQuery, setSearchQuery] = useState("");
  const [followMode, setFollowMode] = useState(false);
  const [selected, setSelected] = useState<SelectedItem>(null);
  const [selectedDriver, setSelectedDriver] = useState<MonitoringDriver | null>(null);
  const [selectedRide, setSelectedRide] = useState<MonitoringRide | null>(null);
  const [serviceAreas, setServiceAreas] = useState<MonitoringServiceArea[]>([]);
  const [vehicleTypes, setVehicleTypes] = useState<{ id: string; name: string }[]>([]);
  // serviceAreaId → set of vehicle_type_ids with an active fare config.
  // Used to narrow the toolbar's vehicle-type dropdown when an area is
  // selected (e.g. picking "Regina" hides vehicle types that have no
  // Regina fare config). An empty/missing set means the area has no
  // configured vehicle types; the dropdown shows "No vehicles configured".
  const [vehicleTypesByArea, setVehicleTypesByArea] = useState<Record<string, Set<string>>>({});
  const [alerts, setAlerts] = useState<AlertEvent[]>([]);
  const [cancelDialogOpen, setCancelDialogOpen] = useState(false);
  const [cancelReason, setCancelReason] = useState("Cancelled by admin");
  const [pendingCancelId, setPendingCancelId] = useState<string | null>(null);
  const [demandData, setDemandData] = useState<AreaDemandSupply[]>([]);
  const [demandError, setDemandError] = useState<string | null>(null);
  const [demandFetchedAt, setDemandFetchedAt] = useState<Date | null>(null);

  // ── Auth token for WebSocket ────────────────────────────────────────
  const token = useAuthStore((s) => s.token);

  // ── Recompute counts from ref maps ─────────────────────────────────
  const refreshCounts = useCallback(() => {
    const drivers = Array.from(driversMapRef.current.values());
    const rides = Array.from(ridesMapRef.current.values());
    setCounts({
      online: drivers.filter((d) => d.is_online && !d.active_ride_id).length,
      onRide: drivers.filter((d) => d.is_online && !!d.active_ride_id).length,
      offline: drivers.filter((d) => !d.is_online).length,
      activeRides: rides.length,
    });
  }, []);

  // ── Apply a driver update to the ref map + map marker ───────────────
  const applyDriver = useCallback((d: MonitoringDriver) => {
    // Apply service area filter
    if (filters.serviceAreaId && d.service_area_id !== filters.serviceAreaId) {
      mapHandlesRef.current?.removeDriverMarker(d.id);
      return;
    }
    // Apply vehicle type filter
    if (filters.vehicleTypeId && d.vehicle_type_id !== filters.vehicleTypeId) {
      mapHandlesRef.current?.removeDriverMarker(d.id);
      return;
    }
    driversMapRef.current.set(d.id, d);
    mapHandlesRef.current?.updateDriverMarker(d);
  }, [filters.serviceAreaId, filters.vehicleTypeId]);

  // ── Apply a ride update to the ref map + map markers ────────────────
  const applyRide = useCallback((r: MonitoringRide) => {
    ridesMapRef.current.set(r.id, r);
    mapHandlesRef.current?.updateRideMarkers(r);
  }, []);

  // ── Push an alert (max 50) ──────────────────────────────────────────
  const pushAlert = useCallback((alert: Omit<AlertEvent, "id" | "timestamp">) => {
    setAlerts((prev) => [
      { ...alert, id: Math.random().toString(36).slice(2), timestamp: new Date().toISOString() },
      ...prev.slice(0, 49),
    ]);
  }, []);

  // ── WebSocket event handler ─────────────────────────────────────────
  const handleWsEvent = useCallback((event: MonitoringWsEvent) => {
    switch (event.type) {
      case "driver_location_update": {
        const driver = driversMapRef.current.get(event.driver_id);
        if (driver) {
          const updated = { ...driver, lat: event.lat, lng: event.lng };
          driversMapRef.current.set(event.driver_id, updated);
          mapHandlesRef.current?.updateDriverMarker(updated);
          if (followMode && selected?.type === "driver" && selected.id === event.driver_id) {
            mapHandlesRef.current?.panTo(event.lat, event.lng);
          }
        }
        break;
      }
      case "ride_status_changed": {
        const ride = ridesMapRef.current.get(event.ride_id);
        if (ride) {
          const updated = { ...ride, status: event.status as MonitoringRide["status"] };
          ridesMapRef.current.set(event.ride_id, updated);
          mapHandlesRef.current?.updateRideMarkers(updated);
          if (selected?.type === "ride" && selected.id === event.ride_id) {
            setSelectedRide(updated);
          }
        }
        break;
      }
      case "driver_status_changed": {
        const driver = driversMapRef.current.get(event.driver_id);
        if (driver) {
          const updated = { ...driver, is_online: event.is_online };
          applyDriver(updated);
          refreshCounts();
          pushAlert({
            icon: event.is_online ? "online" : "offline",
            message: `${driver.name} went ${event.is_online ? "online" : "offline"}`,
            driver_id: event.driver_id,
          });
        }
        break;
      }
      case "ride_requested": {
        applyRide(event.ride);
        refreshCounts();
        pushAlert({
          icon: "ride_new",
          message: `New ride: ${event.ride.pickup_address ?? "—"} → ${event.ride.dropoff_address ?? "—"}`,
          ride_id: event.ride.id,
        });
        break;
      }
      case "ride_completed": {
        const _ride = ridesMapRef.current.get(event.ride_id);
        ridesMapRef.current.delete(event.ride_id);
        mapHandlesRef.current?.removeRideMarkers(event.ride_id);
        refreshCounts();
        pushAlert({
          icon: "ride_done",
          message: `Ride completed${event.fare ? ` · $${event.fare.toFixed(2)}` : ""}`,
          ride_id: event.ride_id,
        });
        if (selected?.type === "ride" && selected.id === event.ride_id) {
          setSelected(null);
          setSelectedRide(null);
        }
        break;
      }
      case "ride_cancelled": {
        ridesMapRef.current.delete(event.ride_id);
        mapHandlesRef.current?.removeRideMarkers(event.ride_id);
        refreshCounts();
        // A scheduled ride's cancellation is unconditionally terminal (no
        // auto-requeue, same as any ride) — but the rider planned around
        // this pickup time and has less slack to just re-hail, so flag it
        // distinctly in the feed rather than let it read the same as a
        // routine on-demand cancellation.
        pushAlert({
          icon: "ride_cancelled",
          message: event.is_scheduled ? "⚠ Scheduled ride cancelled — rider needs follow-up" : "Ride cancelled",
          ride_id: event.ride_id,
        });
        if (selected?.type === "ride" && selected.id === event.ride_id) {
          setSelected(null);
          setSelectedRide(null);
        }
        break;
      }
      case "drivers_snapshot": {
        const incomingIds = new Set<string>();
        for (const d of event.drivers) {
          incomingIds.add(d.id);
          applyDriver(d);
        }
        for (const id of driversMapRef.current.keys()) {
          if (!incomingIds.has(id)) {
            mapHandlesRef.current?.removeDriverMarker(id);
            driversMapRef.current.delete(id);
          }
        }
        refreshCounts();
        break;
      }
      case "rides_snapshot": {
        const incomingIds = new Set<string>();
        for (const r of event.rides) {
          incomingIds.add(r.id);
          applyRide(r);
        }
        for (const id of ridesMapRef.current.keys()) {
          if (!incomingIds.has(id)) {
            mapHandlesRef.current?.removeRideMarkers(id);
            ridesMapRef.current.delete(id);
          }
        }
        refreshCounts();
        break;
      }
    }
  }, [applyDriver, applyRide, followMode, pushAlert, refreshCounts, selected]);

  const {
    status: wsStatus,
    requestSnapshots,
    lastError: wsError,
  } = useMonitoringSocket({ token, onEvent: handleWsEvent });

  // ── Initial data load + polling ─────────────────────────────────────
  const loadData = useCallback(async () => {
    const [rawDriversResult, rawRidesResult] = await Promise.all([
      getMonitoringDrivers().catch(() => [] as any[]),
      getMonitoringRides().catch(() => [] as any[]),
    ]);
    const rawDrivers = Array.isArray(rawDriversResult) ? rawDriversResult : [];
    const rawRides = Array.isArray(rawRidesResult) ? rawRidesResult : [];

    // Sync driver markers
    const incomingDriverIds = new Set<string>();
    for (const d of rawDrivers as MonitoringDriver[]) {
      incomingDriverIds.add(d.id);
      applyDriver(d);
    }
    // Remove stale driver markers
    for (const id of driversMapRef.current.keys()) {
      if (!incomingDriverIds.has(id)) {
        mapHandlesRef.current?.removeDriverMarker(id);
        driversMapRef.current.delete(id);
      }
    }

    // Sync ride markers
    const incomingRideIds = new Set<string>();
    for (const r of rawRides as MonitoringRide[]) {
      incomingRideIds.add(r.id);
      applyRide(r);
    }
    for (const id of ridesMapRef.current.keys()) {
      if (!incomingRideIds.has(id)) {
        mapHandlesRef.current?.removeRideMarkers(id);
        ridesMapRef.current.delete(id);
      }
    }

    refreshCounts();
  }, [applyDriver, applyRide, refreshCounts]);

  // Load service areas + vehicle types + pricing sources once. The
  // serviceAreaId → vehicleTypeId map is unioned from BOTH pricing
  // stores (fare_configs table AND service_areas.vehicle_pricing
  // JSONB) — admins can configure vehicles in either place, and
  // reading only one source caused the drawer to complain "No fare
  // configs" even when pricing was set via the Service Areas admin.
  useEffect(() => {
    Promise.all([
      getServiceAreas(),
      getVehicleTypes().catch(() => [] as any[]),
      getFareConfigs().catch(() => [] as any[]),
    ])
      .then(([rawAreas, vt, configs]) => {
        const areas: any[] = rawAreas || [];

        setServiceAreas(
          areas.map((a) => {
            // Backend stores the shape under `polygon` (may be a raw
            // GeoJSON Polygon or an array of {lat,lng} — see
            // getAreaPolygon() in the service-areas admin page).
            // Normalise to a GeoJSON Polygon for fitBoundsToGeoJSON(), and
            // compute a centroid fallback so fitArea() can still centre
            // the map when the area has no polygon drawn yet.
            const raw = a.polygon ?? a.geojson;
            let geojson: GeoJSON.Polygon | null = null;
            let fallbackCenter: { lat: number; lng: number } | null = null;
            if (raw?.type === "Polygon" && Array.isArray(raw.coordinates)) {
              geojson = raw as GeoJSON.Polygon;
            } else if (Array.isArray(raw) && raw.length > 0 && raw[0]?.lat !== undefined) {
              const ring = raw.map(
                (p: { lat: number; lng: number }) => [p.lng, p.lat] as GeoJSON.Position,
              );
              const first = ring[0];
              const last = ring[ring.length - 1];
              if (first[0] !== last[0] || first[1] !== last[1]) ring.push(first);
              geojson = { type: "Polygon", coordinates: [ring] };
            }
            if (geojson) {
              const ring = geojson.coordinates[0];
              const pts =
                ring.length > 1 &&
                ring[0][0] === ring[ring.length - 1][0] &&
                ring[0][1] === ring[ring.length - 1][1]
                  ? ring.slice(0, -1)
                  : ring;
              const sum = pts.reduce(
                (acc, [lng, lat]) => ({ lng: acc.lng + lng, lat: acc.lat + lat }),
                { lng: 0, lat: 0 },
              );
              fallbackCenter = { lng: sum.lng / pts.length, lat: sum.lat / pts.length };
            } else if (a.center_lat && a.center_lng) {
              fallbackCenter = { lat: a.center_lat, lng: a.center_lng };
            }
            return { id: a.id, name: a.name, geojson, fallbackCenter };
          }),
        );

        const types = (vt || []).map((v: any) => ({ id: v.id, name: v.name }));
        setVehicleTypes(types);
        const byName: Record<string, string> = {};
        for (const t of types) byName[t.name] = t.id;

        const map: Record<string, Set<string>> = {};
        // From fare_configs (direct id refs)
        for (const c of configs || []) {
          if (c?.is_active === false) continue;
          const aId = c?.service_area_id;
          const vtId = c?.vehicle_type_id;
          if (!aId || !vtId) continue;
          if (!map[aId]) map[aId] = new Set<string>();
          map[aId].add(vtId);
        }
        // From service_areas.vehicle_pricing JSONB (name-based)
        for (const area of areas) {
          const pricing = Array.isArray(area?.vehicle_pricing) ? area.vehicle_pricing : [];
          for (const row of pricing) {
            const name = row?.vehicle_type;
            if (!name) continue;
            const vtId = byName[name];
            if (!vtId) continue;
            if (!map[area.id]) map[area.id] = new Set<string>();
            map[area.id].add(vtId);
          }
        }
        setVehicleTypesByArea(map);
      })
      .catch(() => {});
  }, []);

  // Vehicle types available for the current area filter. When no area is
  // selected we show all; when an area is selected we narrow to the
  // vehicle types configured for it (empty allowed — the toolbar surfaces
  // an "empty" hint so the operator knows there's nothing to pick).
  const availableVehicleTypes = useMemo(() => {
    if (!filters.serviceAreaId) return vehicleTypes;
    const allowed = vehicleTypesByArea[filters.serviceAreaId];
    if (!allowed) return [];
    return vehicleTypes.filter((v) => allowed.has(v.id));
  }, [filters.serviceAreaId, vehicleTypes, vehicleTypesByArea]);

  // If the selected vehicle type is no longer valid for the chosen area,
  // auto-clear it so the filter doesn't silently hide every driver.
  useEffect(() => {
    if (!filters.vehicleTypeId) return;
    if (availableVehicleTypes.some((v) => v.id === filters.vehicleTypeId)) return;
    setFilters((f) => ({ ...f, vehicleTypeId: null }));
  }, [availableVehicleTypes, filters.vehicleTypeId]);

  // Deep-link support: the heatmap page's demand cards link here as
  // ?area=<id>&demand=1, so "Regina is at 2.1 demand:supply" leads straight to
  // *where in Regina* and *which drivers are idle there* — otherwise the
  // operator has to re-find the area in a dropdown and re-enable the overlay,
  // and the two screens read as unrelated.
  //
  // Read once on mount from window.location rather than useSearchParams(): the
  // latter needs a <Suspense> boundary this route lacks and fails `next build`
  // (same reasoning as the rides page). Mount-only so a later manual change to
  // the dropdown isn't stomped on the next render.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const params = new URLSearchParams(window.location.search);
    const areaId = params.get("area");
    const wantDemand = params.get("demand") === "1";
    if (!areaId && !wantDemand) return;
    setFilters((f) => ({
      ...f,
      ...(areaId ? { serviceAreaId: areaId } : {}),
      ...(wantDemand ? { showDemand: true } : {}),
    }));
  }, []);

  // When the operator picks a different service area (Saskatoon → Regina,
  // etc.), fly the map to that area so they actually see the city they
  // just selected. serviceAreas is a dep so the fit re-runs once the
  // list loads in — if the user somehow lands on the page with a
  // preselected area, we still centre on it. We deliberately don't move
  // the map when "All Areas" is chosen; the driver auto-fit handles that.
  useEffect(() => {
    if (!filters.serviceAreaId) return;
    const area = serviceAreas.find((a) => a.id === filters.serviceAreaId);
    if (!area) return;
    mapHandlesRef.current?.fitArea(filters.serviceAreaId);
  }, [filters.serviceAreaId, serviceAreas]);

  // Poll data. When the WebSocket is down, temporarily increase polling so
  // dispatch still sees near-live data while the socket reconnects. This is a
  // degraded fallback, not a replacement for the live stream.
  useEffect(() => {
    const intervalMs = wsStatus === "connected" ? POLL_INTERVAL_MS : DEGRADED_POLL_INTERVAL_MS;
    loadData();
    const interval = setInterval(loadData, intervalMs);
    return () => clearInterval(interval);
  }, [loadData, wsStatus]);

  // Poll demand/supply data when overlay is on (matches surge engine tick: 2 min)
  useEffect(() => {
    if (!filters.showDemand) return;
    let cancelled = false;
    const fetchDemand = () => {
      getSurgeStatus().then((res: any) => {
        if (cancelled) return;
        const areas: AreaDemandSupply[] = (res.areas || res || []).map((a: any) => ({
          area_id: a.area_id,
          name: a.name,
          demand_count: a.demand_count ?? 0,
          supply_count: a.supply_count ?? 0,
          ratio: a.ratio ?? 0,
          multiplier: a.multiplier ?? 1,
          surge_active: a.surge_active ?? false,
          surge_enabled: a.surge_enabled ?? true,
          source: a.source ?? "auto",
        }));
        setDemandData(areas);
        setDemandError(null);
        setDemandFetchedAt(new Date());
      }).catch((err) => {
        if (cancelled) return;
        // Never swallow this. Without a signal the map keeps painting the last
        // good colours indefinitely, and an operator reads a stale "balanced"
        // market as live — or pulls incentives from an area that is actually
        // starved because the surge engine stopped reporting.
        console.error("demand status poll failed", err);
        setDemandError(
          err?.status === 403
            ? "No Service Areas module access — demand overlay unavailable."
            : "Demand data failed to refresh."
        );
      });
    };
    fetchDemand();

    // Jittered, matching the heatmap page's demand poll. A fixed interval puts
    // every tab that enabled the overlay at the same moment — a shift handover,
    // or an incident everyone opens at once — onto the same instant, and each
    // poll hits the surge-status endpoint. ±10% spreads them without changing
    // the effective cadence.
    let timer: ReturnType<typeof setTimeout>;
    const scheduleNext = () => {
      const delay = 120_000 * (1 + (Math.random() * 0.2 - 0.1));
      timer = setTimeout(() => {
        if (cancelled) return;
        fetchDemand();
        scheduleNext();
      }, delay);
    };
    scheduleNext();

    return () => { cancelled = true; clearTimeout(timer); };
  }, [filters.showDemand]);

  // Re-apply filters when they change
  useEffect(() => {
    driversMapRef.current.forEach((d) => applyDriver(d));
  }, [applyDriver]);

  // Auto-select the first match when search query is unambiguous
  useEffect(() => {
    if (searchQuery.length < 3) return;
    const q = searchQuery.toLowerCase();
    // Check rides first (ID match is unambiguous)
    const rideMatch = Array.from(ridesMapRef.current.values()).find(
      (r) => r.id.includes(q) || r.rider_name?.toLowerCase().includes(q)
    );
    if (rideMatch) { handleSelectRide(rideMatch.id); return; }
    // Then check driver name
    const driverMatch = Array.from(driversMapRef.current.values()).find(
      (d) => d.name?.toLowerCase().includes(q)
    );
    if (driverMatch) handleSelectDriver(driverMatch.id);
  }, [searchQuery]); // intentionally omit handleSelect* — they're stable useCallbacks

  // ── Selection handlers ──────────────────────────────────────────────
  const handleSelectDriver = useCallback((id: string) => {
    const driver = driversMapRef.current.get(id);
    if (!driver) return;
    setSelected({ type: "driver", id });
    setSelectedDriver(driver);
    setSelectedRide(null);
    if (driver.lat && driver.lng) mapHandlesRef.current?.panTo(driver.lat, driver.lng);
  }, []);

  const handleSelectRide = useCallback((id: string) => {
    const ride = ridesMapRef.current.get(id);
    if (!ride) return;
    setSelected({ type: "ride", id });
    setSelectedRide(ride);
    setSelectedDriver(null);
    mapHandlesRef.current?.panTo(ride.pickup_lat, ride.pickup_lng);
  }, []);

  const handleAlertEventClick = useCallback((event: AlertEvent) => {
    if (event.driver_id) {
      const driver = driversMapRef.current.get(event.driver_id);
      if (driver) handleSelectDriver(event.driver_id);
    } else if (event.ride_id) {
      const ride = ridesMapRef.current.get(event.ride_id);
      if (ride) handleSelectRide(event.ride_id);
    }
  }, [handleSelectDriver, handleSelectRide]);

  const handleAreaFit = useCallback((areaId: string) => {
    mapHandlesRef.current?.fitArea(areaId);
  }, []);

  // Filter active rides list by service area.
  // The WS tick writes into ridesMapRef.current without re-rendering; we
  // recompute visibleRides whenever the `counts` state bumps, which is the
  // signal the map has new data. Reading ref.current here is intentional.
  const visibleRides = useMemo(() => {
    // eslint-disable-next-line react-hooks/refs
    const rides = Array.from(ridesMapRef.current.values());
    return rides.filter((r) => {
      if (filters.serviceAreaId) {
        // best-effort: rides don't carry service_area_id directly; skip filter
      }
      if (searchQuery) {
        const q = searchQuery.toLowerCase();
        return (
          r.rider_name?.toLowerCase().includes(q) ||
          r.driver_name?.toLowerCase().includes(q) ||
          r.id.includes(q)
        );
      }
      return true;
    });
  }, [filters.serviceAreaId, searchQuery, counts]); // counts triggers recompute on poll

  if (!allowed) return null;

  return (
    <div className="flex h-[calc(100vh-4rem)] flex-col overflow-hidden">
      {/* Page header */}
      <div className="flex items-center justify-between border-b border-border px-4 py-3">
        {/* eslint-disable no-restricted-syntax -- standard "live broadcast" red-dot
            convention (Radio icon + pulsing dot), not a success/warning/destructive
            signal (#2816) */}
        <h1 className="flex items-center gap-2 text-xl font-bold">
          <Radio className="h-5 w-5 text-red-500" />
          Live Ride Monitoring
          <span className="relative ml-1 flex h-2.5 w-2.5">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-red-400 opacity-75" />
            <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-red-500" />
          </span>
        </h1>
        {/* eslint-enable no-restricted-syntax */}
      </div>

      {/* Toolbar */}
      <MonitoringToolbar
        counts={counts}
        filters={filters}
        onFilterChange={(partial) => setFilters((f) => ({ ...f, ...partial }))}
        searchQuery={searchQuery}
        onSearchChange={setSearchQuery}
        followMode={followMode}
        onFollowToggle={() => setFollowMode((f) => !f)}
        serviceAreas={serviceAreas}
        vehicleTypes={availableVehicleTypes}
        wsStatus={wsStatus}
      />

      {/* Stale-data warning banner — visible whenever live feed is interrupted */}
      {wsStatus !== "connected" && (
        <div className="flex items-center gap-2 bg-warning/10 border-b border-warning/30 px-4 py-2 text-sm text-warning">
          <span className="font-medium">Live data paused</span>
          <span className="text-warning">
            — map and ride list may be stale ({wsStatus === "connecting" ? "reconnecting…" : wsError || "connection lost"})
          </span>
        </div>
      )}

      {/* Demand overlay staleness — a separate feed from the WebSocket above,
          so it needs its own banner: the colours on the map can be minutes old
          while the ride list is perfectly live. */}
      {filters.showDemand && demandError && (
        <div role="alert" className="flex items-center gap-2 border-b border-destructive/30 bg-destructive/5 px-4 py-2 text-sm text-destructive">
          <span className="font-medium">Demand overlay stale</span>
          <span>
            — {demandError}
            {demandFetchedAt
              ? ` Showing data from ${demandFetchedAt.toLocaleTimeString()}.`
              : " No demand data has loaded yet."}
          </span>
        </div>
      )}

      {/* Main 3-column layout */}
      <div className="flex flex-1 overflow-hidden">
        {/* ── Left: Ride list ─────────────────────────────────────── */}
        <div className="flex w-64 shrink-0 flex-col overflow-hidden border-r border-border">
          <div className="border-b border-border px-3 py-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            Active Rides ({visibleRides.length})
          </div>
          <div className="flex-1 overflow-y-auto">
            {visibleRides.length === 0 ? (
              <p className="py-8 text-center text-xs text-muted-foreground">No active rides</p>
            ) : (
              visibleRides.map((r) => (
                <button
                  key={r.id}
                  onClick={() => handleSelectRide(r.id)}
                  className={`w-full border-b border-border px-3 py-2.5 text-left transition hover:bg-muted/60 ${
                    selected?.type === "ride" && selected.id === r.id ? "bg-primary/5 ring-1 ring-inset ring-primary/30" : ""
                  }`}
                >
                  <div className="flex items-center justify-between">
                    <span
                      className={`inline-block rounded px-1.5 py-0.5 text-[10px] font-medium ${
                        // eslint-disable-next-line no-restricted-syntax -- ride lifecycle stages must stay visually distinct (#2816)
                        r.status === "searching" ? "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300" :
                        // eslint-disable-next-line no-restricted-syntax -- see above
                        r.status === "driver_assigned" ? "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300" :
                        // eslint-disable-next-line no-restricted-syntax -- see above
                        r.status === "driver_arrived" ? "bg-purple-100 text-purple-700 dark:bg-purple-900/30 dark:text-purple-300" :
                        "bg-success/15 text-success"
                      }`}
                    >
                      {r.status === "searching" ? "Searching" :
                       r.status === "driver_assigned" ? "Assigned" :
                       r.status === "driver_arrived" ? "Arrived" : "In Progress"}
                    </span>
                    {r.total_fare != null && (
                      <span className="text-[10px] text-muted-foreground">${r.total_fare.toFixed(2)}</span>
                    )}
                  </div>
                  <p className="mt-1 truncate text-xs">{r.pickup_address ?? "—"}</p>
                  <p className="truncate text-[11px] text-muted-foreground">→ {r.dropoff_address ?? "—"}</p>
                  <p className="mt-0.5 text-[11px] text-muted-foreground">
                    {r.rider_name}{r.driver_name ? ` · ${r.driver_name}` : ""}
                  </p>
                </button>
              ))
            )}
          </div>

          {/* Activity feed (clickable) */}
          <AlertFeed
            events={alerts}
            onClear={() => setAlerts([])}
            onEventClick={handleAlertEventClick}
          />
        </div>

        {/* ── Centre: Map ─────────────────────────────────────────── */}
        <div className="relative flex-1">
          <MonitoringMap
            driversMap={driversMapRef}
            ridesMap={ridesMapRef}
            filters={filters}
            searchQuery={searchQuery}
            selected={selected}
            followMode={followMode}
            serviceAreas={serviceAreas}
            demandData={filters.showDemand ? demandData : undefined}
            onSelectDriver={handleSelectDriver}
            onSelectRide={handleSelectRide}
            onReady={(handles) => {
              mapHandlesRef.current = handles;
              // Populate map once handles are available
              driversMapRef.current.forEach((d) => handles.updateDriverMarker(d));
              ridesMapRef.current.forEach((r) => handles.updateRideMarkers(r));
            }}
          />

          {/* Service-area jump buttons */}
          {serviceAreas.length > 0 && (
            <div className="absolute bottom-4 left-1/2 flex -translate-x-1/2 gap-2">
              {serviceAreas.filter((a) => a.geojson || a.fallbackCenter).slice(0, 5).map((a) => (
                <button
                  key={a.id}
                  onClick={() => handleAreaFit(a.id)}
                  className="rounded-full bg-white/90 px-3 py-1 text-xs font-medium shadow ring-1 ring-black/10 backdrop-blur hover:bg-white"
                >
                  {a.name}
                </button>
              ))}
            </div>
          )}
        </div>

        {/* ── Right: Detail panel ──────────────────────────────────── */}
        <div className="w-72 shrink-0 overflow-hidden border-l border-border">
          {selected === null ? (
            <div className="flex h-full flex-col items-center justify-center gap-2 p-6 text-center">
              <p className="text-sm font-medium">No selection</p>
              <p className="text-xs text-muted-foreground">
                Click a driver marker or a ride in the list to view live details.
              </p>
            </div>
          ) : selected.type === "driver" && selectedDriver ? (
            <div className="relative h-full">
              <button
                onClick={() => { setSelected(null); setSelectedDriver(null); }}
                className="absolute right-2 top-2 z-10 rounded-full p-1 hover:bg-muted"
                aria-label="Close driver panel"
              >
                <X className="h-3.5 w-3.5" aria-hidden="true" />
              </button>
              <DriverPanel
                driver={selectedDriver}
                onRideClick={handleSelectRide}
              />
            </div>
          ) : selected.type === "ride" && selectedRide ? (
            <div className="relative h-full">
              <button
                onClick={() => { setSelected(null); setSelectedRide(null); }}
                className="absolute right-2 top-2 z-10 rounded-full p-1 hover:bg-muted"
                aria-label="Close ride panel"
              >
                <X className="h-3.5 w-3.5" aria-hidden="true" />
              </button>
              <RidePanel
                ride={selectedRide}
                onDriverClick={handleSelectDriver}
                onCancelRide={(id) => {
                  setPendingCancelId(id);
                  setCancelReason("Cancelled by admin");
                  setCancelDialogOpen(true);
                }}
                onCompleteRide={async (id) => {
                  try {
                    const { adminCompleteRide } = await import("@/lib/api");
                    await adminCompleteRide(id);
                    ridesMapRef.current.delete(id);
                    mapHandlesRef.current?.removeRideMarkers(id);
                    refreshCounts();
                    setSelected(null);
                    setSelectedRide(null);
                  } catch (err: any) {
                    toast({ title: "Failed to complete ride", description: err?.message ?? "Unknown error", variant: "destructive" });
                  }
                }}
              />
            </div>
          ) : null}
        </div>
      </div>

      {/* Cancel ride dialog */}
      <Dialog open={cancelDialogOpen} onOpenChange={(open) => { if (!open) setCancelDialogOpen(false); }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Cancel Ride</DialogTitle>
          </DialogHeader>
          <div className="space-y-2 py-2">
            <Label htmlFor="cancel-reason">Cancellation reason (shown to rider &amp; driver)</Label>
            <Input
              id="cancel-reason"
              value={cancelReason}
              onChange={(e) => setCancelReason(e.target.value)}
              placeholder="Cancelled by admin"
            />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setCancelDialogOpen(false)}>
              Back
            </Button>
            <Button
              variant="destructive"
              onClick={async () => {
                if (!pendingCancelId) return;
                const id = pendingCancelId;
                const reason = cancelReason.trim() || "Cancelled by admin";
                setCancelDialogOpen(false);
                setPendingCancelId(null);
                try {
                  await adminCancelRide(id, reason);
                  ridesMapRef.current.delete(id);
                  mapHandlesRef.current?.removeRideMarkers(id);
                  refreshCounts();
                  setSelected(null);
                  setSelectedRide(null);
                } catch (err: any) {
                  toast({ title: "Failed to cancel ride", description: err?.message ?? "Unknown error", variant: "destructive" });
                }
              }}
            >
              Confirm Cancel
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
