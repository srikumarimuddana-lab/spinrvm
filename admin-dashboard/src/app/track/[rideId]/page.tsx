'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import { useParams } from 'next/navigation';
import Script from 'next/script';
import {
  buildPathGradient,
  routePinSvg,
  ROUTE_STROKE_WIDTH,
  type RoutePinKind,
} from '@spinr/shared/constants/routeMapStyle';

// Google Maps API key — add NEXT_PUBLIC_GOOGLE_MAPS_API_KEY to Vercel env vars.
// Same value as EXPO_PUBLIC_GOOGLE_MAPS_API_KEY used by the mobile apps;
// ensure track.spinr.ca is listed as an authorised referrer in the
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

/** Pin diameter on the tracking map — bigger than in-app, this is a phone
 *  browser at arm's length with no other chrome competing for attention. */
const TRACK_PIN_SIZE = 34;

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
  const routePolylinesRef = useRef<G[]>([]);  // OSRM gradient route (array of coloured segments)
  const didFitRef    = useRef(false);
  // Last driver position used for the current route line — used to decide
  // whether to re-fetch from OSRM when the driver moves.
  const lastRoutedDriverRef = useRef<{ lat: number; lng: number } | null>(null);
  const lastRoutedLegRef = useRef<'pickup' | 'dropoff' | null>(null);

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
      mapId: process.env.NEXT_PUBLIC_GOOGLE_MAPS_MAP_ID ?? 'DEMO_MAP_ID',
    });

    // Route gradient lines are created dynamically per-segment; nothing to
    // initialise here — routePolylinesRef starts as an empty array.
  }, [mapsReady]);

  // ── Sync markers + OSRM route whenever ride data changes ────────────────────
  useEffect(() => {
    const g: G = (window as G).google?.maps;
    if (!g || !mapRef.current || !ride) return;
    const map = mapRef.current;

    // Pickup + drop-off markers come from the SHARED spec (routePinSvg), so
    // the page a rider watches their ride on draws exactly the pins the rider
    // app, the driver app, the head unit and the admin maps draw. This page
    // used to invent its own — a green nav-arrow disc and a red teardrop — so
    // the one surface we send to riders by link was the odd one out.
    const svgUrl = (kind: RoutePinKind, size: number) =>
      `data:image/svg+xml,${encodeURIComponent(routePinSvg(kind, size))}`;
    const pickupNavSvg = svgUrl('pickup', TRACK_PIN_SIZE);
    const dropoffPinSvg = svgUrl('dropoff', TRACK_PIN_SIZE);

    // Lyft-style driver marker: a clean top-down sedan. Single dark body with
    // a soft drop shadow, tinted windshield, roof panel, rear window, and
    // subtle side mirrors — minimal and professional, no cartoon wheels or
    // headlights. Nose points up; the icon is centre-anchored on the GPS point.
    const carSvg =
      `data:image/svg+xml,${encodeURIComponent(
        `<svg xmlns="http://www.w3.org/2000/svg" width="52" height="52" viewBox="0 0 52 52">
          <defs>
            <filter id="cshadow" x="-40%" y="-40%" width="180%" height="180%">
              <feDropShadow dx="0" dy="1.5" stdDeviation="2.2" flood-color="#0B1220" flood-opacity="0.45"/>
            </filter>
            <linearGradient id="cbody" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0" stop-color="#283244"/>
              <stop offset="1" stop-color="#161E2B"/>
            </linearGradient>
          </defs>
          <g filter="url(#cshadow)">
            <path d="M17.8 17.5 L15.3 18 L15.7 20.2 L18 19.6 Z" fill="#1B2433"/>
            <path d="M34.2 17.5 L36.7 18 L36.3 20.2 L34 19.6 Z" fill="#1B2433"/>
            <path d="M26 7 C21.4 7 18.7 9.6 18.1 14.2 L17.5 20 C17.1 26 17.1 33 17.9 39 C18.3 42.6 20.5 44.6 26 44.6 C31.5 44.6 33.7 42.6 34.1 39 C34.9 33 34.9 26 34.5 20 L33.9 14.2 C33.3 9.6 30.6 7 26 7 Z"
                  fill="url(#cbody)" stroke="#0B1220" stroke-width="0.8"/>
            <path d="M21 15.8 C23 14 29 14 31 15.8 L30 20.2 L22 20.2 Z" fill="#8FB7DB"/>
            <rect x="21" y="21" width="10" height="9.5" rx="2.4" fill="#3A4759"/>
            <rect x="22.5" y="22.2" width="7" height="2" rx="1" fill="#4B5A6E"/>
            <path d="M22 31.4 L30 31.4 L29.2 35.4 C27.6 36.3 24.4 36.3 22.8 35.4 Z" fill="#5A6B82"/>
          </g>
        </svg>`
      )}`;

    // Helper: create or move an AdvancedMarkerElement.
    // centerAnchor=true wraps the image in a zero-height div so the image
    // center (not its bottom edge) lands on the map position — used for the
    // circular car icon. Pin markers use the default bottom-center anchor so
    // the teardrop tip naturally points at the location.
    const upsertMarker = (
      ref: React.MutableRefObject<G>,
      lat: number | undefined,
      lng: number | undefined,
      svgUrl: string,
      size: number,
      centerAnchor = false,
      zIndex = 1,
    ) => {
      if (lat == null || lng == null) {
        if (ref.current) { ref.current.map = null; ref.current = null; }
        return;
      }
      const pos = { lat, lng };
      if (!ref.current) {
        const img = document.createElement('img');
        img.src = svgUrl;
        img.style.width = `${size}px`;
        // height:auto preserves the SVG aspect ratio — the pin is taller than
        // it is wide (34x46); the car is square so width===height there.
        img.style.height = centerAnchor ? `${size}px` : 'auto';
        img.style.display = 'block';
        let content: HTMLElement = img;
        if (centerAnchor) {
          const wrapper = document.createElement('div');
          wrapper.style.width = `${size}px`;
          wrapper.style.height = '0';
          wrapper.style.overflow = 'visible';
          img.style.marginTop = `-${size / 2}px`;
          wrapper.appendChild(img);
          content = wrapper;
        }
        ref.current = new g.marker.AdvancedMarkerElement({ map, position: pos, content, zIndex });
      } else {
        ref.current.position = pos;
      }
    };

    // Both centre-anchored: the shared pin is a disc that marks the point
    // itself, so there is no teardrop tip for the two ends to disagree about.
    upsertMarker(pickupMarkerRef,  ride.pickup_lat,  ride.pickup_lng,  pickupNavSvg,  TRACK_PIN_SIZE, true);
    upsertMarker(dropoffMarkerRef, ride.dropoff_lat, ride.dropoff_lng, dropoffPinSvg, TRACK_PIN_SIZE, true);

    const d = ride.driver;
    // Args: (ref, lat, lng, svgUrl, size, centerAnchor, zIndex).
    // centerAnchor MUST be the 6th arg (a boolean) — passing zIndex here
    // directly is a type error and breaks the Vercel build. The car is a
    // round puck so it centre-anchors on the driver's GPS position.
    upsertMarker(driverMarkerRef, d?.lat, d?.lng, carSvg, 52, true, 2);

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
    const currentLeg: 'pickup' | 'dropoff' = EN_ROUTE_TO_PICKUP.has(ride.status) ? 'pickup' : 'dropoff';
    // Reroute when: we've never routed yet, OR the leg changed (e.g. the ride
    // flips driver_arrived → in_progress while the driver is stationary, so the
    // destination switches pickup → dropoff), OR the driver moved past the
    // threshold. The leg check fixes the "stuck showing driver→pickup" bug; not
    // re-fetching on an unchanged leg fixes the every-5s identical-request loop
    // when there is no driver (position-based reroute can't fire).
    const legChanged = lastRoutedLegRef.current !== currentLeg;
    const driverMoved =
      hasDriver &&
      lastRoutedDriverRef.current != null &&
      (Math.abs(d!.lat! - lastRoutedDriverRef.current.lat) > ROUTE_REROUTE_THRESHOLD ||
        Math.abs(d!.lng! - lastRoutedDriverRef.current.lng) > ROUTE_REROUTE_THRESHOLD);
    const neverRouted = lastRoutedLegRef.current === null;

    if ((neverRouted || legChanged || driverMoved) && ride.pickup_lat != null && ride.dropoff_lat != null) {
      const originLat  = hasDriver ? d!.lat!  : ride.pickup_lat;
      const originLng  = hasDriver ? d!.lng!  : ride.pickup_lng!;
      const destLat    = currentLeg === 'pickup' ? ride.pickup_lat  : ride.dropoff_lat;
      const destLng    = currentLeg === 'pickup' ? ride.pickup_lng! : ride.dropoff_lng!;

      lastRoutedLegRef.current = currentLeg;
      // Track driver position for the move-based reroute; clear it when there is
      // no driver so we don't re-fetch until the leg changes or a driver appears.
      lastRoutedDriverRef.current = hasDriver ? { lat: d!.lat!, lng: d!.lng! } : null;

      const url =
        `https://router.project-osrm.org/route/v1/driving/` +
        `${originLng},${originLat};${destLng},${destLat}` +
        `?overview=full&geometries=geojson`;

      fetch(url)
        .then(r => r.ok ? r.json() : null)
        .then(data => {
          const coords: [number, number][] | undefined = data?.routes?.[0]?.geometry?.coordinates;
          if (!coords || !mapRef.current) return;

          // Clear previous gradient segments.
          routePolylinesRef.current.forEach(l => l.setMap(null));
          routePolylinesRef.current = [];

          // Draw the real OSRM route as the shared orange→red gradient
          // (#FF9500 → #EE2B2B). OSRM returns [lng, lat]; the shared helper
          // works in [lat, lng]; Google Maps needs {lat, lng}. Geometry is
          // unchanged — every coordinate is preserved, only the per-chunk
          // colour is derived from position along the path.
          const gradient = buildPathGradient(
            coords.map(([lng, lat]) => [lat, lng] as [number, number]),
          );
          for (const chunk of gradient) {
            routePolylinesRef.current.push(new g.Polyline({
              map: mapRef.current,
              path: chunk.coordinates.map(([lat, lng]) => ({ lat, lng })),
              strokeColor: chunk.color,
              strokeWeight: ROUTE_STROKE_WIDTH,
              strokeOpacity: 0.9,
              zIndex: 1,
            }));
          }
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
            src={`https://maps.googleapis.com/maps/api/js?key=${GMAPS_KEY}&libraries=marker&v=weekly`}
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
