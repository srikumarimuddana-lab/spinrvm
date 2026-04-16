"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Radio, X } from "lucide-react";
import { useAuthStore } from "@/store/authStore";

import { getMonitoringDrivers, getMonitoringRides, getServiceAreas, getVehicleTypes } from "@/lib/api";
import { useMonitoringSocket } from "@/hooks/use-monitoring-socket";

import { MonitoringMap, MapHandles, MonitoringServiceArea } from "./monitoring-map";
import { MonitoringToolbar } from "./toolbar";
import { DriverPanel } from "./driver-panel";
import { RidePanel } from "./ride-panel";
import type {
  AlertEvent,
  MonitoringCounts,
  MonitoringDriver,
  MonitoringFilters,
  MonitoringRide,
  MonitoringWsEvent,
  SelectedItem,
} from "./types";

const POLL_INTERVAL_MS = 15_000;

export default function MonitoringPage() {
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
  const [alerts, setAlerts] = useState<AlertEvent[]>([]);

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
        const ride = ridesMapRef.current.get(event.ride_id);
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
        pushAlert({ icon: "ride_cancelled", message: "Ride cancelled", ride_id: event.ride_id });
        if (selected?.type === "ride" && selected.id === event.ride_id) {
          setSelected(null);
          setSelectedRide(null);
        }
        break;
      }
    }
  }, [applyDriver, applyRide, followMode, pushAlert, refreshCounts, selected]);

  const { status: wsStatus } = useMonitoringSocket({ token, onEvent: handleWsEvent });

  // ── Initial data load + polling ─────────────────────────────────────
  const loadData = useCallback(async () => {
    const [rawDrivers, rawRides] = await Promise.all([
      getMonitoringDrivers().catch(() => [] as any[]),
      getMonitoringRides().catch(() => [] as any[]),
    ]);

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

  // Load service areas + vehicle types once
  useEffect(() => {
    getServiceAreas()
      .then((areas: any[]) =>
        setServiceAreas(
          areas.map((a) => ({
            id: a.id,
            name: a.name,
            geojson: a.geojson ?? null,
            fallbackCenter: a.center_lat && a.center_lng
              ? { lat: a.center_lat, lng: a.center_lng }
              : null,
          }))
        )
      )
      .catch(() => {});

    getVehicleTypes()
      .then((vt: any[]) =>
        setVehicleTypes(vt.map((v) => ({ id: v.id, name: v.name })))
      )
      .catch(() => {});
  }, []);

  // Poll data
  useEffect(() => {
    loadData();
    const interval = setInterval(loadData, POLL_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [loadData]);

  // Re-apply filters when they change
  useEffect(() => {
    driversMapRef.current.forEach((d) => applyDriver(d));
  }, [applyDriver]);

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

  const handleAreaFit = useCallback((areaId: string) => {
    mapHandlesRef.current?.fitArea(areaId);
  }, []);

  // Filter active rides list by service area
  const visibleRides = useMemo(() => {
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

  const alertIconMap: Record<AlertEvent["icon"], string> = {
    online: "🟢", offline: "⚫", ride_new: "🚗", ride_done: "✅", ride_cancelled: "❌",
  };

  return (
    <div className="flex h-[calc(100vh-4rem)] flex-col overflow-hidden">
      {/* Page header */}
      <div className="flex items-center justify-between border-b border-border px-4 py-3">
        <h1 className="flex items-center gap-2 text-xl font-bold">
          <Radio className="h-5 w-5 text-red-500" />
          Live Ride Monitoring
          <span className="relative ml-1 flex h-2.5 w-2.5">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-red-400 opacity-75" />
            <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-red-500" />
          </span>
        </h1>
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
        vehicleTypes={vehicleTypes}
        wsStatus={wsStatus}
      />

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
                        r.status === "searching" ? "bg-amber-100 text-amber-700" :
                        r.status === "driver_assigned" ? "bg-blue-100 text-blue-700" :
                        r.status === "driver_arrived" ? "bg-purple-100 text-purple-700" :
                        "bg-green-100 text-green-700"
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

          {/* Recent alerts */}
          <div className="border-t border-border">
            <div className="px-3 py-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              Activity
            </div>
            <div className="max-h-40 overflow-y-auto">
              {alerts.length === 0 ? (
                <p className="pb-3 text-center text-xs text-muted-foreground">No activity yet</p>
              ) : (
                alerts.slice(0, 15).map((a) => (
                  <div key={a.id} className="flex items-start gap-1.5 px-3 py-1 text-[11px]">
                    <span>{alertIconMap[a.icon]}</span>
                    <span className="flex-1 text-muted-foreground">{a.message}</span>
                  </div>
                ))
              )}
            </div>
          </div>
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
              >
                <X className="h-3.5 w-3.5" />
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
              >
                <X className="h-3.5 w-3.5" />
              </button>
              <RidePanel
                ride={selectedRide}
                onDriverClick={handleSelectDriver}
                onCancelRide={(id) => {
                  // Optimistic remove
                  ridesMapRef.current.delete(id);
                  mapHandlesRef.current?.removeRideMarkers(id);
                  refreshCounts();
                  setSelected(null);
                  setSelectedRide(null);
                }}
              />
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}
