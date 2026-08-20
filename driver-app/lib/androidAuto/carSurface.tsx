/**
 * The branded in-dash map drawn onto the car head-unit surface.
 *
 * iternio's MapTemplate renders this React component onto the Android Auto map
 * surface. It ALWAYS shows a live map centered on the driver (a car marker at the
 * phone's current location), so the car screen is useful even when idle — not a
 * blank status pane. Zoom is driven by the template's map buttons via
 * `useCarMapCamera` (the surface is non-interactive: Android Auto forbids
 * in-surface touch and routes interaction through buttons).
 *
 * During a ride it additionally draws the route we ALREADY stored at ride
 * creation (`rides.planned_route_polyline`) with the destination pinned — so
 * there is no live Routes/Directions API spend (turn-by-turn is handed off to the
 * driver's Google Maps / Waze; see carRoute.ts + register.ts).
 *
 * Reads the SAME useDriverStore the phone uses (single source of truth).
 * react-native-maps' native view is absent in Expo Go / web / tests, so it is
 * lazy-required and the component degrades to null instead of crashing.
 *
 * UNPROVEN ON HARDWARE: validated at the JS level only. The on-surface render
 * must still be confirmed on an EAS dev build + Android Auto DHU.
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Platform, StyleSheet, Text, View } from 'react-native';
import { useDriverStore } from '../../store/driverStore';
import { useDemandHeatmapView } from '../../hooks/demandHeatmapShared';
import type { HeatmapCell } from '../../hooks/useDemandHeatmap';
import { selectCarRoute } from './carRoute';
import { buildTripCard, type OfferLike } from './carCard';
import { CarTripCard } from './CarTripCard';
import { CarOfferPanel } from './CarOfferPanel';
import { useCarMapCamera } from './carMapCamera';
import { normalizeHeading, shouldCommitHeading, zoomForSpan } from './carCameraMath';
import { useCarLocation } from './useCarLocation';
import { useCarLiveRoute } from './useCarLiveRoute';
import { displayEarnings, useCarEarningsPrivacy } from './carEarningsPrivacy';
import { pushDebug, setDebugFact } from './carDebug';
import { CarDebugPanel } from './CarDebugPanel';
import { useCarSurfaceGeneration } from './carSurfaceGeneration';
import {
  NIGHT_MAP_STYLE,
  setCarColorScheme,
  useCarColorScheme,
  type CarColorScheme,
} from './carColorScheme';
import { carColors } from './carTheme';
// RouteLine / RoutePins hard-import react-native-maps, so they are lazy-required
// AFTER the maps guard below (never at module scope) — otherwise loading this
// file in a maps-less context (web / Expo Go / tests) would crash before the
// guarded require, defeating the graceful null fallback.

// Saskatoon — Spinr is Saskatchewan-first. Used only until the first fix /
// last-known location loads, so the idle map never opens on null-island (0,0).
const FALLBACK_CENTER = { latitude: 52.1332, longitude: -106.67 };

/**
 * Camera glide, in ms. Comfortably under the ~2s fix cadence (TRIP_CADENCE in
 * useCarLocation) so each move settles before the next one starts, and long
 * enough that a turn sweeps rather than snaps. The camera used to jump on every
 * fix because react-native-maps' `region` prop moves without animating.
 */
const CAMERA_ANIM_MS = 700;

// Inlined at build time by Expo. Empty here means the AAB was built without the
// var set in its EAS environment, which lands an empty apiKey in the merged
// AndroidManifest — Google Maps then draws a blank white canvas with no error
// anywhere in JS. shared/components/AppMap.tsx guards on this for the phone;
// the car surface needs the same guard, because a white void on a head unit is
// indistinguishable from "the integration is broken".
const GOOGLE_MAPS_API_KEY = process.env.EXPO_PUBLIC_GOOGLE_MAPS_API_KEY || '';

// Heatmap cell geometry. Fallbacks only — the server sends the grid size it
// actually bucketed with, because cell size is tunable per service area. Same
// defaults as HeatmapCells.tsx, used when talking to a backend that omits them.
const DEFAULT_CELL_LAT = 0.004;
const DEFAULT_CELL_LNG = 0.006;
const CAR_MAX_POLYGONS = 80;
// Dark-theme ramp (car surface is always dark)
const CAR_RAMP = ['#4E211E', '#7F2D26', '#B2382E', '#FF453A', '#FF8A80'];

function cellCorners(lat: number, lng: number, cellLat: number, cellLng: number) {
  const bLat = Math.floor(lat / cellLat) * cellLat;
  const bLng = Math.floor(lng / cellLng) * cellLng;
  return [
    { latitude: bLat, longitude: bLng },
    { latitude: bLat + cellLat, longitude: bLng },
    { latitude: bLat + cellLat, longitude: bLng + cellLng },
    { latitude: bLat, longitude: bLng + cellLng },
  ];
}

function rampColor(weight: number, max: number): { fill: string; stroke: string; sw: number } {
  const r = max > 0 ? weight / max : 0;
  const idx = r < 0.2 ? 0 : r < 0.4 ? 1 : r < 0.6 ? 2 : r < 0.8 ? 3 : 4;
  const hex = CAR_RAMP[idx];
  const [rv, gv, bv] = [parseInt(hex.slice(1, 3), 16), parseInt(hex.slice(3, 5), 16), parseInt(hex.slice(5, 7), 16)];
  return {
    fill: `rgba(${rv},${gv},${bv},0.4)`,
    stroke: idx === 4 ? `rgba(${rv},${gv},${bv},0.7)` : 'transparent',
    sw: idx === 4 ? 1 : 0,
  };
}

/**
 * Props the car host hands the React root. iternio's VirtualRenderer.kt:248 puts
 * `colorScheme` in the initial-properties bundle and MapTemplate.ts:199 passes
 * props straight through to this component — we simply never read them before.
 */
export function CarMapSurface({ colorScheme }: { colorScheme?: CarColorScheme } = {}):
  React.ReactElement | null {
  // Hooks first — unconditional, before any early return (rules-of-hooks).
  const rideState = useDriverStore((s) => s.rideState);
  const activeRide = useDriverStore((s) => s.activeRide);
  const incomingRide = useDriverStore((s) => s.incomingRide);
  const delta = useCarMapCamera((s) => s.delta);
  const offsetLat = useCarMapCamera((s) => s.offsetLat);
  const offsetLng = useCarMapCamera((s) => s.offsetLng);
  // Bumped by register.ts shortly after connect. Folded into the MapView key so
  // a map that came up empty on a cold launch remounts once everything is
  // loaded, instead of the driver having to unplug and replug.
  const surfaceGeneration = useCarSurfaceGeneration((s) => s.generation);
  const here = useCarLocation();
  // Seed the store from the initial prop, then let register.ts's
  // onAppearanceDidChange drive it for the rest of the session. Without the
  // seed the first render guesses; without the store a driver who set off in
  // daylight keeps a white map at 10pm.
  const scheme = useCarColorScheme((s) => s.scheme);
  useEffect(() => {
    if (colorScheme) setCarColorScheme(colorScheme);
  }, [colorScheme]);
  const isNight = scheme === 'dark';
  const route = selectCarRoute(rideState, activeRide);
  // Cumulative earnings for the completed-trip card. Fetched by the phone, so
  // a car-only cold launch legitimately has none — buildTripCard omits the line
  // rather than showing a misleading zero.
  const earningsSummary = useDriverStore((s) => s.earnings);

  // Fetch it ourselves. `fetchEarnings` is otherwise called ONLY from the phone's
  // Activity tab (components/activity/ActivityView.tsx), so `earnings` stayed
  // null for any driver who had not opened that tab — and always on a car-only
  // cold launch, where no phone screen mounts at all. The pill and the
  // completed-trip line were therefore invisible in exactly the session they
  // were built for.
  //
  // Unlike the demand heatmap there is no phone-side publisher to subscribe to
  // here, so the car has to be the fetcher. Once on mount, then again whenever a
  // trip completes — that is the only moment the day's total actually changes.
  React.useEffect(() => {
    if (rideState !== 'idle' && rideState !== 'trip_completed') return;
    const fetchEarnings = useDriverStore.getState().fetchEarnings;
    if (typeof fetchEarnings !== 'function') return;
    fetchEarnings('day').catch((e) => {
      // Non-fatal: the pill simply stays hidden. Recorded so a persistently
      // missing pill is diagnosable from the car rather than a mystery.
      pushDebug('error', 'earnings fetch failed:', e);
    });
  }, [rideState]);
  const earningsCtx = useMemo(
    () =>
      earningsSummary
        ? { total: earningsSummary.total_earnings, rides: earningsSummary.total_rides }
        : null,
    [earningsSummary],
  );
  const card = buildTripCard(rideState, activeRide, incomingRide as OfferLike | null, earningsCtx);
  // Money only — the pill is glanceable chrome, so the ride count that the
  // completed-trip card carries would be noise here.
  //
  // Shown at $0.00 too. An earlier version hid the pill below a positive total,
  // on the theory that a zero reads as "you have made nothing" — but that was
  // wrong twice over. At the start of a shift $0.00 is simply the truth, and it
  // is what Uber and Lyft both show; and hiding the pill makes "no earnings yet"
  // indistinguishable from "this feature is broken", which is exactly how it was
  // first reported. Only a genuinely absent summary hides it now.
  const earningsHidden = useCarEarningsPrivacy((s) => s.hidden);
  const todayEarnings = useMemo(() => {
    if (!earningsCtx) return null;
    const total = Number(earningsCtx.total);
    return Number.isFinite(total) && total >= 0 ? `$${total.toFixed(2)}` : null;
  }, [earningsCtx]);
  // Read-only view of the phone's poller — NOT a second useDemandHeatmap().
  //
  // Two independent instances meant two timers (double the requests, battery
  // and data whenever a driver plugged in) and, worse, two different answers to
  // "am I online?": this surface read authStore.driver.is_online while the
  // phone dashboard used useDriverDashboard's own state. A driver taken offline
  // by dispatch stopped seeing demand on the phone and kept seeing it here.
  //
  // `status` is consumed too: with no active publisher, or once the data has
  // gone stale, the car renders nothing rather than a frozen map the driver has
  // no way to tell is frozen.
  const { cells: heatmapCells, status: heatmapStatus, cellLatDeg, cellLngDeg } = useDemandHeatmapView();

  // The surface has no dev menu, no red box and no Metro console, so these are
  // the only signal that distinguishes "map never initialised" from "map
  // initialised and drew nothing" (the classic symptom of an API key that is
  // absent, or restricted to a signing certificate this build wasn't signed
  // with — Play App Signing re-signs the AAB, so a key pinned to the upload
  // key's SHA-1 is rejected on a Play-installed build while sideloaded APKs
  // keep working). Read with: adb logcat -s ReactNativeJS:V
  // ─── Heading-up camera ─────────────────────────────────────────────────────
  //
  // The map used to be locked north-up while the CAR MARKER spun, which is the
  // opposite of what every driver expects from Google Maps: there the map turns
  // and the car stays pointing up the screen. Reported from a real head unit as
  // "the map is in fixed layout and car keeps rotating up and down".
  //
  // This cannot be done through the `region` prop. On Android that compiles to
  // `newLatLngBounds`, which resets camera bearing to zero on every update — so
  // `region` and a rotation can never coexist. The camera is therefore driven
  // imperatively from here, and `region` downgraded to `initialRegion`.
  // Inline type-only import: fully erased at compile time, so it cannot defeat
  // the lazy `require` below that keeps this file loadable without native maps.
  const mapRef = useRef<import('react-native-maps').default | null>(null);
  // Bumped by onMapReady. A remount (the MapView is keyed on the trip leg) needs
  // the camera re-applied, and only a dependency change will do that — hence a
  // counter in state rather than a ref.
  const [mapReadyTick, setMapReadyTick] = useState(0);
  const setMapRef = useCallback((m: typeof mapRef.current) => {
    mapRef.current = m;
  }, []);
  // Surface size in dp, needed to turn a lat/lng span into a Google zoom level.
  // Zero until first layout; zoomForSpan returns null on that, and the camera
  // then keeps whatever framing initialRegion established.
  const [viewport, setViewport] = useState({ w: 0, h: 0 });
  const onMapLayout = useCallback((e: { nativeEvent: { layout: { width: number; height: number } } }) => {
    const { width, height } = e.nativeEvent.layout;
    setViewport((p) => (p.w === width && p.h === height ? p : { w: width, h: height }));
  }, []);

  // The bearing the camera is actually committed to — deliberately NOT every
  // raw fix. GPS course is noisy, and a map that twitches a degree at a time on
  // a straight road reads as broken; shouldCommitHeading holds until a real
  // turn. Null until the first fix carries a course, and the map stays north-up
  // until then rather than guessing a direction.
  const [cameraHeading, setCameraHeading] = useState<number | null>(null);
  const rawHeading = here?.heading ?? null;
  // Rotation freezes while the driver has dragged the map away from themselves:
  // turning a deliberately-offset view under them is disorienting. Holding the
  // last bearing (rather than snapping back to north, which is what this did
  // before) means Recenter resumes tracking without a spin.
  // Live OSRM route from the driver's CURRENT position, replacing the static
  // booking-time planned line once it arrives. Null when there is no
  // trustworthy one (wrong ride, wrong leg, or too old) and the planned line
  // stands in. See useCarLiveRoute for why the car may poll for itself.
  const liveRoute = useCarLiveRoute(activeRide?.ride?.id ?? null, route?.leg ?? null, !!route);
  const livePath = liveRoute?.polyline ?? null;

  const isPannedAway = offsetLat !== 0 || offsetLng !== 0;
  // Adjusted during render rather than in an effect — React's documented
  // "adjusting state when a prop changes" pattern, the same one CarMarker.tsx
  // uses for its image-failure reset. An effect here would be a cascading
  // render per GPS fix (and trips react-hooks/set-state-in-effect); this
  // re-renders only when the bearing actually clears the jitter threshold.
  const [prevRawHeading, setPrevRawHeading] = useState<number | null>(rawHeading);
  if (rawHeading !== prevRawHeading) {
    setPrevRawHeading(rawHeading);
    if (!isPannedAway) {
      const next = normalizeHeading(rawHeading);
      if (shouldCommitHeading(cameraHeading, next)) setCameraHeading(next);
    }
  }

  const followTarget = here ?? route?.destination ?? FALLBACK_CENTER;
  // The pan offset rides on top: while it is non-zero the driver has dragged the
  // map, so the view stays where they put it instead of being yanked back by the
  // next GPS fix. The Recenter map button clears it.
  const centerLat = followTarget.latitude + offsetLat;
  const centerLng = followTarget.longitude + offsetLng;

  useEffect(() => {
    const map = mapRef.current;
    if (!map?.animateCamera || mapReadyTick === 0) return;
    const camera: Record<string, unknown> = {
      center: { latitude: centerLat, longitude: centerLng },
      // 0 is north-up, which is exactly the right fallback before any course
      // has been observed — and the behaviour this surface had until now.
      heading: cameraHeading ?? 0,
    };
    // Omitted rather than guessed before layout: animateCamera leaves a field it
    // isn't given alone, so skipping `zoom` preserves the current framing
    // instead of snapping the driver to some default.
    const zoom = zoomForSpan(delta, viewport.w, viewport.h, centerLat);
    if (zoom !== null) camera.zoom = zoom;
    try {
      map.animateCamera(camera, { duration: CAMERA_ANIM_MS });
    } catch (e) {
      // Loud-ish: a throw here freezes the camera, which looks exactly like a
      // dead GPS. Never fatal though — the map still renders where it was.
      pushDebug('error', `animateCamera failed: ${String(e)}`);
    }
  }, [centerLat, centerLng, cameraHeading, delta, viewport.w, viewport.h, mapReadyTick]);

  const onMapReady = useCallback(() => {
    console.log('[CarSurface] MapView ready (native view attached)');
    pushDebug('info', 'MapView onMapReady — native view attached');
    setDebugFact('map', 'ready (no tiles yet)');
    setMapReadyTick((n) => n + 1);
  }, []);
  const onMapLoaded = useCallback(() => {
    console.log('[CarSurface] MapView finished rendering tiles');
    pushDebug('info', 'MapView onMapLoaded — tiles rendered');
    setDebugFact('map', 'TILES RENDERED');
  }, []);

  const carHeatCells = useMemo(() => {
    const usable = heatmapStatus === 'ready' || heatmapStatus === 'empty';
    if (!usable || !heatmapCells.length || rideState !== 'idle') return [];
    return [...heatmapCells]
      .filter((c) => Number.isFinite(c.lat) && Number.isFinite(c.lng) && Number.isFinite(c.weight))
      .sort((a, b) => b.weight - a.weight)
      .slice(0, CAR_MAX_POLYGONS);
  }, [heatmapCells, heatmapStatus, rideState]);

  const carCellLat = typeof cellLatDeg === 'number' && cellLatDeg > 0 ? cellLatDeg : DEFAULT_CELL_LAT;
  const carCellLng = typeof cellLngDeg === 'number' && cellLngDeg > 0 ? cellLngDeg : DEFAULT_CELL_LNG;

  const carHeatMax = useMemo(
    () => carHeatCells.reduce((m, c) => Math.max(m, c.weight), 0),
    [carHeatCells],
  );

  // Point-in-time state for the debug panel. Effect (not render body) so a
  // render never mutates the store; setFact no-ops on an unchanged value, so
  // this cannot loop.
  React.useEffect(() => {
    setDebugFact('surface', 'rendering');
    // Labelled (js) deliberately: this is the bundle-inlined copy, NOT the
    // AndroidManifest key the Maps SDK actually authenticates with. Empty here
    // means the bundle was built without an EAS environment (an OTA missing
    // --environment), which says nothing about the installed binary.
    setDebugFact('mapsKey(js)', GOOGLE_MAPS_API_KEY ? `present (${GOOGLE_MAPS_API_KEY.length} ch)` : 'empty — OTA env?');
    setDebugFact('renderer', 'full (liteMode off)');
    setDebugFact(
      'earnings',
      todayEarnings
        ? `today ${earningsHidden ? 'masked' : 'shown'}`
        : 'none yet (pill hidden)',
    );
    // expo-location reports -1 for "heading unknown" and null when the provider
    // gives no bearing at all. Either way CarMarker falls back to a bearing
    // derived from consecutive fixes, so this tells you WHICH path is driving
    // the marker's rotation rather than leaving it to guesswork.
    setDebugFact(
      'heading',
      here == null
        ? 'no fix'
        : here.heading == null
          ? 'null → derived from travel'
          : here.heading < 0
            ? `${here.heading} (unknown) → derived`
            : `${here.heading.toFixed(0)}° from GPS`,
    );
    setDebugFact('rideState', String(rideState));
    setDebugFact('leg', card.leg);
    const z = zoomForSpan(delta, viewport.w, viewport.h, centerLat);
    setDebugFact(
      'zoomDelta',
      `${delta.toFixed(4)} → ${z === null ? 'no layout yet' : `z${z.toFixed(1)}`}`,
    );
    // Distinguishes the three states that all look like "the map won't turn":
    // no course from the sensor, a course held but the driver has panned, and a
    // live committed bearing.
    setDebugFact(
      'mapHeading',
      isPannedAway
        ? `${cameraHeading?.toFixed(0) ?? '—'}° frozen (panned)`
        : cameraHeading === null
          ? 'north-up (no course yet)'
          : `${cameraHeading.toFixed(0)}° course-up`,
    );
    setDebugFact(
      'pan',
      offsetLat === 0 && offsetLng === 0
        ? 'following driver'
        : `${offsetLat.toFixed(4)}, ${offsetLng.toFixed(4)} (recenter to clear)`,
    );
    setDebugFact('location', here ? `${here.latitude.toFixed(4)}, ${here.longitude.toFixed(4)}` : 'no fix (fallback)');
    // Says WHICH line is on screen. "planned" on a moving trip is the symptom
    // of the live poll failing, and was previously indistinguishable from
    // working correctly.
    setDebugFact(
      'route',
      route
        ? livePath
          ? `${route.leg}, ${livePath.length} pts (live OSRM)`
          : `${route.leg}, ${route.polyline.length} pts (planned, booking-time)`
        : 'none',
    );
    setDebugFact('heatmap', `${heatmapStatus}, ${carHeatCells.length} cells`);
  }, [rideState, card.leg, delta, here, route, heatmapStatus, carHeatCells.length, offsetLat, offsetLng, todayEarnings, earningsHidden, cameraHeading, isPannedAway, viewport.w, viewport.h, centerLat, livePath]);

  let Maps: typeof import('react-native-maps') | null = null;
  try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    Maps = require('react-native-maps');
  } catch {
    Maps = null;
  }
  if (!Maps) return null;

  const MapView = Maps.default;
  const MapsPolygon = Maps.Polygon;

  // NOTE: this JS value does NOT decide whether the native map can render, and
  // must never gate it.
  //
  // Two different keys are in play. The Maps SDK reads its key from the merged
  // AndroidManifest, written at BUILD time from app.config.ts:191. This
  // constant is the same variable inlined into the JS bundle — and `eas update`
  // rebuilds that bundle, so an OTA published without --environment ships it
  // empty while the installed binary's manifest key is perfectly intact.
  //
  // An earlier version of this file returned a "Map unavailable" screen here.
  // That was wrong: on an env-less OTA it replaced a working map with an error,
  // and masked whatever the real fault was. Warn, record, render anyway — the
  // manifest is the authority.
  if (Platform.OS === 'android' && !GOOGLE_MAPS_API_KEY) {
    console.warn(
      '[CarSurface] EXPO_PUBLIC_GOOGLE_MAPS_API_KEY is empty in the JS bundle. ' +
      'Expected on an OTA published without --environment; the native manifest ' +
      'key is unaffected, so the map is still attempted.'
    );
    pushDebug('info', 'JS bundle has no maps key (native manifest key may still be set)');
  }

  // CarMarker hard-imports react-native-maps; require it only after the maps
  // guard above so it can never crash a maps-less context (web / Expo Go).
  let CarMarker: React.ComponentType<{
    coordinate: { latitude: number; longitude: number };
    heading?: number | null;
  }> | null = null;
  try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    CarMarker = require('../../components/CarMarker').CarMarker;
  } catch {
    CarMarker = null;
  }

  // Same guard for the shared route renderers (they hard-import react-native-maps).
  let RouteLine: typeof import('@shared/components/RouteLine').RouteLine | null = null;
  let RoutePins: typeof import('@shared/components/RoutePins').RoutePins | null = null;
  try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    RouteLine = require('@shared/components/RouteLine').RouteLine;
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    RoutePins = require('@shared/components/RoutePins').RoutePins;
  } catch {
    RouteLine = null;
    RoutePins = null;
  }

  // `followTarget`, `centerLat`, `centerLng`, `cameraHeading` and the effect
  // that drives them all live ABOVE the maps guard — the camera effect is a
  // hook and cannot sit after an early return.

  return (
    <View style={[styles.fill, styles.mapBackdrop]}>
      <MapView
        // Re-mount on a leg / idle transition so Android's Google Maps native
        // layer fully drops a leftover route overlay; within a leg the camera is
        // driven by the `camera` prop (below), not a remount, so live location,
        // zoom, and heading updates don't thrash the surface.
        key={`${route ? route.leg : 'idle'}-${surfaceGeneration}`}
        ref={setMapRef}
        style={styles.fill}
        onLayout={onMapLayout}
        // Match shared/components/AppMap.tsx rather than relying on the
        // platform default, so the car surface and the phone resolve the
        // provider identically.
        provider={Platform.OS === 'android' ? Maps.PROVIDER_GOOGLE : undefined}
        // NO liteMode. It was briefly enabled here on the theory that a GL
        // SurfaceView could not composite onto the car's VirtualDisplay. That
        // was a misdiagnosis: the blank map was an empty Maps API key (the OTA
        // that carried this code was published without --environment, so every
        // EXPO_PUBLIC_* value inlined empty). Once the key was restored the map
        // rendered, and the on-surface debug panel had already shown
        // `surface: rendering` — proving iternio's Presentation hosts React
        // fine.
        //
        // Lite mode is a static bitmap that "cannot be tilted or rotated at
        // all", and it no-ops animateMarkerToCoordinate (CarMarker.tsx:175). It
        // pinned the car marker to north and killed the glide between fixes.
        // Full mode is the correct renderer here.
        onMapReady={onMapReady}
        onMapLoaded={onMapLoaded}
        // The projected car surface is non-interactive (Android Auto drives
        // interaction through template buttons, not in-surface touches); the
        // controlled camera lets the zoom buttons + follow-me drive position,
        // zoom, and heading.
        pointerEvents="none"
        showsUserLocation={false}
        // Android Auto flips this at dusk from the car's own ambient state. A
        // daylight map at night on a dashboard is genuinely dangerous, not just
        // ugly — it is the brightest object in the cabin and it is at eye level.
        customMapStyle={isNight ? NIGHT_MAP_STYLE : undefined}
        // INITIAL only — every subsequent move goes through animateCamera in the
        // effect above.
        //
        // A controlled `camera` prop was tried first (f8983e2) and is the reason
        // rotation still looked broken on hardware: react-native-maps compiles it
        // to `map.moveCamera`, which SNAPS. Position jumped every ~2s and the
        // bearing jumped with it, so a turn arrived as a lurch rather than a
        // sweep. animateCamera is the same camera, interpolated.
        initialRegion={{
          latitude: centerLat,
          longitude: centerLng,
          latitudeDelta: delta,
          longitudeDelta: delta,
        }}
      >
        {/* Heatmap demand cells — idle state only (HM-30) */}
        {carHeatCells.map((c, i) => {
          const rc = rampColor(c.weight, carHeatMax);
          return (
            <MapsPolygon
              key={`ch-${i}-${c.lat}-${c.lng}`}
              coordinates={cellCorners(c.lat, c.lng, carCellLat, carCellLng)}
              fillColor={rc.fill}
              strokeColor={rc.stroke}
              strokeWidth={rc.sw}
            />
          );
        })}

        {/* THE uniform route line + pins, drawn as one orange→red gradient via
            the shared RouteLine so the car reads identically to the phone.

            The line PREFERS the live OSRM route — road-matched from where the
            driver actually is, refreshed every 20s — and falls back to the
            stored `planned_route_polyline` only when there is no trustworthy
            live one. Until now only the stored line existed, so a driver who
            took a different road watched the head unit insist on the route
            planned at booking for the whole trip.

            The live line also fills a gap the stored one could never cover: it
            exists on the PRE-PICKUP leg too (`route.polyline` is empty there,
            because the stored geometry runs pickup→dropoff and drawing it while
            the driver heads to the rider pointed the wrong way entirely).

            Pins follow the leg: green pickup while heading to the rider; green
            pickup (route start) + red dropoff once the trip is under way. */}
        {route && RouteLine && (livePath ?? route.polyline).length > 1 && (
          <RouteLine path={livePath ?? route.polyline} />
        )}
        {route && RoutePins && (
          <RoutePins
            pickup={route.leg === 'pickup' ? route.destination : (route.polyline[0] ?? null)}
            dropoff={route.leg === 'dropoff' ? route.destination : null}
          />
        )}
        {here && CarMarker && (
          <CarMarker
            coordinate={{ latitude: here.latitude, longitude: here.longitude }}
            // The camera's committed bearing, NOT the raw fix.
            //
            // CarMarker is `flat`, so react-native-maps applies its rotation
            // relative to the MAP, not the screen — the icon's on-screen angle
            // is therefore (marker heading − camera bearing). Feeding it the
            // same value the camera is using makes that zero, so the car sits
            // still pointing up while the world turns underneath, which is what
            // Google Maps does and what "the car keeps rotating up and down"
            // was asking for. Falls back to the raw bearing when no course has
            // been observed yet: the map is north-up then, so the marker must
            // show the true direction itself (and CarMarker derives one from
            // travel when even that is missing).
            heading={cameraHeading ?? here.heading}
          />
        )}
      </MapView>
      {/* The offer moment gets its own, much richer panel — see
          CarOfferPanel.tsx for why it is not the slim bar below.

          Short version: the alert is system chrome, so the one number a driver
          is deciding on arrives there as ordinary body text. This panel renders
          it at hero scale with the rate, the badges and both ends of the trip.
          It repeats neither button, so the alert still unambiguously owns the
          Accept/Decline decision — the overlap problem that got the slim bar
          hidden here in the first place was two panels competing to be acted
          on, not two panels on screen. */}
      {card.leg === 'offer' && <CarOfferPanel card={card} />}
      {/* Slim status bar for every engaged leg (display-only; interaction is via
          template header actions / map buttons). Hidden when `idle` — nothing to
          say, so the screen stays an uncluttered map. */}
      {card.leg !== 'idle' && card.leg !== 'offer' && <CarTripCard card={card} />}
      {/* Always-on status pill. Two jobs:
          - UX: the idle car screen is otherwise a bare map with no indication
            the app is alive or connected, which is what Uber/Lyft put here.
          - Diagnosis: it renders independently of react-native-maps, so a blank
            map with a visible pill means the React surface is painting and the
            fault is the map alone, while an entirely blank screen means the
            root itself never rendered. That distinction previously needed a
            throwaway build to establish. */}
      <View style={styles.pillRow} pointerEvents="none">
        <View style={styles.statusPill}>
          <View style={[styles.statusDot, { backgroundColor: card.accent }]} />
          <Text style={styles.statusText}>
            {card.leg === 'idle' ? 'Spinr Driver · Ready' : card.statusLabel}
          </Text>
        </View>
        {/* Earnings pill. Persistent, not just on the completed-trip card: a
            driver should be able to glance at the dashboard mid-shift and see
            the day adding up, which is the whole point of putting earnings on
            this screen. Hidden when the store has no summary (a car-only cold
            launch never ran the phone's fetch) rather than showing $0.00 — a
            zero on the earnings pill would read as "you've made nothing".

            MASKED BY DEFAULT. The head unit is a shared screen — the rider in
            the back reads it as easily as the driver does — so the amount is
            replaced by dots until the driver presses the eye map button
            (register.ts). The eye glyph stays visible either way so the pill
            still reads as "your earnings, hidden" rather than as a bug. */}
        {todayEarnings && (
          <View style={styles.earningsPill}>
            <Text style={styles.earningsPillLabel}>TODAY</Text>
            <Text
              style={[styles.earningsPillEye, earningsHidden && styles.earningsPillEyeMuted]}
              accessibilityLabel={
                earningsHidden ? 'Earnings hidden' : 'Earnings visible'
              }
            >
              {earningsHidden ? '\u25CF\u20E0' : '\u25C9'}
            </Text>
            <Text style={styles.earningsPillValue}>
              {displayEarnings(todayEarnings, earningsHidden)}
            </Text>
          </View>
        )}
      </View>
      {/* Renders above everything, including the map, so it stays readable even
          if the map is the thing that's broken. Returns null unless toggled. */}
      <CarDebugPanel />
    </View>
  );
}

const styles = StyleSheet.create({
  fill: { flex: 1 },
  // A map that fails to paint leaves whatever is behind it showing. White reads
  // as "the app is broken"; this dark backdrop matches the car surface's own
  // dark theme, so a tile-load failure looks like an unloaded map rather than a
  // crash — and is visually distinct from iternio's DKGRAY "React never
  // mounted" background (VirtualRenderer.kt sets that on the ReactSurfaceView).
  mapBackdrop: { backgroundColor: '#0B0B0F' },
  diagnostic: { alignItems: 'center', justifyContent: 'center', backgroundColor: '#0B0B0F', padding: 24 },
  diagnosticTitle: { color: '#FFFFFF', fontSize: 22, fontWeight: '700', marginBottom: 8 },
  diagnosticBody: { color: '#B6B6C0', fontSize: 15, textAlign: 'center' },
  pillRow: {
    position: 'absolute',
    top: 12,
    left: 12,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  statusPill: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 12,
    paddingVertical: 7,
    borderRadius: 999,
    backgroundColor: 'rgba(11,11,15,0.82)',
  },
  earningsPill: {
    flexDirection: 'row',
    alignItems: 'baseline',
    gap: 6,
    paddingHorizontal: 12,
    paddingVertical: 7,
    borderRadius: 999,
    backgroundColor: 'rgba(11,11,15,0.82)',
  },
  earningsPillLabel: { color: carColors.textMuted, fontSize: 11, fontWeight: '700', letterSpacing: 1 },
  earningsPillValue: { color: carColors.gold, fontSize: 17, fontWeight: '800' },
  earningsPillEye: { color: carColors.gold, fontSize: 14, fontWeight: '700' },
  earningsPillEyeMuted: { color: carColors.textMuted },
  statusDot: { width: 8, height: 8, borderRadius: 4, marginRight: 8 },
  statusText: { color: '#FFFFFF', fontSize: 14, fontWeight: '600' },
});
