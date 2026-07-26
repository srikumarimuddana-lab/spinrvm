import React from 'react';
import { Polyline } from 'react-native-maps';

import {
  buildStraightRouteGradient,
  ROUTE_GRADIENT_SEGMENTS,
  ROUTE_STROKE_WIDTH,
} from '../constants/routeMapStyle';

export interface RoutePoint {
  latitude: number;
  longitude: number;
}

interface RouteLineProps {
  /** Pickup end of the line (orange). */
  pickup?: RoutePoint | null;
  /** Destination end of the line (red). */
  destination?: RoutePoint | null;
  strokeWidth?: number;
  segments?: number;
}

/**
 * THE route line for every react-native map (rider + driver, live and
 * completed): a single straight pickup→destination line drawn as an orange→red
 * gradient. There is no per-segment / gap / planned-vs-actual styling anywhere —
 * this component is the only route renderer the apps use, so every map reads the
 * same. Renders nothing when either endpoint is missing or the points coincide.
 */
export function RouteLine({
  pickup,
  destination,
  strokeWidth = ROUTE_STROKE_WIDTH,
  segments = ROUTE_GRADIENT_SEGMENTS,
}: RouteLineProps) {
  const gradient = buildStraightRouteGradient(
    pickup ? [pickup.latitude, pickup.longitude] : null,
    destination ? [destination.latitude, destination.longitude] : null,
    segments,
  );
  if (gradient.length === 0) return null;
  return (
    <>
      {gradient.map((seg, i) => (
        <Polyline
          key={`route-gradient-${i}`}
          coordinates={seg.coordinates.map(([latitude, longitude]) => ({ latitude, longitude }))}
          strokeColor={seg.color}
          strokeWidth={strokeWidth}
          lineCap="round"
          lineJoin="round"
        />
      ))}
    </>
  );
}

export default RouteLine;
