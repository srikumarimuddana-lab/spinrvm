import React from 'react';
import { Polyline } from 'react-native-maps';

import {
  buildMultiPathGradient,
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
  /**
   * REAL route SECTIONS (v2 capture segments). Preferred for completed rides —
   * each section is drawn independently so a GPS gap is never bridged by a false
   * chord, while the gradient runs continuously across the whole trip.
   */
  paths?: (RoutePoint[] | null | undefined)[] | null;
  /** A single continuous REAL route path (no internal gaps). */
  path?: RoutePoint[] | null;
  /** Straight-line fallback endpoints, used only when there is no real route. */
  pickup?: RoutePoint | null;
  destination?: RoutePoint | null;
  strokeWidth?: number;
  segments?: number;
}

const toLatLng = (p: RoutePoint) => [p.latitude, p.longitude] as [number, number];
const clean = (pts?: RoutePoint[] | null) =>
  (pts ?? []).filter((p) => p && Number.isFinite(p.latitude) && Number.isFinite(p.longitude));

/**
 * THE route line for every react-native map. Draws the REAL route as one
 * orange→red gradient (orange at the start, red at the destination), keeping the
 * true road shape. Prefer `paths` (v2 sections — gap-preserving); `path` for a
 * single continuous route; `pickup`/`destination` for a straight preview
 * fallback. Same component everywhere, so every map reads the same. Renders
 * nothing when there is no usable geometry.
 */
export function RouteLine({
  paths,
  path,
  pickup,
  destination,
  strokeWidth = ROUTE_STROKE_WIDTH,
  segments = ROUTE_GRADIENT_SEGMENTS,
}: RouteLineProps) {
  const cleanSections = (paths ?? []).map(clean).filter((s) => s.length >= 2);
  const cleanPath = clean(path);

  const gradient =
    cleanSections.length > 0
      ? buildMultiPathGradient(
          cleanSections.map((s) => s.map(toLatLng)),
          segments,
        )
      : cleanPath.length >= 2
        ? buildPathGradient(cleanPath.map(toLatLng), segments)
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
