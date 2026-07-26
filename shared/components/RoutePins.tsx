import React from 'react';
import { StyleSheet, View } from 'react-native';
import { Marker } from 'react-native-maps';
import { Ionicons } from '@expo/vector-icons';

import { ROUTE_MARKER_SIZE, ROUTE_PIN_COLORS } from '../constants/routeMapStyle';

export interface RoutePoint {
  latitude: number;
  longitude: number;
}

function Pin({ color, icon }: { color: string; icon: keyof typeof Ionicons.glyphMap }) {
  return (
    <View
      style={[
        styles.pin,
        {
          backgroundColor: color,
          width: ROUTE_MARKER_SIZE,
          height: ROUTE_MARKER_SIZE,
          borderRadius: ROUTE_MARKER_SIZE / 2,
        },
      ]}
    >
      <Ionicons name={icon} size={Math.round(ROUTE_MARKER_SIZE * 0.5)} color="#FFFFFF" />
    </View>
  );
}

interface RoutePinsProps {
  pickup?: RoutePoint | null;
  dropoff?: RoutePoint | null;
  /** Actual completion fix (amber checkmark). Omit to hide. */
  completion?: RoutePoint | null;
}

/**
 * THE pickup / destination / completion markers for every react-native map.
 * One style, one size, one colour set (green pickup, red dropoff, amber
 * completion) reused on every screen so the "navigation points" look identical
 * everywhere. Any point that is null is simply not rendered.
 */
export function RoutePins({ pickup, dropoff, completion }: RoutePinsProps) {
  return (
    <>
      {pickup ? (
        <Marker coordinate={pickup} anchor={{ x: 0.5, y: 0.5 }} tracksViewChanges={false}>
          <Pin color={ROUTE_PIN_COLORS.pickup} icon="location" />
        </Marker>
      ) : null}
      {dropoff ? (
        <Marker coordinate={dropoff} anchor={{ x: 0.5, y: 0.5 }} tracksViewChanges={false}>
          <Pin color={ROUTE_PIN_COLORS.dropoff} icon="flag" />
        </Marker>
      ) : null}
      {completion ? (
        <Marker coordinate={completion} anchor={{ x: 0.5, y: 0.5 }} tracksViewChanges={false}>
          <Pin color={ROUTE_PIN_COLORS.completion} icon="checkmark" />
        </Marker>
      ) : null}
    </>
  );
}

const styles = StyleSheet.create({
  pin: {
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: 2,
    borderColor: '#FFFFFF',
  },
});

export default RoutePins;
