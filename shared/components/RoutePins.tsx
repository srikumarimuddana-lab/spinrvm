import React, { useEffect, useState } from 'react';
import { StyleSheet, View } from 'react-native';
import { Marker } from 'react-native-maps';

import {
  ROUTE_MARKER_SIZE,
  ROUTE_PIN_GEOMETRY,
  ROUTE_PIN_SPEC,
  type RoutePinKind,
} from '../constants/routeMapStyle';

export interface RoutePoint {
  latitude: number;
  longitude: number;
}

/**
 * The glyph, drawn as plain Views.
 *
 * It used to be an @expo/vector-icons glyph. That is fine on a phone and NOT
 * fine on the Android Auto surface, which renders this same component inside a
 * Presentation on a VirtualDisplay — the icon font came out empty there, so the
 * destination showed on the head unit as a bare red dot while the phone showed
 * a flag. A View with a background colour has no such failure mode, and it
 * matches routePinSvg() on the web surfaces shape for shape.
 */
function Glyph({ kind, size }: { kind: RoutePinKind; size: number }): React.ReactElement {
  const g = ROUTE_PIN_GEOMETRY;
  if (ROUTE_PIN_SPEC[kind].glyph === 'dot') {
    const d = size * g.dotRatio;
    return <View style={{ width: d, height: d, borderRadius: d / 2, backgroundColor: '#FFFFFF' }} />;
  }
  if (ROUTE_PIN_SPEC[kind].glyph === 'square') {
    const d = size * g.squareRatio;
    return <View style={{ width: d, height: d, borderRadius: d * 0.18, backgroundColor: '#FFFFFF' }} />;
  }
  // Check: two rotated bars. Same two-segment tick the SVG draws.
  const sw = size * g.checkStrokeRatio;
  const short = size * g.checkShortRatio;
  const long = size * g.checkLongRatio;
  return (
    <View style={{ width: size * 0.6, height: size * 0.6, alignItems: 'center', justifyContent: 'center' }}>
      <View
        style={{
          position: 'absolute',
          width: short,
          height: sw,
          borderRadius: sw / 2,
          backgroundColor: '#FFFFFF',
          transform: [{ translateX: -size * 0.12 }, { translateY: size * 0.06 }, { rotate: '45deg' }],
        }}
      />
      <View
        style={{
          position: 'absolute',
          width: long,
          height: sw,
          borderRadius: sw / 2,
          backgroundColor: '#FFFFFF',
          transform: [{ translateX: size * 0.05 }, { rotate: '-45deg' }],
        }}
      />
    </View>
  );
}

function Pin({ kind, size }: { kind: RoutePinKind; size: number }): React.ReactElement {
  return (
    <View
      style={[
        styles.pin,
        {
          backgroundColor: ROUTE_PIN_SPEC[kind].color,
          width: size,
          height: size,
          borderRadius: size / 2,
          borderWidth: Math.max(1, (ROUTE_PIN_GEOMETRY.ringWidth * size) / ROUTE_MARKER_SIZE),
        },
      ]}
    >
      <Glyph kind={kind} size={size} />
    </View>
  );
}

/**
 * Snapshot settle window.
 *
 * `tracksViewChanges={false}` from the very first render is how a custom-view
 * marker ends up as an invisible (or empty white) box on Android: the native
 * marker takes its bitmap before the child View has laid out. The old pin got
 * away with it because the icon font forced an extra layout pass; the drawn
 * shapes do not. So track for a moment, then stop — the same trade
 * CarMarker.tsx makes, minus the image decode it has to wait on.
 */
const SNAPSHOT_SETTLE_MS = 300;

function useSettledSnapshot(): boolean {
  const [tracking, setTracking] = useState(true);
  useEffect(() => {
    const t = setTimeout(() => setTracking(false), SNAPSHOT_SETTLE_MS);
    return () => clearTimeout(t);
  }, []);
  return tracking;
}

interface RoutePinsProps {
  pickup?: RoutePoint | null;
  dropoff?: RoutePoint | null;
  /** Actual completion fix (amber check). Omit to hide. */
  completion?: RoutePoint | null;
  /** Diameter in px. Defaults to the shared ROUTE_MARKER_SIZE. */
  size?: number;
}

/**
 * THE pickup / destination / completion markers for every react-native map:
 * one disc style, size and colour set (green pickup dot / red dropoff square /
 * amber completion check), centre-anchored. Any null point is not rendered.
 *
 * The web surfaces (admin MapLibre maps, the public tracking page) draw the
 * SAME marker from routePinSvg() in shared/constants/routeMapStyle.ts — change
 * the spec there, not here, or the two drift apart again.
 */
export function RoutePins({ pickup, dropoff, completion, size = ROUTE_MARKER_SIZE }: RoutePinsProps) {
  const tracksViewChanges = useSettledSnapshot();
  return (
    <>
      {pickup ? (
        <Marker coordinate={pickup} anchor={{ x: 0.5, y: 0.5 }} tracksViewChanges={tracksViewChanges}>
          <Pin kind="pickup" size={size} />
        </Marker>
      ) : null}
      {dropoff ? (
        <Marker coordinate={dropoff} anchor={{ x: 0.5, y: 0.5 }} tracksViewChanges={tracksViewChanges}>
          <Pin kind="dropoff" size={size} />
        </Marker>
      ) : null}
      {completion ? (
        <Marker coordinate={completion} anchor={{ x: 0.5, y: 0.5 }} tracksViewChanges={tracksViewChanges}>
          <Pin kind="completion" size={size} />
        </Marker>
      ) : null}
    </>
  );
}

const styles = StyleSheet.create({
  pin: {
    alignItems: 'center',
    justifyContent: 'center',
    borderColor: '#FFFFFF',
  },
});

export default RoutePins;
