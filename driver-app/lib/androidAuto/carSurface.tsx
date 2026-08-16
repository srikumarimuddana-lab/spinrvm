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
import React, { useCallback, useMemo } from 'react';
import { Platform, StyleSheet, Text, View } from 'react-native';
import { useDriverStore } from '../../store/driverStore';
import { useAuthStore } from '@shared/store/authStore';
import { useDemandHeatmapView } from '../../hooks/demandHeatmapShared';
import type { HeatmapCell } from '../../hooks/useDemandHeatmap';
import { selectCarRoute } from './carRoute';
import { buildTripCard, type OfferLike } from './carCard';
import { CarTripCard } from './CarTripCard';
import { useCarMapCamera } from './carMapCamera';
import { useCarLocation } from './useCarLocation';
import { pushDebug, setDebugFact } from './carDebug';
import { CarDebugPanel } from './CarDebugPanel';
import { carColors } from './carTheme';
// RouteLine / RoutePins hard-import react-native-maps, so they are lazy-required
// AFTER the maps guard below (never at module scope) — otherwise loading this
// file in a maps-less context (web / Expo Go / tests) would crash before the
// guarded require, defeating the graceful null fallback.

// Saskatoon — Spinr is Saskatchewan-first. Used only until the first fix /
// last-known location loads, so the idle map never opens on null-island (0,0).
const FALLBACK_CENTER = { latitude: 52.1332, longitude: -106.67 };

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

export function CarMapSurface(): React.ReactElement | null {
  // Hooks first — unconditional, before any early return (rules-of-hooks).
  const rideState = useDriverStore((s) => s.rideState);
  const activeRide = useDriverStore((s) => s.activeRide);
  const incomingRide = useDriverStore((s) => s.incomingRide);
  const delta = useCarMapCamera((s) => s.delta);
  const offsetLat = useCarMapCamera((s) => s.offsetLat);
  const offsetLng = useCarMapCamera((s) => s.offsetLng);
  const here = useCarLocation();
  const route = selectCarRoute(rideState, activeRide);
  // Cumulative earnings for the completed-trip card. Fetched by the phone, so
  // a car-only cold launch legitimately has none — buildTripCard omits the line
  // rather than showing a misleading zero.
  const earningsSummary = useDriverStore((s) => s.earnings);
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
  const todayEarnings = useMemo(() => {
    const total = Number(earningsCtx?.total);
    return Number.isFinite(total) && total > 0 ? `$${total.toFixed(2)}` : null;
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
  const onMapReady = useCallback(() => {
    console.log('[CarSurface] MapView ready (native view attached)');
    pushDebug('info', 'MapView onMapReady — native view attached');
    setDebugFact('map', 'ready (no tiles yet)');
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
    setDebugFact('zoomDelta', delta.toFixed(4));
    setDebugFact(
      'pan',
      offsetLat === 0 && offsetLng === 0
        ? 'following driver'
        : `${offsetLat.toFixed(4)}, ${offsetLng.toFixed(4)} (recenter to clear)`,
    );
    setDebugFact('location', here ? `${here.latitude.toFixed(4)}, ${here.longitude.toFixed(4)}` : 'no fix (fallback)');
    setDebugFact('route', route ? `${route.leg}, ${route.polyline.length} pts` : 'none');
    setDebugFact('heatmap', `${heatmapStatus}, ${carHeatCells.length} cells`);
  }, [rideState, card.leg, delta, here, route, heatmapStatus, carHeatCells.length, offsetLat, offsetLng]);

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

  // Follow the driver; fall back to the active destination, then a city center,
  // so the camera always has a valid target even before the first GPS fix.
  // The pan offset is added on top: while it is non-zero the driver has dragged
  // the map, so the view stays where they put it instead of being yanked back
  // by the next GPS fix. The Recenter map button clears it.
  const followTarget = here ?? route?.destination ?? FALLBACK_CENTER;
  const center = {
    latitude: followTarget.latitude + offsetLat,
    longitude: followTarget.longitude + offsetLng,
  };

  return (
    <View style={[styles.fill, styles.mapBackdrop]}>
      <MapView
        // Re-mount on a leg / idle transition so Android's Google Maps native
        // layer fully drops a leftover route overlay; within a leg the camera is
        // driven by `region` (below), not a remount, so live location + zoom
        // updates don't thrash the surface.
        key={route ? `${route.leg}` : 'idle'}
        style={styles.fill}
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
        // controlled region lets the zoom buttons + follow-me drive the camera.
        pointerEvents="none"
        showsUserLocation={false}
        region={{
          latitude: center.latitude,
          longitude: center.longitude,
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

        {/* THE uniform route line + pins. `route.polyline` is the SAME stored
            pickup→dropoff geometry as before (empty on the pre-pickup leg); it is
            now drawn as one orange→red gradient via the shared RouteLine so the
            car surface reads identically to the phone. Pins follow the leg: green
            pickup while heading to the rider; green pickup (route start) + red
            dropoff once the trip is under way. */}
        {route && RouteLine && <RouteLine path={route.polyline} />}
        {route && RoutePins && (
          <RoutePins
            pickup={route.leg === 'pickup' ? route.destination : (route.polyline[0] ?? null)}
            dropoff={route.leg === 'dropoff' ? route.destination : null}
          />
        )}
        {here && CarMarker && (
          <CarMarker
            coordinate={{ latitude: here.latitude, longitude: here.longitude }}
            heading={here.heading}
          />
        )}
      </MapView>
      {/* Branded trip card overlay (display-only; interaction is via template
          header actions / map buttons / the ride-offer alert). Hidden on the
          bare idle map so the screen stays an uncluttered live map until there
          is something to show. */}
      {card.leg !== 'idle' && <CarTripCard card={card} />}
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
            zero on the earnings pill would read as "you've made nothing". */}
        {todayEarnings && (
          <View style={styles.earningsPill}>
            <Text style={styles.earningsPillLabel}>TODAY</Text>
            <Text style={styles.earningsPillValue}>{todayEarnings}</Text>
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
  statusDot: { width: 8, height: 8, borderRadius: 4, marginRight: 8 },
  statusText: { color: '#FFFFFF', fontSize: 14, fontWeight: '600' },
});
