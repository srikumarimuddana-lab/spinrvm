/**
 * react-native-maps stub for Expo web builds.
 *
 * react-native-maps is a native-only package. On web we render a simple
 * placeholder so the bundle compiles without errors. Screens that render
 * MapView on web will show "Open in the mobile app for maps."
 *
 * This file is resolved by metro.config.js via resolver.resolveRequest
 * when platform === 'web'. It is NEVER bundled into native builds.
 */
import React from 'react';
import { View, Text, StyleSheet } from 'react-native';

const MapPlaceholder = React.forwardRef(function MapView({ style, children }, _ref) {
  return (
    <View style={[styles.container, style]}>
      <Text style={styles.icon}>🗺️</Text>
      <Text style={styles.text}>Map view available on iOS & Android</Text>
      <Text style={styles.sub}>Open the Spinr mobile app for full map experience</Text>
    </View>
  );
});

const Noop = () => null;
Noop.displayName = 'MapNoop';

const styles = StyleSheet.create({
  container: {
    justifyContent: 'center',
    alignItems: 'center',
    backgroundColor: '#F8F8F8',
    minHeight: 200,
    borderRadius: 12,
    padding: 24,
    gap: 8,
  },
  icon: { fontSize: 36 },
  text: { color: '#555', fontSize: 15, fontWeight: '600', textAlign: 'center' },
  sub: { color: '#999', fontSize: 13, textAlign: 'center', marginTop: 4 },
});

// Default export is the map container
export default MapPlaceholder;

// Named exports for all MapView sub-components
export const Marker = Noop;
export const Callout = Noop;
export const Circle = Noop;
export const Polygon = Noop;
export const Polyline = Noop;
export const Overlay = Noop;
export const Heatmap = Noop;
export const UrlTile = Noop;
export const LocalTile = Noop;
export const WMSTile = Noop;
export const MapCallout = Noop;
export const MarkerAnimated = Noop;
export const AnimatedRegion = class AnimatedRegion {
  constructor(coords) { Object.assign(this, coords); }
  setValue() {}
  timing() { return { start: () => {} }; }
};

// Provider constants
export const PROVIDER_GOOGLE = 'google';
export const PROVIDER_DEFAULT = null;
