/**
 * The branded in-dash route map drawn onto the car head-unit surface.
 *
 * iternio's MapTemplate renders this React component onto the Android Auto map
 * surface. It draws the route we ALREADY stored at ride creation
 * (`rides.planned_route_polyline`) with the destination pinned — so there is no
 * live Routes/Directions API spend (turn-by-turn is handed off to the driver's
 * Google Maps / Waze; see carRoute.ts + register.ts).
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

const SPINR_RED = '#ee2b2b';

export function CarMapSurface(): React.ReactElement | null {
  // Hooks first — unconditional, before any early return (rules-of-hooks).
  const rideState = useDriverStore((s) => s.rideState);
  const activeRide = useDriverStore((s) => s.activeRide);
  const route = selectCarRoute(rideState, activeRide);

  let Maps: typeof import('react-native-maps') | null = null;
  try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    Maps = require('react-native-maps');
  } catch {
    Maps = null;
  }
  if (!Maps || !route) return null;

  const MapView = Maps.default;
  const { Marker, Polyline } = Maps;

  return (
    <View style={styles.fill}>
      <MapView
        // Re-mount (fresh initialRegion) when the destination changes so the
        // camera re-centers on a leg/ride swap instead of staying on the stale
        // ride — initialRegion only applies on mount.
        key={`${route.destination.latitude},${route.destination.longitude}`}
        style={styles.fill}
        // The projected car surface is non-interactive (Android Auto drives
        // interaction through template buttons, not in-surface touches).
        pointerEvents="none"
        initialRegion={{
          latitude: route.destination.latitude,
          longitude: route.destination.longitude,
          latitudeDelta: 0.05,
          longitudeDelta: 0.05,
        }}
      >
        {route.polyline.length >= 2 && (
          <Polyline coordinates={route.polyline} strokeColor={SPINR_RED} strokeWidth={6} />
        )}
        <Marker coordinate={route.destination} title={route.destinationLabel} />
      </MapView>
    </View>
  );
}

const styles = StyleSheet.create({ fill: { flex: 1 } });
