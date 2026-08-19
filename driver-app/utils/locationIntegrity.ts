import { LocationObject } from 'expo-location';
import { Platform } from 'react-native';

export type LocationIntegrityResult = {
  trusted: boolean;
  reason?: string;
};

export interface LocationIntegrityChecker {
  check(loc: LocationObject): LocationIntegrityResult;
  reset(): void;
}

const MAX_SPEED_MPS = 90; // ~324 km/h — faster than any car
const TELEPORT_THRESHOLD_KM = 5;
const TELEPORT_MIN_INTERVAL_MS = 5_000;

export function haversineKm(
  lat1: number, lng1: number, lat2: number, lng2: number
): number {
  const R = 6371;
  const dLat = ((lat2 - lat1) * Math.PI) / 180;
  const dLng = ((lng2 - lng1) * Math.PI) / 180;
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos((lat1 * Math.PI) / 180) *
      Math.cos((lat2 * Math.PI) / 180) *
      Math.sin(dLng / 2) ** 2;
  return R * 2 * Math.asin(Math.sqrt(a));
}

// Every checker ever created, so resetLocationIntegrity() can clear ALL
// producers' state in one call (go-offline, sign-out) without each call site
// having to know about the others.
const _registry = new Set<LocationIntegrityChecker>();

/**
 * Per-producer integrity checker.
 *
 * The teleport test compares against the PREVIOUS fix — which only means
 * anything when both fixes come from the same producer. This used to be one
 * module-global `_lastLocation` shared by four producers (background task,
 * Android Auto task at 2 s, dashboard watcher, GPS heartbeat); interleaved
 * cross-producer fixes landing < 5 s apart could trip `teleport_detected` on a
 * perfectly legitimate sample. Each producer now owns its state.
 *
 * NOTE (capture-before-filter): this check gates DISPLAY surfaces only — the
 * car marker, the dispatch WS marker, UI state. It must never sit in front of
 * durable capture (the trip outbox): the v2 point already carries `mocked` to
 * the server, and settlement filtering (backend utils/trip_distance.py) owns
 * billing quality.
 */
export function createLocationIntegrityChecker(): LocationIntegrityChecker {
  let lastLocation: { lat: number; lng: number; timestamp: number } | null = null;

  const checker: LocationIntegrityChecker = {
    check(loc: LocationObject): LocationIntegrityResult {
      // Android: direct mock location detection
      if (Platform.OS === 'android' && loc.mocked === true) {
        return { trusted: false, reason: 'mock_location_detected' };
      }

      // NOTE: accuracy is deliberately NOT a trust signal. Legitimate fixes
      // routinely report accuracy of null/0 (background & coarse fixes on many
      // devices) or > 200 m (urban canyon, tunnels, cold start). Rejecting on
      // accuracy dropped whole backgrounded trips — the completion fix was often
      // the only surviving point. Quality/outliers are handled server-side (the
      // complete_ride spike filter + OSRM road-snap), so here we only reject
      // genuine spoofing signals: mocked, physically impossible speed, teleport.

      // Speed check — impossible speeds suggest spoofing
      const speed = loc.coords.speed ?? 0;
      if (speed > MAX_SPEED_MPS) {
        return { trusted: false, reason: 'impossible_speed' };
      }

      // Teleportation check — jumping large distances in short time
      const now = loc.timestamp;
      const lat = loc.coords.latitude;
      const lng = loc.coords.longitude;

      if (lastLocation) {
        const elapsed = now - lastLocation.timestamp;
        if (elapsed > 0 && elapsed < TELEPORT_MIN_INTERVAL_MS) {
          const dist = haversineKm(lastLocation.lat, lastLocation.lng, lat, lng);
          if (dist > TELEPORT_THRESHOLD_KM) {
            lastLocation = { lat, lng, timestamp: now };
            return { trusted: false, reason: 'teleport_detected' };
          }
        }
      }

      lastLocation = { lat, lng, timestamp: now };
      return { trusted: true };
    },
    reset(): void {
      lastLocation = null;
    },
  };
  _registry.add(checker);
  return checker;
}

// Back-compat module-level checker for call sites not yet migrated to their
// own instance. Same behaviour as before the factory refactor.
const _defaultChecker = createLocationIntegrityChecker();

export function checkLocationIntegrity(loc: LocationObject): LocationIntegrityResult {
  return _defaultChecker.check(loc);
}

/** Reset EVERY producer's integrity state (go-offline, sign-out). */
export function resetLocationIntegrity(): void {
  for (const checker of _registry) checker.reset();
}
