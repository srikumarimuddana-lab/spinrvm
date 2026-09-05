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

// Segment-selection is otherwise a pure nearest-distance search across the
// WHOLE route, with no notion of which way the car is actually moving. At
// any point where two segments both fall within maxSnapMeters — a divided
// road, an out-and-back street where the outbound and return legs run close
// together, a crossing street near an intersection, or just GPS noise
// nudging the point sideways for one tick — the globally-nearest segment can
// legitimately run backward or across relative to true travel. The position
// still looks fine (still near a road) but the attached bearing (the
// segment's own direction) ends up 90–180° off, even though nothing else in
// the pipeline disagrees. `preferredFromIndex` (the previous tick's own
// `segmentIndex`) closes that gap: restrict the search to segments at or
// after that index, with a small backward tolerance for legitimate GPS
// correction, before ever falling back to the unrestricted global search.
const SEGMENT_BACKWARD_TOLERANCE = 1;

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
 *
 * `preferredFromIndex`, when given, biases the search toward continuing
 * forward from the previous tick's own `segmentIndex` (see the module-level
 * comment above) instead of a pure global-nearest search — the fix for a car
 * momentarily pointing backward or sideways while still snapped to the
 * route. If nothing within that window is close enough (off-route, or a
 * genuine reroute/restart past the tolerance), a full unrestricted search
 * runs instead, so this can never permanently prevent re-snapping.
 */
export function snapToRoute(
  point: TrackingLatLng,
  route: readonly TrackingLatLng[] | null | undefined,
  maxSnapMeters = 35,
  preferredFromIndex?: number | null,
): RouteSnapResult | null {
  if (!route || route.length < 2) return null;

  const cosLat = Math.cos(toRad(point.latitude));
  // Local meters-per-degree scale around the fix.
  const mPerDegLat = (Math.PI / 180) * EARTH_RADIUS_M;
  const mPerDegLng = mPerDegLat * cosLat;

  const px = point.longitude * mPerDegLng;
  const py = point.latitude * mPerDegLat;

  const nearestFrom = (startIndex: number): RouteSnapResult | null => {
    let best: RouteSnapResult | null = null;
    let bestDistSq = Infinity;

    for (let i = startIndex; i < route.length - 1; i++) {
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
    return best;
  };

  const minIndex =
    preferredFromIndex != null && Number.isFinite(preferredFromIndex)
      ? Math.max(0, Math.floor(preferredFromIndex) - SEGMENT_BACKWARD_TOLERANCE)
      : 0;

  let best = nearestFrom(minIndex);
  if ((!best || best.deviationMeters > maxSnapMeters) && minIndex > 0) {
    // Nothing close enough ahead of the continuity window — fall back to an
    // unrestricted search rather than refusing to re-snap.
    best = nearestFrom(0);
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

/**
 * Great-circle destination: the point `distanceM` meters from `origin` along
 * `bearingDeg`. Used by the course-up camera to shift the map center ahead of
 * the car so the car sits in the lower third of the screen, like every
 * navigation UI. Accurate to centimeters at the sub-kilometer distances used.
 */
export function destinationPoint(
  origin: TrackingLatLng,
  bearingDeg: number,
  distanceM: number,
): TrackingLatLng {
  const δ = distanceM / EARTH_RADIUS_M;
  const θ = toRad(bearingDeg);
  const φ1 = toRad(origin.latitude);
  const λ1 = toRad(origin.longitude);
  const φ2 = Math.asin(
    Math.sin(φ1) * Math.cos(δ) + Math.cos(φ1) * Math.sin(δ) * Math.cos(θ),
  );
  const λ2 =
    λ1 +
    Math.atan2(
      Math.sin(θ) * Math.sin(δ) * Math.cos(φ1),
      Math.cos(δ) - Math.sin(φ1) * Math.sin(φ2),
    );
  return { latitude: (φ2 * 180) / Math.PI, longitude: (λ2 * 180) / Math.PI };
}

/** Where a chosen bearing came from. `route`/`travel` are movement-derived. */
export type BearingSource = 'route' | 'travel' | 'heading' | 'none';

export interface BearingSelection {
  /** Compass bearing 0–359, or null when nothing trustworthy is available. */
  bearing: number | null;
  source: BearingSource;
}

/**
 * Choose which way the car should point for one position update.
 *
 * Priority: route segment → direction of travel → the reported GPS heading.
 *
 * Movement deliberately outranks the reported heading. A reported heading is
 * not trustworthy across platforms: iOS sets `CLLocation.course` to -1 when it
 * is invalid, but Android's `Location` carries a SEPARATE `hasBearing()` flag
 * and `getBearing()` returns `0.0` when that flag is false — so "this fix has
 * no course" reaches JS as a literal `0`, indistinguishable from genuinely
 * driving due north. While the reported heading ranked above the travel
 * fallback, that placeholder won every comparison and pinned the marker to
 * north on east–west streets while the car slid sideways across the map
 * (live-testing reports 2026-08-21 and 2026-08-28).
 *
 * Demoting it costs nothing for a driver who really is heading north: two
 * fixes apart on a northbound street measure ~0 from movement anyway.
 *
 * `hasMovementBearing` carries whether movement has EVER established a bearing
 * for this marker. Once it has, a reported heading is ignored entirely, so a
 * placeholder 0 arriving while the car waits at a light cannot spin a
 * known-westbound car back to north.
 *
 * Pure, so every branch is testable without a device or a map.
 */
export function selectBearing(params: {
  /** Result of snapping this fix to the route, or null when off-route/no route. */
  snap: RouteSnapResult | null;
  movedMeters: number;
  /** Previous rendered position — the travel bearing is measured from here. */
  from: TrackingLatLng;
  /** Position being rendered now. */
  to: TrackingLatLng;
  /** Raw `coords.heading` as reported by the platform. */
  heading: number | null | undefined;
  hasMovementBearing: boolean;
  minMoveMeters: number;
}): BearingSelection {
  const { snap, movedMeters, from, to, heading, hasMovementBearing, minMoveMeters } = params;

  if (snap && movedMeters >= minMoveMeters) {
    return { bearing: snap.bearing, source: 'route' };
  }
  if (movedMeters >= minMoveMeters) {
    return {
      bearing: bearingDegrees(from.latitude, from.longitude, to.latitude, to.longitude),
      source: 'travel',
    };
  }
  // Under the movement threshold (stopped, or GPS jitter). A reported heading
  // is the best evidence available only while movement has never produced one.
  const reported =
    heading != null && Number.isFinite(heading) && heading >= 0 ? heading : null;
  if (reported != null && !hasMovementBearing) {
    return { bearing: reported, source: 'heading' };
  }
  return { bearing: null, source: 'none' };
}
