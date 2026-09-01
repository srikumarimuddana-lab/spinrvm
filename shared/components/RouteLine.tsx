import React from 'react';
import { Polyline } from 'react-native-maps';

import {
  buildMultiPathGradient,
  buildPathGradient,
  buildStraightRouteGradient,
  ROUTE_GRADIENT_SEGMENTS,
  ROUTE_STROKE_WIDTH,
} from '../constants/routeMapStyle';
import { snapToRoute } from '../utils/vehicleTracking';

export interface RoutePoint {
  latitude: number;
  longitude: number;
}

/** How far the vehicle may be from `path` and still count as "on it" for
 * traveled-erasure — matches CarMarker's own MAX_ROUTE_SNAP_M so the two
 * agree on what "on route" means. Beyond this, the vehicle is off-route
 * (detour, stale fix) and the whole path renders rather than guessing where
 * to cut it. */
const TRAVELED_TRIM_MAX_SNAP_M = 35;

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
  /**
   * Current position of the vehicle traveling `path` (its own tracked
   * position — the marker's reported/delayed position where available, not
   * necessarily the rawest possible fix; a few meters of slack is invisible
   * here, unlike a camera anchor). When set, the portion of `path` already
   * behind the vehicle is erased instead of rendered — matching Uber/Lyft's
   * live tracking screens, where the line only shows the road ahead. Has no
   * effect on `paths` (completed-ride history views, where nothing is "still
   * ahead" to erase behind) or on the straight pickup/destination fallback.
   * Omit entirely for a route that isn't actively being driven yet (e.g. the
   * pickup→dropoff preview before the rider has gotten in).
   */
  vehiclePosition?: RoutePoint | null;
}

const toLatLng = (p: RoutePoint) => [p.latitude, p.longitude] as [number, number];
const clean = (pts?: RoutePoint[] | null) =>
  (pts ?? []).filter((p) => p && Number.isFinite(p.latitude) && Number.isFinite(p.longitude));

/**
 * Drop the portion of `path` already behind `vehiclePosition`. Snaps the
 * vehicle onto the path (same technique CarMarker uses to keep the car on
 * the road) and keeps only the snapped point plus everything after its
 * segment — the traveled prefix is simply not returned, matching the "line
 * erases behind the car" behavior riders expect from Uber/Lyft. Returns
 * `path` unchanged when there's no vehicle position, too few points, or the
 * vehicle isn't within snapping distance of this path (off-route/stale —
 * safer to show the whole line than guess where to cut it).
 */
export function trimTraveled(path: RoutePoint[], vehiclePosition?: RoutePoint | null): RoutePoint[] {
  if (!vehiclePosition || path.length < 2) return path;
  const snap = snapToRoute(vehiclePosition, path, TRAVELED_TRIM_MAX_SNAP_M);
  if (!snap) return path;
  const remaining = path.slice(snap.segmentIndex + 1);
  return [snap.coordinate, ...remaining];
}

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
  vehiclePosition,
}: RouteLineProps) {
  const cleanSections = (paths ?? []).map(clean).filter((s) => s.length >= 2);
  const cleanPath = trimTraveled(clean(path), vehiclePosition);

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
