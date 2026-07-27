import React from 'react';
import { Polyline } from 'react-native-maps';

import {
  buildPathGradient,
  buildStraightRouteGradient,
  ROUTE_GRADIENT_SEGMENTS,
  ROUTE_STROKE_WIDTH,
} from '../constants/routeMapStyle';

export interface RoutePoint {
  latitude: number;
  longitude: number;
}

interface RouteLineProps {
  /** The REAL route coordinates (actual driven / road path). Preferred. */
  path?: RoutePoint[] | null;
  /** Straight-line fallback endpoints, used only when `path` has < 2 points. */
  pickup?: RoutePoint | null;
  destination?: RoutePoint | null;
  strokeWidth?: number;
  segments?: number;
}

/**
 * THE route line for every react-native map. Draws the REAL route `path` as one
 * orange→red gradient (orange at the start, red at the destination), keeping the
 * true road shape. When no path is available yet it falls back to a straight
 * pickup→destination gradient. Same component everywhere, so every map reads the
 * same. Renders nothing when there is neither a usable path nor endpoints.
 */
export function RouteLine({
  path,
  pickup,
  destination,
  strokeWidth = ROUTE_STROKE_WIDTH,
  segments = ROUTE_GRADIENT_SEGMENTS,
}: RouteLineProps) {
  const cleanPath = (path ?? []).filter(
    (p) => p && Number.isFinite(p.latitude) && Number.isFinite(p.longitude),
  );

  const gradient =
    cleanPath.length >= 2
      ? buildPathGradient(
          cleanPath.map((p) => [p.latitude, p.longitude] as [number, number]),
          segments,
        )
      : buildStraightRouteGradient(
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
