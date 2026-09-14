import {
  bearingDegrees, distanceMeters, snapToRoute,
  type RouteSnapResult, type TrackingLatLng,
} from './vehicleTracking';

/** A compass angle or no evidence; zero is a valid northbound course. */
export function validTravelBearing(value: number | null | undefined): number | null {
  return value != null && Number.isFinite(value) && value >= 0 ? value % 360 : null;
}

/** Only confirmed movement can establish the direction of a route snap. */
export function trackingSnap(
  position: TrackingLatLng,
  route: readonly TrackingLatLng[] | null | undefined,
  travelBearing: number | null,
  preferredIndex?: number | null,
): RouteSnapResult | null {
  if (travelBearing === null) return null;
  return snapToRoute(position, route, 35, preferredIndex, travelBearing);
}

export interface MarkerMotionStep {
  coordinate: TrackingLatLng;
  durationMs: number;
}

/** Split a native animation at road vertices so a bend cannot become a chord. */
export function routeMotionSteps(
  from: TrackingLatLng, to: TrackingLatLng,
  route: readonly TrackingLatLng[] | null | undefined,
  end: RouteSnapResult | null, durationMs: number,
): MarkerMotionStep[] {
  const direct = [{ coordinate: to, durationMs }];
  if (!route || !end) return direct;
  const start = snapToRoute(from, route, 2, end.segmentIndex);
  if (!start || start.segmentIndex > end.segmentIndex) return direct;
  // Bounded work on malformed/self-crossing geometry. A 500ms tick cannot
  // legitimately cross dozens of road segments.
  if (end.segmentIndex - start.segmentIndex > 16) return direct;
  const points = route.slice(start.segmentIndex + 1, end.segmentIndex + 1).concat([to]);
  let previous = from;
  const lengths = points.map((point) => {
    const metres = distanceMeters(previous.latitude, previous.longitude, point.latitude, point.longitude);
    previous = point;
    return metres;
  });
  const total = lengths.reduce((sum, value) => sum + value, 0);
  if (total < 0.5) return direct;
  const chord = distanceMeters(from.latitude, from.longitude, to.latitude, to.longitude);
  if (total > Math.max(25, chord * 2)) return direct;
  return points.map((coordinate, i) => ({ coordinate, durationMs: durationMs * lengths[i] / total }));
}

/** Bearing of the motion actually being animated, including each corner leg. */
export function motionStepBearing(from: TrackingLatLng, to: TrackingLatLng, fallback: number): number {
  return distanceMeters(from.latitude, from.longitude, to.latitude, to.longitude) >= 0.5
    ? bearingDegrees(from.latitude, from.longitude, to.latitude, to.longitude) : fallback;
}
