/**
 * Car route selection + nav hand-off (Path B *look*, Path A *cost*).
 *
 * The Android Auto map shows the route we ALREADY stored at ride creation
 * (`rides.planned_route_polyline`, migration 100 — a decoded `[[lat,lng], …]`
 * line), so there is no live Routes/Directions API spend. Turn-by-turn itself is
 * handed off to the driver's Google Maps / Waze (free, their account) — mirroring
 * the existing `openNavigation()` deep link in `hooks/useDriverDashboard.ts`.
 *
 * Pure + framework-free so it is fully unit-testable without the native car
 * module or a head unit (the rendering/template layer lives in car/carNav.tsx).
 */
import type { ActiveRide, RideState } from '../store/driverStore';

export type LatLng = { latitude: number; longitude: number };
export type NavProvider = 'google' | 'apple' | 'waze';
export type NavButton = { id: string; provider: NavProvider };

export type CarRoute = {
  /** Where the driver is currently headed: pickup before the trip, dropoff during it. */
  destination: LatLng;
  destinationLabel: string;
  /** Which marker the destination corresponds to. Also used to debounce template rebuilds. */
  leg: 'pickup' | 'dropoff';
  /** Stored pickup→dropoff route line. May be empty if it was never captured. */
  polyline: LatLng[];
};

// Ride states during which the car shows a route + a "navigate" hand-off.
const NAV_STATES = new Set<RideState>([
  'navigating_to_pickup',
  'arrived_at_pickup',
  'trip_in_progress',
]);

export function isNavState(state: RideState): boolean {
  return NAV_STATES.has(state);
}

/**
 * Pull the stored `[[lat,lng], …]` polyline off the active ride, tolerating the
 * store's loose typing — it rides along on `res.data` via RideInfo's index
 * signature, so it isn't on the declared ActiveRide shape. Junk entries are
 * dropped rather than rendered as (0,0).
 */
export function extractPolyline(activeRide: ActiveRide | null): LatLng[] {
  if (!activeRide) return [];
  const ride = activeRide.ride as Record<string, unknown> | undefined;
  const raw =
    (ride?.planned_route_polyline as unknown) ??
    ((activeRide as unknown as Record<string, unknown>).planned_route_polyline as unknown) ??
    null;
  if (!Array.isArray(raw)) return [];
  const out: LatLng[] = [];
  for (const p of raw) {
    if (
      Array.isArray(p) &&
      p.length >= 2 &&
      Number.isFinite(p[0]) &&
      Number.isFinite(p[1])
    ) {
      out.push({ latitude: p[0] as number, longitude: p[1] as number });
    }
  }
  return out;
}

/**
 * Decide what the car map should show for the current ride state. Destination is
 * the pickup before the trip starts and the dropoff once `in_progress`. Returns
 * null when there is no active ride, it isn't a navigation state, or the
 * destination coords are missing (surface nothing rather than a bogus pin).
 */
export function selectCarRoute(
  state: RideState,
  activeRide: ActiveRide | null
): CarRoute | null {
  if (!activeRide || !isNavState(state)) return null;
  const ride = activeRide.ride;
  if (!ride) return null;

  const toDropoff = state === 'trip_in_progress';
  const destination: LatLng = toDropoff
    ? { latitude: ride.dropoff_lat, longitude: ride.dropoff_lng }
    : { latitude: ride.pickup_lat, longitude: ride.pickup_lng };

  if (!Number.isFinite(destination.latitude) || !Number.isFinite(destination.longitude)) {
    return null;
  }

  return {
    destination,
    destinationLabel: toDropoff
      ? ride.dropoff_address || 'Dropoff'
      : ride.pickup_address || 'Pickup',
    leg: toDropoff ? 'dropoff' : 'pickup',
    polyline: extractPolyline(activeRide),
  };
}

/**
 * Deep-link URL that hands live turn-by-turn to the driver's nav app. Launching
 * it while the head unit is connected brings the nav app up on the car screen.
 *   - google → Android `google.navigation:` intent (mirrors openNavigation())
 *   - apple  → Apple Maps directions (CarPlay-native, always installed on iOS)
 *   - waze   → Waze universal link (works on both platforms if installed)
 */
export function buildHandoffUrl(provider: NavProvider, dest: LatLng): string {
  const { latitude, longitude } = dest;
  switch (provider) {
    case 'waze':
      return `https://waze.com/ul?ll=${latitude},${longitude}&navigate=yes`;
    case 'apple':
      return `maps://?daddr=${latitude},${longitude}&dirflg=d`;
    case 'google':
    default:
      return `google.navigation:q=${latitude},${longitude}`;
  }
}

/**
 * The nav hand-off buttons to show on the car map, per platform: Android Auto
 * offers Google + Waze, CarPlay offers Apple Maps + Waze (Apple Maps is the
 * CarPlay-native, guaranteed-present option). The button id round-trips through
 * the head unit's press event back to `providerForButton`.
 */
export function navButtonsForPlatform(os: string): NavButton[] {
  return os === 'ios'
    ? [
        { id: 'nav-apple', provider: 'apple' },
        { id: 'nav-waze', provider: 'waze' },
      ]
    : [
        { id: 'nav-google', provider: 'google' },
        { id: 'nav-waze', provider: 'waze' },
      ];
}

/** Resolve a pressed map-button id back to its nav provider (falls back to the
 *  platform default if the id is unrecognized). */
export function providerForButton(os: string, id: string): NavProvider {
  const found = navButtonsForPlatform(os).find((b) => b.id === id);
  if (found) return found.provider;
  return os === 'ios' ? 'apple' : 'google';
}
