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
import React from 'react';
import { StyleSheet, View } from 'react-native';
import { useDriverStore } from '../../store/driverStore';
import { selectCarRoute } from './carRoute';
import { buildTripCard, type OfferLike } from './carCard';
import { CarTripCard } from './CarTripCard';
import { useCarMapCamera } from './carMapCamera';
import { useCarLocation } from './useCarLocation';
import { RouteLine } from '@shared/components/RouteLine';
import { RoutePins } from '@shared/components/RoutePins';

// Saskatoon — Spinr is Saskatchewan-first. Used only until the first fix /
// last-known location loads, so the idle map never opens on null-island (0,0).
const FALLBACK_CENTER = { latitude: 52.1332, longitude: -106.67 };

export function CarMapSurface(): React.ReactElement | null {
  // Hooks first — unconditional, before any early return (rules-of-hooks).
  const rideState = useDriverStore((s) => s.rideState);
  const activeRide = useDriverStore((s) => s.activeRide);
  const incomingRide = useDriverStore((s) => s.incomingRide);
  const delta = useCarMapCamera((s) => s.delta);
  const here = useCarLocation();
  const route = selectCarRoute(rideState, activeRide);
  // The same glanceable card the offer alert is built from, drawn over the map.
  const card = buildTripCard(rideState, activeRide, incomingRide as OfferLike | null);

  let Maps: typeof import('react-native-maps') | null = null;
  try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    Maps = require('react-native-maps');
  } catch {
    Maps = null;
  }
  if (!Maps) return null;

  const MapView = Maps.default;

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

  // Follow the driver; fall back to the active destination, then a city center,
  // so the camera always has a valid target even before the first GPS fix.
  const center = here ?? route?.destination ?? FALLBACK_CENTER;

  return (
    <View style={styles.fill}>
      <MapView
        // Re-mount on a leg / idle transition so Android's Google Maps native
        // layer fully drops a leftover route overlay; within a leg the camera is
        // driven by `region` (below), not a remount, so live location + zoom
        // updates don't thrash the surface.
        key={route ? `${route.leg}` : 'idle'}
        style={styles.fill}
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
        {/* THE uniform route line + pins. `route.polyline` is the SAME stored
            pickup→dropoff geometry as before (empty on the pre-pickup leg); it is
            now drawn as one orange→red gradient via the shared RouteLine so the
            car surface reads identically to the phone. Pins follow the leg: green
            pickup while heading to the rider; green pickup (route start) + red
            dropoff once the trip is under way. */}
        {route && <RouteLine path={route.polyline} />}
        {route && (
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
    </View>
  );
}

const styles = StyleSheet.create({ fill: { flex: 1 } });
