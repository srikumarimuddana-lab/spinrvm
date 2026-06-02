'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import { useParams } from 'next/navigation';
import maplibregl from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import {
  DEFAULT_CENTER,
  addStandardControls,
  trackBaseMapStyle,
  MAP_STYLE_FALLBACK,
} from '@/lib/map/maplibre-base';

// Labeled vector basemap (Protomaps when a key is configured, otherwise the
// keyless OpenFreeMap fallback). We deliberately do NOT use raw OSM raster
// tiles here: OSM's tile-usage policy forbids using them as an app basemap and
// they load blank/throttled in the field, which is what left the tracking map
// showing pins floating over grey with no street detail.

interface RideInfo {
  status: string;
  message?: string;
  pickup_address: string;
  dropoff_address: string;
  pickup_lat?: number;
  pickup_lng?: number;
  dropoff_lat?: number;
  dropoff_lng?: number;
  ride_code?: string;
  eta_minutes?: number | null;
  driver?: {
    name: string;
    lat?: number;
    lng?: number;
    vehicle_make?: string;
    vehicle_model?: string;
    vehicle_color?: string;
    vehicle_year?: number;
    license_plate?: string;
    rating?: number;
    photo_url?: string;
  };
}

const STATUS_LABEL: Record<string, { label: string; color: string; bg: string }> = {
  searching: { label: 'Finding driver', color: '#B45309', bg: '#FEF3C7' },
  driver_assigned: { label: 'Driver assigned', color: '#1D4ED8', bg: '#DBEAFE' },
  driver_accepted: { label: 'Driver on the way', color: '#1D4ED8', bg: '#DBEAFE' },
  driver_arrived: { label: 'Driver arrived', color: '#047857', bg: '#D1FAE5' },
  in_progress: { label: 'Trip in progress', color: '#5B21B6', bg: '#EDE9FE' },
  completed: { label: 'Trip complete', color: '#4B5563', bg: '#F3F4F6' },
  cancelled: { label: 'Trip cancelled', color: '#B91C1C', bg: '#FEE2E2' },
};

export default function TrackRide() {
  const params = useParams();
  const shareToken = params.rideId as string;
  const [ride, setRide] = useState<RideInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);

  const mapContainerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const driverMarkerRef = useRef<maplibregl.Marker | null>(null);
  const pickupMarkerRef = useRef<maplibregl.Marker | null>(null);
  const dropoffMarkerRef = useRef<maplibregl.Marker | null>(null);
  const didFitRef = useRef(false);
  const routeFetchedRef = useRef(false);
  const didFallbackRef = useRef(false);
  const ROUTE_SOURCE_ID = 'spinr-route';
  const ROUTE_LAYER_ID = 'spinr-route-line';

  // Poll the public endpoint every 5 s while the ride is active. The endpoint
  // is the canonical source — page just renders what it sends, no client math.
  useEffect(() => {
    let cancelled = false;
    const fetchRideStatus = async () => {
      try {
        const apiUrl = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
        const res = await fetch(`${apiUrl}/api/v1/rides/track/${shareToken}`);
        if (!res.ok) throw new Error('Tracking link is invalid or has expired.');
        const data = await res.json();
        if (cancelled) return;
        setRide(data);
        setLastUpdated(new Date());
        setError('');
      } catch (err: any) {
        if (!cancelled) setError(err.message);
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    fetchRideStatus();
    const id = setInterval(fetchRideStatus, 5000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [shareToken]);

  const statusCfg = STATUS_LABEL[ride?.status || ''] || STATUS_LABEL.searching;
  const isActive = !!ride?.status && !['completed', 'cancelled'].includes(ride.status);
  const driverName = ride?.driver?.name || 'Driver';
  const vehicleLine = useMemo(() => {
    const d = ride?.driver;
    if (!d) return '';
    return [d.vehicle_color, d.vehicle_year, d.vehicle_make, d.vehicle_model].filter(Boolean).join(' ');
  }, [ride?.driver]);
  const driverInitial = (driverName.trim()[0] || 'D').toUpperCase();

  // Initialise the map once when the container mounts.
  useEffect(() => {
    if (!mapContainerRef.current || mapRef.current) return;
    // Serve the tile-processing worker from the same origin so it passes
    // `worker-src 'self'` without needing `worker-src blob:` in the CSP.
    // The file is copied from node_modules at build time (see public/).
    maplibregl.workerUrl = '/maplibre-gl-csp-worker.js';
    const primaryStyle = trackBaseMapStyle();
    const map = new maplibregl.Map({
      container: mapContainerRef.current,
      style: primaryStyle,
      center: DEFAULT_CENTER,
      zoom: 11,
      attributionControl: { compact: true },
    });
    map.on('error', (e) => {
      // Surface tile/style load failures so they don't fail silently in the
      // field — the screen would otherwise just stay blank. If the primary
      // (Protomaps) style itself failed to load, fall back once to the keyless
      // OpenFreeMap style so a misconfigured/over-quota key never leaves the
      // rider with a blank map.
      const msg = e?.error?.message || String(e);
      console.warn('[track-map] map error:', msg);
      if (!didFallbackRef.current && primaryStyle !== MAP_STYLE_FALLBACK && !map.isStyleLoaded()) {
        didFallbackRef.current = true;
        routeFetchedRef.current = false; // re-add the route layer onto the new style
        map.setStyle(MAP_STYLE_FALLBACK);
      }
    });
    addStandardControls(map);
    mapRef.current = map;
    return () => {
      map.remove();
      mapRef.current = null;
    };
  }, []);

  // Sync markers + bounds whenever the ride payload changes.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ride) return;
    if (!map.loaded()) {
      map.once('load', () => syncMap());
      return;
    }
    syncMap();

    function syncMap() {
      const points: { lat: number; lng: number }[] = [];

      const setMarker = (
        ref: React.MutableRefObject<maplibregl.Marker | null>,
        lat: number | undefined,
        lng: number | undefined,
        el: HTMLElement,
      ) => {
        if (lat == null || lng == null) {
          if (ref.current) {
            ref.current.remove();
            ref.current = null;
          }
          return;
        }
        if (!ref.current) {
          ref.current = new maplibregl.Marker({ element: el }).setLngLat([lng, lat]).addTo(map!);
        } else {
          ref.current.setLngLat([lng, lat]);
        }
        points.push({ lat, lng });
      };

      setMarker(pickupMarkerRef, ride!.pickup_lat, ride!.pickup_lng, pinElement('#10B981', 'A'));
      setMarker(dropoffMarkerRef, ride!.dropoff_lat, ride!.dropoff_lng, pinElement('#EF4444', 'B'));
      const d = ride!.driver;
      if (d?.lat != null && d?.lng != null) {
        setMarker(driverMarkerRef, d.lat, d.lng, carElement());
      } else if (driverMarkerRef.current) {
        driverMarkerRef.current.remove();
        driverMarkerRef.current = null;
      }

      // Draw a road-following route between pickup and drop-off once both
      // endpoints are known. OSRM (public OSM routing service, no key
      // required) returns the polyline; we render it as a layer underneath
      // the markers so the rider sees the actual streets the driver will
      // take, not a straight line cutting across the map.
      if (
        !routeFetchedRef.current
        && ride!.pickup_lat != null && ride!.pickup_lng != null
        && ride!.dropoff_lat != null && ride!.dropoff_lng != null
      ) {
        routeFetchedRef.current = true;
        const url =
          `https://router.project-osrm.org/route/v1/driving/` +
          `${ride!.pickup_lng},${ride!.pickup_lat};${ride!.dropoff_lng},${ride!.dropoff_lat}` +
          `?overview=full&geometries=geojson`;
        fetch(url)
          .then((r) => (r.ok ? r.json() : null))
          .then((data) => {
            const coords: [number, number][] | undefined =
              data?.routes?.[0]?.geometry?.coordinates;
            if (!coords || coords.length < 2 || !mapRef.current) return;
            const m = mapRef.current;
            if (m.getSource(ROUTE_SOURCE_ID)) {
              (m.getSource(ROUTE_SOURCE_ID) as maplibregl.GeoJSONSource).setData({
                type: 'Feature', properties: {}, geometry: { type: 'LineString', coordinates: coords },
              });
            } else {
              m.addSource(ROUTE_SOURCE_ID, {
                type: 'geojson',
                data: { type: 'Feature', properties: {}, geometry: { type: 'LineString', coordinates: coords } },
              });
              m.addLayer({
                id: ROUTE_LAYER_ID,
                type: 'line',
                source: ROUTE_SOURCE_ID,
                layout: { 'line-cap': 'round', 'line-join': 'round' },
                paint: { 'line-color': '#111827', 'line-width': 4, 'line-opacity': 0.85 },
              });
            }
          })
          .catch(() => { /* silent fallback — markers remain visible */ });
      }

      // Fit once on first load so the rider sees the whole trip; afterwards
      // only re-center on the driver so the camera doesn't keep snapping back.
      if (!didFitRef.current && points.length >= 2) {
        // Build a LngLatBounds from the points and fit with our own options
        // (the shared helper only takes a number for padding — no maxZoom).
        const bounds = points.reduce(
          (b, p) => b.extend([p.lng, p.lat] as [number, number]),
          new maplibregl.LngLatBounds(),
        );
        map!.fitBounds(bounds, { padding: 80, maxZoom: 15, duration: 500 });
        didFitRef.current = true;
      } else if (d?.lat != null && d?.lng != null && didFitRef.current) {
        map!.easeTo({ center: [d.lng, d.lat], duration: 800 });
      }
    }
  }, [ride]);

  if (loading) {
    return (
      <Centered>
        <div className="text-center">
          <div className="animate-spin rounded-full h-10 w-10 border-2 border-gray-200 border-t-gray-800 mx-auto mb-4" />
          <p className="text-sm text-gray-500 font-medium">Loading trip…</p>
        </div>
      </Centered>
    );
  }

  if (error || !ride) {
    return (
      <Centered>
        <div className="bg-white p-8 rounded-2xl shadow-sm max-w-sm w-full text-center border border-gray-100">
          <h1 className="text-lg font-semibold text-gray-900 mb-1">Tracking unavailable</h1>
          <p className="text-sm text-gray-500">{error || 'This link may have expired or the ride has ended.'}</p>
        </div>
      </Centered>
    );
  }

  return (
    <div className="min-h-screen bg-gray-50 flex flex-col">
      {/* Map fills the top of the viewport. Use explicit height (h-[60vh])
          rather than flex-1 so the canvas always has dimensions on first
          paint — flex-grow can resolve to 0 height on some mobile browsers
          while the layout is settling, which kills MapLibre silently. */}
      <div className="relative w-full" style={{ height: '60vh', minHeight: 320 }}>
        <div ref={mapContainerRef} className="absolute inset-0 bg-gray-200" />

        {/* Status pill, top-left over the map */}
        <div
          className="absolute top-4 left-4 right-4 mx-auto max-w-md flex items-center gap-2 px-3 py-2 rounded-full shadow-sm bg-white/95 backdrop-blur"
        >
          <span
            className="inline-block w-2 h-2 rounded-full"
            style={{ backgroundColor: statusCfg.color }}
          />
          <span className="text-xs font-semibold text-gray-700 tracking-wide">
            {statusCfg.label.toUpperCase()}
          </span>
          {isActive && ride.eta_minutes != null && (
            <span className="ml-auto text-xs font-medium text-gray-500">
              ETA {ride.eta_minutes} min
            </span>
          )}
        </div>
      </div>

      {/* Bottom sheet: ETA, driver, route */}
      <div className="bg-white rounded-t-3xl -mt-6 shadow-[0_-8px_30px_rgba(0,0,0,0.06)] relative">
        <div className="mx-auto w-12 h-1.5 bg-gray-200 rounded-full mt-3" />

        <div className="px-5 pt-5 pb-4">
          {ride.eta_minutes != null && isActive ? (
            <div className="flex items-baseline gap-2">
              <span className="text-3xl font-semibold text-gray-900 tracking-tight">{ride.eta_minutes}</span>
              <span className="text-sm text-gray-500 font-medium">min away</span>
            </div>
          ) : (
            <div className="text-base font-semibold text-gray-900">{statusCfg.label}</div>
          )}
          {ride.message && <p className="text-sm text-gray-500 mt-1">{ride.message}</p>}
        </div>

        {/* Driver card */}
        {ride.driver && (
          <div className="mx-5 mb-5 rounded-2xl border border-gray-100 bg-gray-50/70 p-4 flex items-center gap-4">
            <div className="relative">
              {ride.driver.photo_url ? (
                <img
                  src={ride.driver.photo_url}
                  alt={driverName}
                  className="w-12 h-12 rounded-full object-cover"
                />
              ) : (
                <div className="w-12 h-12 rounded-full bg-gray-900 text-white flex items-center justify-center font-semibold">
                  {driverInitial}
                </div>
              )}
              {typeof ride.driver.rating === 'number' && (
                <span className="absolute -bottom-1 -right-1 text-[10px] font-semibold px-1.5 py-0.5 rounded-full bg-white border border-gray-200 text-gray-700">
                  ★ {ride.driver.rating.toFixed(1)}
                </span>
              )}
            </div>
            <div className="flex-1 min-w-0">
              <div className="text-sm font-semibold text-gray-900 truncate">{driverName}</div>
              {vehicleLine && <div className="text-xs text-gray-500 truncate mt-0.5">{vehicleLine}</div>}
            </div>
            {ride.driver.license_plate && (
              <div className="px-2.5 py-1 rounded-md bg-white border border-gray-200 text-xs font-mono font-semibold text-gray-900 tracking-wider">
                {ride.driver.license_plate}
              </div>
            )}
          </div>
        )}

        {/* Route */}
        <div className="mx-5 mb-5 rounded-2xl border border-gray-100 p-4">
          <div className="flex gap-3 relative before:absolute before:top-2 before:bottom-2 before:left-[7px] before:w-0.5 before:bg-gray-200">
            <div className="flex flex-col items-center pt-1">
              <span className="w-4 h-4 rounded-full bg-emerald-500 border-2 border-white shadow-sm z-10" />
              <span className="flex-1" />
              <span className="w-4 h-4 rounded-sm bg-red-500 border-2 border-white shadow-sm z-10" />
            </div>
            <div className="flex-1 space-y-3">
              <div>
                <div className="text-[10px] font-semibold text-gray-400 tracking-wider uppercase">Pickup</div>
                <div className="text-sm text-gray-900 mt-0.5">{ride.pickup_address}</div>
              </div>
              <div>
                <div className="text-[10px] font-semibold text-gray-400 tracking-wider uppercase">Drop-off</div>
                <div className="text-sm text-gray-900 mt-0.5">{ride.dropoff_address}</div>
              </div>
            </div>
          </div>
        </div>

        <div className="px-5 pb-4 flex items-center justify-between text-[11px] text-gray-400">
          <span>{ride.ride_code ? `Ref ${ride.ride_code}` : ''}</span>
          {isActive && lastUpdated && (
            <span>
              Updated {lastUpdated.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit', second: '2-digit' })}
            </span>
          )}
        </div>

        <div className="py-3 text-center text-[11px] text-gray-400 border-t border-gray-100">
          Live tracking · <span className="font-semibold text-gray-500">Spinr</span>
        </div>
      </div>
    </div>
  );
}

function Centered({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-center min-h-screen bg-gray-50 p-4">
      {children}
    </div>
  );
}

function pinElement(color: string, letter: string): HTMLElement {
  const el = document.createElement('div');
  el.style.cssText = `
    width: 28px; height: 28px; border-radius: 50% 50% 50% 0;
    transform: rotate(-45deg);
    background: ${color};
    border: 2px solid #fff;
    box-shadow: 0 2px 6px rgba(0,0,0,0.25);
    display: flex; align-items: center; justify-content: center;
    color: #fff; font-size: 12px; font-weight: 700; font-family: ui-sans-serif, system-ui;
  `;
  const inner = document.createElement('span');
  inner.textContent = letter;
  inner.style.cssText = 'transform: rotate(45deg);';
  el.appendChild(inner);
  return el;
}

function carElement(): HTMLElement {
  const el = document.createElement('div');
  el.style.cssText = `
    width: 36px; height: 36px; border-radius: 50%;
    background: #111827;
    border: 3px solid #fff;
    box-shadow: 0 4px 10px rgba(0,0,0,0.3);
    display: flex; align-items: center; justify-content: center;
    color: #fff; font-size: 18px;
  `;
  el.textContent = '🚗';
  // Pulse ring
  const ring = document.createElement('div');
  ring.style.cssText = `
    position: absolute; inset: -8px;
    border-radius: 50%;
    border: 2px solid rgba(17,24,39,0.25);
    animation: pulse 2s ease-out infinite;
  `;
  el.style.position = 'relative';
  el.appendChild(ring);
  // Inject keyframes once
  if (!document.getElementById('spinr-pulse-kf')) {
    const style = document.createElement('style');
    style.id = 'spinr-pulse-kf';
    style.textContent = `@keyframes pulse {
      0% { transform: scale(0.85); opacity: 0.9; }
      100% { transform: scale(1.6); opacity: 0; }
    }`;
    document.head.appendChild(style);
  }
  return el;
}
