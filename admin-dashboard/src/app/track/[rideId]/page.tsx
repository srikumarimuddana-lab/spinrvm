'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import { useParams } from 'next/navigation';
import Script from 'next/script';

// Google Maps API key — add NEXT_PUBLIC_GOOGLE_MAPS_API_KEY to Vercel env vars.
// Same value as EXPO_PUBLIC_GOOGLE_MAPS_API_KEY used by the mobile apps;
// ensure spinrvm.vercel.app is listed as an authorised referrer in the
// Google Cloud Console → Credentials page for this key.
const GMAPS_KEY = process.env.NEXT_PUBLIC_GOOGLE_MAPS_API_KEY ?? '';

// Saskatoon default centre — map lands somewhere operational before locations load.
const DEFAULT_CENTER = { lat: 52.13, lng: -106.67 };

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

const STATUS_LABEL: Record<string, { label: string; color: string }> = {
  searching:        { label: 'Finding driver',    color: '#B45309' },
  driver_assigned:  { label: 'Driver assigned',   color: '#1D4ED8' },
  driver_accepted:  { label: 'Driver on the way', color: '#1D4ED8' },
  driver_arrived:   { label: 'Driver arrived',    color: '#047857' },
  in_progress:      { label: 'Trip in progress',  color: '#5B21B6' },
  completed:        { label: 'Trip complete',      color: '#4B5563' },
  cancelled:        { label: 'Trip cancelled',     color: '#B91C1C' },
};

// Statuses where the driver is heading to pickup (route: driver → pickup).
// Once in_progress the driver heads to dropoff (route: driver → dropoff).
const EN_ROUTE_TO_PICKUP = new Set(['driver_assigned', 'driver_accepted', 'driver_arrived']);

// Minimum distance (degrees ~= ~10m) the driver must move before we re-fetch
// the OSRM route — avoids hammering the public router on every poll tick.
const ROUTE_REROUTE_THRESHOLD = 0.0001;

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type G = any;

export default function TrackRide() {
  const params = useParams();
  const shareToken = params.rideId as string;
  const [ride, setRide] = useState<RideInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [mapsReady, setMapsReady] = useState(false);

  const mapContainerRef = useRef<HTMLDivElement | null>(null);
  const mapRef       = useRef<G>(null);
  const driverMarkerRef  = useRef<G>(null);
  const pickupMarkerRef  = useRef<G>(null);
  const dropoffMarkerRef = useRef<G>(null);
  const routePolylineRef = useRef<G>(null);  // OSRM route drawn as a Polyline
  const didFitRef    = useRef(false);
  // Last driver position used for the current route line — used to decide
  // whether to re-fetch from OSRM when the driver moves.
  const lastRoutedDriverRef = useRef<{ lat: number; lng: number } | null>(null);

  // ── Poll the public backend endpoint every 5 s ──────────────────────────────
  useEffect(() => {
    let cancelled = false;
    const fetchStatus = async () => {
      try {
        const apiUrl = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
        const res = await fetch(`${apiUrl}/api/v1/rides/track/${shareToken}`);
        if (!res.ok) throw new Error('Tracking link is invalid or has expired.');
        const data = await res.json();
        if (!cancelled) { setRide(data); setLastUpdated(new Date()); setError(''); }
      } catch (err: unknown) {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Unknown error');
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    fetchStatus();
    const id = setInterval(fetchStatus, 5000);
    return () => { cancelled = true; clearInterval(id); };
  }, [shareToken]);

  // ── Initialise Google Maps once the script has loaded ───────────────────────
  useEffect(() => {
    if (!mapsReady || !mapContainerRef.current || mapRef.current) return;
    const g: G = (window as G).google?.maps;
    if (!g) return;

    mapRef.current = new g.Map(mapContainerRef.current, {
      center: DEFAULT_CENTER,
      zoom: 12,
      disableDefaultUI: true,
      zoomControl: true,
      gestureHandling: 'greedy',
    });

    // OSRM route will be drawn on this polyline — Google Maps renders it
    // directly on the canvas without needing the Directions API.
    routePolylineRef.current = new g.Polyline({
      map: mapRef.current,
      path: [],
      strokeColor: '#111827',
      strokeWeight: 4,
      strokeOpacity: 0.85,
    });
  }, [mapsReady]);

  // ── Sync markers + OSRM route whenever ride data changes ────────────────────
  useEffect(() => {
    const g: G = (window as G).google?.maps;
    if (!g || !mapRef.current || !ride) return;
    const map = mapRef.current;

    // Inline SVG for the pickup pin (green teardrop).
    const pinSvg = (color: string, letter: string) =>
      `data:image/svg+xml,${encodeURIComponent(
        `<svg xmlns="http://www.w3.org/2000/svg" width="32" height="40" viewBox="0 0 32 40">
          <path d="M16 0C7.163 0 0 7.163 0 16c0 10 16 24 16 24S32 26 32 16C32 7.163 24.837 0 16 0z" fill="${color}"/>
          <circle cx="16" cy="16" r="8" fill="white"/>
          <text x="16" y="20" text-anchor="middle" font-size="10" font-weight="700"
                font-family="ui-sans-serif,system-ui,sans-serif" fill="${color}">${letter}</text>
        </svg>`
      )}`;

    // Top-down car SVG for the driver — looks professional and direction-neutral.
    const carSvg =
      `data:image/svg+xml,${encodeURIComponent(
        `<svg xmlns="http://www.w3.org/2000/svg" width="44" height="44" viewBox="0 0 44 44">
          <circle cx="22" cy="22" r="20" fill="#111827" stroke="white" stroke-width="3"/>
          <g fill="white" transform="translate(10,11)">
            <path d="M20 6H4L2 10h20L20 6z"/>
            <rect x="2" y="11" width="20" height="6" rx="1"/>
            <rect x="3" y="9" width="4" height="3" rx="1" fill="#93C5FD"/>
            <rect x="17" y="9" width="4" height="3" rx="1" fill="#93C5FD"/>
            <circle cx="5"  cy="18.5" r="2" fill="#6B7280"/>
            <circle cx="19" cy="18.5" r="2" fill="#6B7280"/>
          </g>
        </svg>`
      )}`;

    // Helper: create or move a marker.
    const upsertMarker = (
      ref: React.MutableRefObject<G>,
      lat: number | undefined,
      lng: number | undefined,
      iconUrl: string,
      size: number,
      zIndex = 1,
    ) => {
      if (lat == null || lng == null) {
        if (ref.current) { ref.current.setMap(null); ref.current = null; }
        return;
      }
      const pos = { lat, lng };
      const icon = { url: iconUrl, scaledSize: new g.Size(size, size), anchor: new g.Point(size / 2, size / 2) };
      if (!ref.current) {
        ref.current = new g.Marker({ map, position: pos, icon, zIndex });
      } else {
        ref.current.setPosition(pos);
      }
    };

    upsertMarker(pickupMarkerRef,  ride.pickup_lat,  ride.pickup_lng,  pinSvg('#10B981', 'A'), 36);
    upsertMarker(dropoffMarkerRef, ride.dropoff_lat, ride.dropoff_lng, pinSvg('#EF4444', 'B'), 36);

    const d = ride.driver;
    upsertMarker(driverMarkerRef, d?.lat, d?.lng, carSvg, 44, 2);

    // Pan to driver after initial fit.
    if (d?.lat != null && didFitRef.current) {
      map.panTo({ lat: d.lat, lng: d.lng! });
    }

    // ── Fit view on first load ─────────────────────────────────────────────────
    if (!didFitRef.current) {
      const bounds = new g.LatLngBounds();
      let pts = 0;
      if (ride.pickup_lat  != null) { bounds.extend({ lat: ride.pickup_lat,  lng: ride.pickup_lng!  }); pts++; }
      if (ride.dropoff_lat != null) { bounds.extend({ lat: ride.dropoff_lat, lng: ride.dropoff_lng! }); pts++; }
      if (d?.lat           != null) { bounds.extend({ lat: d.lat,            lng: d.lng!            }); pts++; }
      if (pts >= 2) { map.fitBounds(bounds, 80); didFitRef.current = true; }
    }

    // ── OSRM route: recalculate from driver's current position ─────────────────
    // Route origin = driver (when assigned) or pickup (no driver yet).
    // Route destination = pickup (driver en route to pickup) or dropoff (trip in progress).
    const hasDriver = d?.lat != null && d?.lng != null;
    const driverMoved =
      !lastRoutedDriverRef.current ||
      (hasDriver && (
        Math.abs(d!.lat! - lastRoutedDriverRef.current.lat) > ROUTE_REROUTE_THRESHOLD ||
        Math.abs(d!.lng! - lastRoutedDriverRef.current.lng) > ROUTE_REROUTE_THRESHOLD
      ));

    if (driverMoved && ride.pickup_lat != null && ride.dropoff_lat != null) {
      const originLat  = hasDriver ? d!.lat!  : ride.pickup_lat;
      const originLng  = hasDriver ? d!.lng!  : ride.pickup_lng!;
      const destLat    = EN_ROUTE_TO_PICKUP.has(ride.status) ? ride.pickup_lat  : ride.dropoff_lat;
      const destLng    = EN_ROUTE_TO_PICKUP.has(ride.status) ? ride.pickup_lng! : ride.dropoff_lng!;

      if (hasDriver) {
        lastRoutedDriverRef.current = { lat: d!.lat!, lng: d!.lng! };
      }

      const url =
        `https://router.project-osrm.org/route/v1/driving/` +
        `${originLng},${originLat};${destLng},${destLat}` +
        `?overview=full&geometries=geojson`;

      fetch(url)
        .then(r => r.ok ? r.json() : null)
        .then(data => {
          const coords: [number, number][] | undefined = data?.routes?.[0]?.geometry?.coordinates;
          if (!coords || !routePolylineRef.current) return;
          // OSRM returns [lng, lat]; Google Maps needs {lat, lng}.
          routePolylineRef.current.setPath(coords.map(([lng, lat]) => ({ lat, lng })));
        })
        .catch(() => { /* silent — markers remain visible without a route line */ });
    }
  }, [ride, mapsReady]);

  const statusCfg    = STATUS_LABEL[ride?.status ?? ''] ?? STATUS_LABEL.searching;
  const isActive     = !!ride?.status && !['completed', 'cancelled'].includes(ride.status);
  const driverName   = ride?.driver?.name || 'Driver';
  const vehicleLine  = useMemo(() => {
    const dr = ride?.driver;
    if (!dr) return '';
    return [dr.vehicle_color, dr.vehicle_year, dr.vehicle_make, dr.vehicle_model].filter(Boolean).join(' ');
  }, [ride?.driver]);
  const driverInitial = (driverName.trim()[0] || 'D').toUpperCase();

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
      {/* Map — explicit height so the canvas always has dimensions on first paint */}
      <div className="relative w-full" style={{ height: '60vh', minHeight: 320 }}>
        <div ref={mapContainerRef} className="absolute inset-0 bg-gray-200" />

        {GMAPS_KEY ? (
          <Script
            src={`https://maps.googleapis.com/maps/api/js?key=${GMAPS_KEY}&v=weekly`}
            strategy="afterInteractive"
            onLoad={() => setMapsReady(true)}
          />
        ) : (
          <div className="absolute inset-0 flex items-center justify-center bg-gray-100 text-gray-500 text-sm px-4 text-center">
            Map unavailable — set <code className="mx-1">NEXT_PUBLIC_GOOGLE_MAPS_API_KEY</code> in Vercel
          </div>
        )}

        {/* Status pill */}
        <div className="absolute top-4 left-4 right-4 mx-auto max-w-md flex items-center gap-2 px-3 py-2 rounded-full shadow-sm bg-white/95 backdrop-blur">
          <span className="inline-block w-2 h-2 rounded-full" style={{ backgroundColor: statusCfg.color }} />
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

      {/* Bottom sheet */}
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
                <img src={ride.driver.photo_url} alt={driverName} className="w-12 h-12 rounded-full object-cover" />
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
            <span>Updated {lastUpdated.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit', second: '2-digit' })}</span>
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
