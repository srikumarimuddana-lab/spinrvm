import { LocationObject } from 'expo-location';
import { Platform } from 'react-native';

export type LocationIntegrityResult = {
  trusted: boolean;
  reason?: string;
};

const MAX_SPEED_MPS = 90; // ~324 km/h — faster than any car
const TELEPORT_THRESHOLD_KM = 5;
const TELEPORT_MIN_INTERVAL_MS = 5_000;

let _lastLocation: { lat: number; lng: number; timestamp: number } | null = null;

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

export function checkLocationIntegrity(loc: LocationObject): LocationIntegrityResult {
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

  if (_lastLocation) {
    const elapsed = now - _lastLocation.timestamp;
    if (elapsed > 0 && elapsed < TELEPORT_MIN_INTERVAL_MS) {
      const dist = haversineKm(_lastLocation.lat, _lastLocation.lng, lat, lng);
      if (dist > TELEPORT_THRESHOLD_KM) {
        _lastLocation = { lat, lng, timestamp: now };
        return { trusted: false, reason: 'teleport_detected' };
      }
    }
  }

  _lastLocation = { lat, lng, timestamp: now };
  return { trusted: true };
}

export function resetLocationIntegrity(): void {
  _lastLocation = null;
}
