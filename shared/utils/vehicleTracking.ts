/**
 * Geometry helpers for smooth vehicle-marker tracking (Uber/Lyft-style).
 *
 * Pure math, no React/native imports — consumed by CarMarker (rider + driver
 * apps) to snap raw GPS fixes onto the known route polyline and to rotate the
 * car smoothly along the shortest arc instead of snapping its angle.
 */

export interface TrackingLatLng {
  latitude: number;
  longitude: number;
}

export interface RouteSnapResult {
  /** Point on the route polyline nearest to the raw fix. */
  coordinate: TrackingLatLng;
  /** Direction of the route segment the fix snapped onto, degrees 0–359. */
  bearing: number;
  /** Index of the segment's start vertex in the route array. */
  segmentIndex: number;
  /** Distance from the raw fix to the snapped point, meters. */
  deviationMeters: number;
}

const EARTH_RADIUS_M = 6371000;

const toRad = (d: number) => (d * Math.PI) / 180;

export function distanceMeters(
  lat1: number,
  lng1: number,
  lat2: number,
  lng2: number,
): number {
  const dLat = toRad(lat2 - lat1);
  const dLng = toRad(lng2 - lng1);
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLng / 2) ** 2;
  return 2 * EARTH_RADIUS_M * Math.asin(Math.sqrt(a));
}

export function bearingDegrees(
  lat1: number,
  lng1: number,
  lat2: number,
  lng2: number,
): number {
  const dLng = toRad(lng2 - lng1);
  const y = Math.sin(dLng) * Math.cos(toRad(lat2));
  const x =
    Math.cos(toRad(lat1)) * Math.sin(toRad(lat2)) -
    Math.sin(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.cos(dLng);
  return ((Math.atan2(y, x) * 180) / Math.PI + 360) % 360;
}

/**
 * Project a raw GPS fix onto the nearest segment of a route polyline.
 *
 * Uses an equirectangular local projection around the fix — accurate to well
 * under a meter at the ~50 m scales involved, and cheap enough to run on
 * every fix on low-end Android.
 *
 * Returns null when the route has fewer than 2 points or the nearest point on
 * the route is farther than `maxSnapMeters` (driver off-route / detour): the
 * caller should then fall back to the raw fix so the marker never lies about
 * where the car actually is.
 */
export function snapToRoute(
  point: TrackingLatLng,
  route: readonly TrackingLatLng[] | null | undefined,
  maxSnapMeters = 35,
): RouteSnapResult | null {
  if (!route || route.length < 2) return null;

  const cosLat = Math.cos(toRad(point.latitude));
  // Local meters-per-degree scale around the fix.
  const mPerDegLat = (Math.PI / 180) * EARTH_RADIUS_M;
  const mPerDegLng = mPerDegLat * cosLat;

  const px = point.longitude * mPerDegLng;
  const py = point.latitude * mPerDegLat;

  let best: RouteSnapResult | null = null;
  let bestDistSq = Infinity;

  for (let i = 0; i < route.length - 1; i++) {
    const a = route[i];
    const b = route[i + 1];
    if (
      !Number.isFinite(a?.latitude) || !Number.isFinite(a?.longitude) ||
      !Number.isFinite(b?.latitude) || !Number.isFinite(b?.longitude)
    ) {
      continue;
    }
    const ax = a.longitude * mPerDegLng;
    const ay = a.latitude * mPerDegLat;
    const bx = b.longitude * mPerDegLng;
    const by = b.latitude * mPerDegLat;

    const abx = bx - ax;
    const aby = by - ay;
    const lenSq = abx * abx + aby * aby;
    // Zero-length segment (duplicate vertex) — treat as the point itself.
    const t = lenSq === 0 ? 0 : Math.max(0, Math.min(1, ((px - ax) * abx + (py - ay) * aby) / lenSq));
    const cx = ax + t * abx;
    const cy = ay + t * aby;
    const dx = px - cx;
    const dy = py - cy;
    const distSq = dx * dx + dy * dy;
    if (distSq < bestDistSq) {
      bestDistSq = distSq;
      const snapped: TrackingLatLng = {
        latitude: cy / mPerDegLat,
        longitude: cx / mPerDegLng,
      };
      // Bearing of the segment itself; for a zero-length segment fall back to
      // the next/previous distinct vertex handled by the caller's own bearing.
      const bearing =
        lenSq === 0
          ? bearingDegrees(point.latitude, point.longitude, b.latitude, b.longitude)
          : bearingDegrees(a.latitude, a.longitude, b.latitude, b.longitude);
      best = {
        coordinate: snapped,
        bearing,
        segmentIndex: i,
        deviationMeters: Math.sqrt(distSq),
      };
    }
  }

  if (!best || best.deviationMeters > maxSnapMeters) return null;
  return best;
}

/**
 * Given the marker's current (possibly un-normalized, continuously
 * accumulated) rotation value and a target compass bearing 0–359, return the
 * nearest equivalent target so an Animated timing between the two always
 * turns through the shortest arc (350° → 10° animates +20°, not −340°).
 */
export function shortestArcRotationTarget(current: number, targetBearing: number): number {
  const currentNorm = ((current % 360) + 360) % 360;
  let delta = targetBearing - currentNorm;
  if (delta > 180) delta -= 360;
  else if (delta < -180) delta += 360;
  return current + delta;
}
