/**
 * Validated pickup/dropoff coordinates for the ride map screens.
 *
 * react-native-maps crashes the native view (white screen, not catchable by a
 * JS ErrorBoundary) when it receives a non-finite latitude/longitude in
 * `initialRegion` / `<Marker coordinate>`. The ride object's coords can be
 * null/undefined for a beat right after booking, or arrive as numeric strings
 * from the API, so screens must validate before handing them to the map.
 *
 * Returns null when any of the four coords is missing or non-finite; callers
 * render a loading placeholder instead of mounting the map.
 *
 * Shared by driver-arriving.tsx and ride-in-progress.tsx so both ride map
 * surfaces guard the map identically.
 */

export type RideMapCoords = {
  pickupLat: number;
  pickupLng: number;
  dropoffLat: number;
  dropoffLng: number;
};

export function finiteNumber(value: unknown): number | null {
  const n = typeof value === 'number' ? value : typeof value === 'string' ? Number(value) : NaN;
  return Number.isFinite(n) ? n : null;
}

export function getRideMapCoords(ride: any): RideMapCoords | null {
  if (!ride) return null;
  const pickupLat = finiteNumber(ride.pickup_lat ?? ride.pickup?.lat);
  const pickupLng = finiteNumber(ride.pickup_lng ?? ride.pickup?.lng);
  const dropoffLat = finiteNumber(ride.dropoff_lat ?? ride.dropoff?.lat);
  const dropoffLng = finiteNumber(ride.dropoff_lng ?? ride.dropoff?.lng);
  if (pickupLat === null || pickupLng === null || dropoffLat === null || dropoffLng === null) {
    return null;
  }
  return { pickupLat, pickupLng, dropoffLat, dropoffLng };
}
