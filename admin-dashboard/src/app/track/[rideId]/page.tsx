'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useParams } from 'next/navigation';
import Script from 'next/script';
import {
  buildPathGradient,
  routePinSvg,
  ROUTE_PIN_COLORS,
  ROUTE_STROKE_WIDTH,
  type RoutePinKind,
} from '@spinr/shared/constants/routeMapStyle';
import {
  bearingDegrees,
  distanceMeters,
  shortestArcRotationTarget,
  snapToRoute,
  visualRotationDegrees,
  type TrackingLatLng,
} from '@spinr/shared/utils/vehicleTracking';
import {
  MARKER_ANIMATION_MS,
  interpolateMarker,
  prefersReducedMotion,
  type MarkerPose,
} from '@/lib/map/marker-interpolation';
import { setVisibleInterval } from '@/lib/visible-interval';
import { TRACK_LIGHT_TOKENS } from './light-tokens';

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

// `dot` is a semantic token class (see light-tokens.ts): pending → warning,
// on the move → info, arrived → success, ended → muted, cancelled →
// destructive. The label beside it carries the meaning; the dot only echoes it.
const STATUS_LABEL: Record<string, { label: string; dot: string }> = {
  searching:        { label: 'Finding driver',    dot: 'bg-warning' },
  driver_assigned:  { label: 'Driver assigned',   dot: 'bg-info' },
  driver_accepted:  { label: 'Driver on the way', dot: 'bg-info' },
  driver_arrived:   { label: 'Driver arrived',    dot: 'bg-success' },
  in_progress:      { label: 'Trip in progress',  dot: 'bg-info' },
  completed:        { label: 'Trip ended',        dot: 'bg-muted-foreground' },
  cancelled:        { label: 'Trip cancelled',    dot: 'bg-destructive' },
};

// Statuses where the driver is heading to pickup (route: driver → pickup).
// Once in_progress the driver heads to dropoff (route: driver → dropoff).
const EN_ROUTE_TO_PICKUP = new Set(['driver_assigned', 'driver_accepted', 'driver_arrived']);

// RideStatus.terminal_statuses() in the backend. For these the tracking
// endpoint (backend/routes/rides/sharing.py) returns only status, message and
// the two addresses — no driver, no coordinates — so nothing on the map is
// live any more.
const ENDED_STATUSES = new Set(['completed', 'cancelled']);

// Minimum distance (degrees ~= ~10m) the driver must move before we re-fetch
// the OSRM route — avoids hammering the public router on every poll tick.
const ROUTE_REROUTE_THRESHOLD = 0.0001;

// Beyond this from the OSRM line the driver is off-route (detour, stale route)
// — fall back to the travel bearing rather than lying about which way the car
// faces. Same value the mobile CarMarker uses for the same decision.
const MAX_ROUTE_SNAP_M = 35;
// How far the driver must move between 5 s polls before the straight-line
// travel bearing is trusted. Urban GPS error is 5–20 m, so a smaller floor
// would spin the car on noise while it sits at a light.
const MIN_TRAVEL_BEARING_MOVE_M = 10;

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
  // ── Car heading state ──────────────────────────────────────────────────────
  // The OSRM geometry kept in a form snapToRoute can consume, plus the segment
  // the car last snapped to (a continuity hint, so a nearby wrong-direction
  // segment can't win the nearest-distance search for one poll).
  const routeCoordsRef = useRef<TrackingLatLng[]>([]);
  const routeSegIndexRef = useRef<number | null>(null);
  // Monotonic ticket for in-flight OSRM requests — only the newest commits.
  const routeFetchSeqRef = useRef(0);
  const lastDriverPosRef = useRef<TrackingLatLng | null>(null);
  // Last world-space course applied, and the continuously-accumulated CSS
  // angle — kept un-normalised so a 350°→10° turn animates +20°, not −340°.
  const carBearingRef = useRef<number | null>(null);
  const carRotationRef = useRef(0);

  // ── Car glide (UX program W4.2) ────────────────────────────────────────────
  // The pose drawn right now (`shown`), the glide in progress (`from` → `to`,
  // started at `startedAt`, null when idle) and when the last update arrived
  // (for the util's stale-gap snap). Position and heading both move through
  // marker-interpolation (W4.1) on one requestAnimationFrame loop, so they
  // glide together and snap together (big jump, stale feed, Reduce Motion).
  const carMotionRef = useRef<{
    shown: MarkerPose; from: MarkerPose; to: MarkerPose; startedAt: number | null; updatedAt: number;
  } | null>(null);
  const carFrameRef = useRef<number | null>(null);

  // Point the car icon along `bearing` (world-space course, degrees).
  //
  // An AdvancedMarkerElement's content is ordinary DOM and the map does NOT
  // rotate it, so this is a SCREEN-space transform and the map's own heading
  // has to be subtracted — the same correction driver-app applies for Apple
  // Maps, and the reason visualRotationDegrees() exists in the shared util.
  // This map is north-up in practice (disableDefaultUI hides the rotate
  // control), but a two-finger rotate on a vector map still moves it, and a
  // transform that ignored that would be wrong by exactly the rotation angle.
  const applyCarRotation = useCallback((bearing: number | null | undefined) => {
    const marker = driverMarkerRef.current;
    if (!marker || bearing == null) return;
    const img: HTMLImageElement | null = marker.content?.querySelector?.('img') ?? null;
    if (!img) return;
    const rawHeading = mapRef.current?.getHeading?.();
    const mapHeading = typeof rawHeading === 'number' && Number.isFinite(rawHeading) ? rawHeading : 0;
    carRotationRef.current = shortestArcRotationTarget(
      carRotationRef.current,
      visualRotationDegrees(bearing, mapHeading),
    );
    img.style.transformOrigin = '50% 50%';
    // No CSS transition: the turn is stepped frame by frame by the glide
    // below, so it snaps with the position instead of tweening on its own.
    img.style.transform = `rotate(${carRotationRef.current}deg)`;
  }, []);

  const drawCar = useCallback((pose: MarkerPose) => {
    const marker = driverMarkerRef.current;
    if (!marker) return;
    marker.position = { lat: pose.lat, lng: pose.lng };
    applyCarRotation(pose.bearing);
  }, [applyCarRotation]);

  const stopCarMotion = useCallback(() => {
    if (carFrameRef.current != null) cancelAnimationFrame(carFrameRef.current);
    carFrameRef.current = null;
    carMotionRef.current = null;
  }, []);

  /** Move the car to `to`: glide from wherever it is drawn now, or draw it
   *  there directly when the util says snap (first placement, nothing moved,
   *  a jump over 500 m, a stale feed, Reduce Motion). */
  const moveCar = useCallback((to: MarkerPose) => {
    const now = performance.now();
    const prev = carMotionRef.current;
    // A glide already under way retargets from the pose drawn right now.
    const from = prev?.shown ?? to;
    const { done } = interpolateMarker(from, to, 0, MARKER_ANIMATION_MS, {
      reduceMotion: prefersReducedMotion(),
      gapMs: prev ? now - prev.updatedAt : undefined,
    });
    if (done) {
      if (carFrameRef.current != null) cancelAnimationFrame(carFrameRef.current);
      carFrameRef.current = null;
      carMotionRef.current = { shown: to, from: to, to, startedAt: null, updatedAt: now };
      drawCar(to);
      return;
    }
    carMotionRef.current = { shown: from, from, to, startedAt: now, updatedAt: now };
    if (carFrameRef.current != null) return; // the running loop picks up the new target
    const step = (t: number) => {
      carFrameRef.current = null;
      const m = carMotionRef.current;
      if (!m || m.startedAt == null) return;
      const { pose, done: arrived } = interpolateMarker(m.from, m.to, t - m.startedAt, MARKER_ANIMATION_MS);
      m.shown = pose;
      drawCar(pose);
      if (arrived) m.startedAt = null;
      else carFrameRef.current = requestAnimationFrame(step);
    };
    carFrameRef.current = requestAnimationFrame(step);
  }, [drawCar]);

  // No frame loop outlives the page.
  useEffect(() => stopCarMotion, [stopCarMotion]);

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
    const stopPolling = setVisibleInterval(fetchStatus, 5000);
    return () => { cancelled = true; stopPolling(); };
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

    // The car's rotation is screen-space (see applyCarRotation), so a camera
    // rotation has to re-derive it — otherwise a rider who twists the map
    // leaves the car pointing wrong until the next position update.
    mapRef.current.addListener?.('heading_changed', () => {
      applyCarRotation(carMotionRef.current?.shown.bearing);
    });
  }, [mapsReady, applyCarRotation]);

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
    // center (not its bottom edge) lands on the map position. Every marker on
    // this page wants that now: the car is a round puck, and the shared route
    // pin is a disc that marks the point itself (it replaced a teardrop, which
    // was the one thing here that needed the default bottom-center anchor).
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

    // Once the trip is over the car comes off the map even if a payload were
    // ever to carry a driver again — a parked car on an ended trip reads as live.
    const ended = ENDED_STATUSES.has(ride.status);
    const d = ended ? undefined : ride.driver;
    // Args: (ref, lat, lng, svgUrl, size, centerAnchor, zIndex).
    // centerAnchor MUST be the 6th arg (a boolean) — passing zIndex here
    // directly is a type error and breaks the Vercel build. The car is a
    // round puck so it centre-anchors on the driver's GPS position.
    // An EXISTING car is not moved here: moveCar() below glides it once this
    // fix's course is known. Setting .position here would teleport it first.
    if (!driverMarkerRef.current || d?.lat == null || d?.lng == null) {
      upsertMarker(driverMarkerRef, d?.lat, d?.lng, carSvg, 52, true, 2);
    }

    // ── Which way the car faces ────────────────────────────────────────────────
    // The car SVG above is drawn nose-up and nothing ever rotated it, so every
    // driver on this page pointed due north for the whole trip regardless of
    // travel direction — reported from a live trip on Jim Cairns Blvd, car
    // drawn facing north while the route ran east.
    //
    // The bearing is DERIVED here rather than read from the API, deliberately.
    // `drivers.heading` exists and is populated, but Android reports a
    // placeholder 0 on a fix that carries no bearing (see selectBearing's own
    // doc comment for the incident history) — shipping that here would trade
    // "always north" for "sometimes wrongly north". It would also mean adding
    // a field to a PUBLIC, unauthenticated share-token payload, which is a
    // privacy decision this rendering fix has no need to make.
    //
    // Priority mirrors the mobile marker: route segment → direction of travel
    // → hold the last known course. It never falls back to a default, because
    // "0" is the bug being fixed, not a safe neutral value.
    //
    // Computed here, ABOVE the reroute block that also uses them, because the
    // heading has to know the leg flipped before it reads a route belonging to
    // the previous one — see the legChanged branch below.
    const hasDriver = d?.lat != null && d?.lng != null;
    const currentLeg: 'pickup' | 'dropoff' = EN_ROUTE_TO_PICKUP.has(ride.status) ? 'pickup' : 'dropoff';
    const legChanged = lastRoutedLegRef.current !== currentLeg;

    if (!hasDriver) {
      // Driver released (offer timeout sets rides.driver_id = NULL, so the
      // payload returns driver: null and upsertMarker tore the icon down).
      // The course refs are about THAT driver — carrying them into whoever is
      // assigned next would measure a travel bearing between two unrelated
      // vehicles on the next poll. Reset with the marker.
      lastDriverPosRef.current = null;
      carBearingRef.current = null;
      routeSegIndexRef.current = null;
      carRotationRef.current = 0;
      routeCoordsRef.current = [];
      // Same for the glide: the next driver's first fix is placed exactly,
      // never slid in from where this one was.
      stopCarMotion();
    } else {
      const here: TrackingLatLng = { latitude: d!.lat!, longitude: d!.lng! };
      if (legChanged) {
        // The leg just flipped (driver_arrived → in_progress). The route still
        // in hand runs driver→pickup and the driver is sitting on its terminus,
        // so snapping would take the bearing of the ARRIVAL and could point the
        // car backwards for a whole poll cycle while it drives off toward the
        // dropoff. Drop it and let travel/hold cover the gap until the
        // dropoff-leg route lands below.
        routeCoordsRef.current = [];
        routeSegIndexRef.current = null;
      }
      const snap = snapToRoute(
        here, routeCoordsRef.current, MAX_ROUTE_SNAP_M, routeSegIndexRef.current,
      );
      routeSegIndexRef.current = snap?.segmentIndex ?? null;
      const prev = lastDriverPosRef.current;
      if (snap) {
        carBearingRef.current = snap.bearing;
      } else if (prev) {
        const moved = distanceMeters(prev.latitude, prev.longitude, here.latitude, here.longitude);
        if (moved >= MIN_TRAVEL_BEARING_MOVE_M) {
          carBearingRef.current = bearingDegrees(
            prev.latitude, prev.longitude, here.latitude, here.longitude,
          );
        }
      }
      lastDriverPosRef.current = here;
      moveCar({ lat: here.latitude, lng: here.longitude, bearing: carBearingRef.current });
    }

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

    // ── Trip over ──────────────────────────────────────────────────────────────
    // The pins and the car are already gone above (no coordinates). The route
    // line isn't tied to the payload, so clear it here, and void any OSRM
    // request still in flight so it can't draw one back afterwards.
    if (ended) {
      routeFetchSeqRef.current++;
      routePolylinesRef.current.forEach(l => l.setMap(null));
      routePolylinesRef.current = [];
    }

    // ── OSRM route: recalculate from driver's current position ─────────────────
    // Route origin = driver (when assigned) or pickup (no driver yet).
    // Route destination = pickup (driver en route to pickup) or dropoff (trip in progress).
    // Reroute when: we've never routed yet, OR the leg changed (e.g. the ride
    // flips driver_arrived → in_progress while the driver is stationary, so the
    // destination switches pickup → dropoff), OR the driver moved past the
    // threshold. The leg check fixes the "stuck showing driver→pickup" bug; not
    // re-fetching on an unchanged leg fixes the every-5s identical-request loop
    // when there is no driver (position-based reroute can't fire).
    // `hasDriver`/`currentLeg`/`legChanged` are computed above, with the heading.
    const driverMoved =
      hasDriver &&
      lastRoutedDriverRef.current != null &&
      (Math.abs(d!.lat! - lastRoutedDriverRef.current.lat) > ROUTE_REROUTE_THRESHOLD ||
        Math.abs(d!.lng! - lastRoutedDriverRef.current.lng) > ROUTE_REROUTE_THRESHOLD);
    const neverRouted = lastRoutedLegRef.current === null;
    // A driver appearing where there was none — first assignment, or a
    // REPLACEMENT after an offer timeout released the previous one. Without
    // this, none of the three conditions above can fire for the new driver
    // (lastRoutedDriverRef was nulled, so driverMoved is false; the leg and
    // the routed flag are unchanged), so the page kept drawing the released
    // driver's route line indefinitely. Pre-existing, but it now also starves
    // the heading, which reads that same geometry.
    const driverAppeared = hasDriver && lastRoutedDriverRef.current === null;

    if (!ended && (neverRouted || legChanged || driverMoved || driverAppeared) && ride.pickup_lat != null && ride.dropoff_lat != null) {
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

      // At road speed the move threshold trips on nearly every 5 s poll, so
      // several OSRM requests can be in flight at once against a public router
      // with no latency guarantee. Responses are not ordered, so a slow earlier
      // one could land last and overwrite newer geometry. That was survivable
      // while this only drew a line; it is not now that the same geometry
      // orients the car. Only the newest request may commit.
      const fetchSeq = ++routeFetchSeqRef.current;

      fetch(url)
        .then(r => r.ok ? r.json() : null)
        .then(data => {
          if (fetchSeq !== routeFetchSeqRef.current) return; // superseded
          const coords: [number, number][] | undefined = data?.routes?.[0]?.geometry?.coordinates;
          if (!coords || !mapRef.current) return;

          // Keep the geometry for the heading derivation above. OSRM is
          // [lng, lat]; snapToRoute works in {latitude, longitude}.
          routeCoordsRef.current = coords.map(([lng, lat]) => ({ latitude: lat, longitude: lng }));
          // A segment index only means anything against the array it came
          // from, and this route is re-anchored AT the driver on every
          // reroute, so carrying the old index forward would restrict the
          // search to segments already behind the car.
          routeSegIndexRef.current = null;
          // A freshly anchored route is the best heading evidence available —
          // re-snap now rather than waiting out the next 5 s poll, so the car
          // is already pointing correctly when the new line is drawn.
          const dp = lastDriverPosRef.current;
          if (dp) {
            const snapped = snapToRoute(dp, routeCoordsRef.current, MAX_ROUTE_SNAP_M, null);
            if (snapped) {
              routeSegIndexRef.current = snapped.segmentIndex;
              carBearingRef.current = snapped.bearing;
              moveCar({ lat: dp.latitude, lng: dp.longitude, bearing: snapped.bearing });
            }
          }

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
  }, [ride, mapsReady, moveCar, stopCarMotion]);

  const statusCfg    = STATUS_LABEL[ride?.status ?? ''] ?? STATUS_LABEL.searching;
  const isEnded      = !!ride?.status && ENDED_STATUSES.has(ride.status);
  const isActive     = !!ride?.status && !isEnded;
  const isArrived    = ride?.status === 'driver_arrived';
  // The ETA is to the drop-off, so it means nothing while the driver waits at
  // pickup; "arrived" replaces it rather than sitting next to it.
  const headline     = isArrived ? 'The driver has arrived' : statusCfg.label;
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
          <div className="animate-spin rounded-full h-10 w-10 border-2 border-border border-t-foreground mx-auto mb-4" />
          <p className="text-sm text-muted-foreground font-medium">Loading trip…</p>
        </div>
      </Centered>
    );
  }

  if (error || !ride) {
    return (
      <Centered>
        <div className="bg-card p-8 rounded-2xl shadow-sm max-w-sm w-full text-center border border-border">
          <h1 className="text-lg font-semibold text-foreground mb-1">Tracking unavailable</h1>
          <p className="text-sm text-muted-foreground">{error || 'This link may have expired or the ride has ended.'}</p>
        </div>
      </Centered>
    );
  }

  return (
    <div className="min-h-screen bg-background text-foreground flex flex-col" style={TRACK_LIGHT_TOKENS}>
      {/* Map — explicit height so the canvas always has dimensions on first paint */}
      <div className="relative w-full" style={{ height: '60vh', minHeight: 320 }}>
        <div ref={mapContainerRef} className="absolute inset-0 bg-muted" />

        {GMAPS_KEY ? (
          <Script
            src={`https://maps.googleapis.com/maps/api/js?key=${GMAPS_KEY}&libraries=marker&v=weekly`}
            strategy="afterInteractive"
            onLoad={() => setMapsReady(true)}
          />
        ) : (
          <div className="absolute inset-0 flex items-center justify-center bg-muted text-muted-foreground text-sm px-4 text-center">
            Map unavailable — set <code className="mx-1">NEXT_PUBLIC_GOOGLE_MAPS_API_KEY</code> in Vercel
          </div>
        )}

        {isEnded ? (
          // Trip over: say so over the (now empty) map instead of leaving a
          // map that looks like it's still waiting for the car.
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-1 bg-background/90 px-6 text-center">
            <p className="text-lg font-semibold text-foreground">{statusCfg.label}</p>
            <p className="text-sm text-muted-foreground">Live location is no longer shared.</p>
          </div>
        ) : (
          // Status pill
          <div className="absolute top-4 left-4 right-4 mx-auto max-w-md flex items-center gap-2 px-3 py-2 rounded-full shadow-sm bg-card/95 backdrop-blur">
            <span className={`inline-block w-2 h-2 rounded-full ${statusCfg.dot}`} />
            <span className="text-xs font-semibold text-foreground tracking-wide">
              {statusCfg.label.toUpperCase()}
            </span>
            {isActive && !isArrived && ride.eta_minutes != null && (
              <span className="ml-auto text-xs font-medium text-muted-foreground">
                ETA {ride.eta_minutes} min
              </span>
            )}
          </div>
        )}
      </div>

      {/* Bottom sheet */}
      <div className="bg-card rounded-t-3xl -mt-6 shadow-[0_-8px_30px_rgba(0,0,0,0.06)] relative">
        <div className="mx-auto w-12 h-1.5 bg-border rounded-full mt-3" />

        <div className="px-5 pt-5 pb-4">
          {/* Announces each status change (e.g. arrived, trip ended) to screen readers. */}
          <p className="sr-only" role="status">{headline}</p>
          {isArrived ? (
            <div className="text-xl font-semibold text-foreground">{headline}</div>
          ) : ride.eta_minutes != null && isActive ? (
            <div className="flex items-baseline gap-2">
              <span className="text-3xl font-semibold text-foreground tracking-tight">{ride.eta_minutes}</span>
              <span className="text-sm text-muted-foreground font-medium">min away</span>
            </div>
          ) : (
            <div className="text-base font-semibold text-foreground">{statusCfg.label}</div>
          )}
          {ride.message && <p className="text-sm text-muted-foreground mt-1">{ride.message}</p>}
        </div>

        {/* Driver card */}
        {ride.driver && (
          <div className="mx-5 mb-5 rounded-2xl border border-border bg-muted/50 p-4 flex items-center gap-4">
            <div className="relative">
              {ride.driver.photo_url ? (
                <img src={ride.driver.photo_url} alt={driverName} className="w-12 h-12 rounded-full object-cover" />
              ) : (
                <div className="w-12 h-12 rounded-full bg-foreground text-background flex items-center justify-center font-semibold">
                  {driverInitial}
                </div>
              )}
              {typeof ride.driver.rating === 'number' && (
                <span className="absolute -bottom-1 -right-1 text-[10px] font-semibold px-1.5 py-0.5 rounded-full bg-card border border-border text-foreground">
                  ★ {ride.driver.rating.toFixed(1)}
                </span>
              )}
            </div>
            <div className="flex-1 min-w-0">
              <div className="text-sm font-semibold text-foreground truncate">{driverName}</div>
              {vehicleLine && <div className="text-xs text-muted-foreground truncate mt-0.5">{vehicleLine}</div>}
            </div>
            {ride.driver.license_plate && (
              <div className="px-2.5 py-1 rounded-md bg-card border border-border text-xs font-mono font-semibold text-foreground tracking-wider">
                {ride.driver.license_plate}
              </div>
            )}
          </div>
        )}

        {/* Route */}
        <div className="mx-5 mb-5 rounded-2xl border border-border p-4">
          <div className="flex gap-3 relative before:absolute before:top-2 before:bottom-2 before:left-[7px] before:w-0.5 before:bg-border">
            <div className="flex flex-col items-center pt-1">
              {/* Same fills as the map's pins (shared route pin spec). */}
              <span className="w-4 h-4 rounded-full border-2 border-card shadow-sm z-10" style={{ backgroundColor: ROUTE_PIN_COLORS.pickup }} />
              <span className="flex-1" />
              <span className="w-4 h-4 rounded-sm border-2 border-card shadow-sm z-10" style={{ backgroundColor: ROUTE_PIN_COLORS.dropoff }} />
            </div>
            <div className="flex-1 space-y-3">
              <div>
                <div className="text-[10px] font-semibold text-muted-foreground tracking-wider uppercase">Pickup</div>
                <div className="text-sm text-foreground mt-0.5">{ride.pickup_address}</div>
              </div>
              <div>
                <div className="text-[10px] font-semibold text-muted-foreground tracking-wider uppercase">Drop-off</div>
                <div className="text-sm text-foreground mt-0.5">{ride.dropoff_address}</div>
              </div>
            </div>
          </div>
        </div>

        <div className="px-5 pb-4 flex items-center justify-between text-[11px] text-muted-foreground">
          <span>{ride.ride_code ? `Ref ${ride.ride_code}` : ''}</span>
          {isActive && lastUpdated && (
            <span>Updated {lastUpdated.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit', second: '2-digit' })}</span>
          )}
        </div>

        <div className="py-3 text-center text-[11px] text-muted-foreground border-t border-border">
          Live tracking · <span className="font-semibold">Spinr</span>
        </div>
      </div>
    </div>
  );
}

function Centered({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-center min-h-screen bg-background text-foreground p-4" style={TRACK_LIGHT_TOKENS}>
      {children}
    </div>
  );
}
