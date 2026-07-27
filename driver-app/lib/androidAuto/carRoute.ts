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
 * module or a head unit (the rendering/template layer lives in register.ts).
 */
import type { ActiveRide, RideState } from '../../store/driverStore';

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
    // Only draw the stored line on the DROPOFF leg — `planned_route_polyline` is
    // the pickup→dropoff trip route, captured at creation. Pre-pickup (driver→
    // pickup) we have no stored line, so drawing it would show a route leaving
    // the pickup toward the dropoff while the hand-off targets the pickup —
    // misleading exactly when the driver is heading to the rider. Marker only.
    polyline: toDropoff ? extractPolyline(activeRide) : [],
  };
}

/**
 * Deep-link URL that hands live turn-by-turn to the driver's nav app. Launching
 * it while the head unit is connected brings the nav app up on the car screen.
 * The scheme is per-platform because the SAME app uses different schemes:
 *   - google → Android `google.navigation:` intent; iOS `comgooglemaps://`
 *              (Google Maps supports CarPlay — offered when installed)
 *   - apple  → Apple Maps directions (CarPlay-native, always present on iOS)
 *   - waze   → `waze://` on iOS, Waze universal link on Android (web fallback)
 */
export function buildHandoffUrl(provider: NavProvider, dest: LatLng, os: string): string {
  const { latitude: la, longitude: lo } = dest;
  const ios = os === 'ios';
  switch (provider) {
    case 'apple':
      return `maps://?daddr=${la},${lo}&dirflg=d`;
    case 'google':
      return ios
        ? `comgooglemaps://?daddr=${la},${lo}&directionsmode=driving`
        : `google.navigation:q=${la},${lo}`;
    case 'waze':
    default:
      return ios
        ? `waze://?ll=${la},${lo}&navigate=yes`
        : `https://waze.com/ul?ll=${la},${lo}&navigate=yes`;
  }
}

const BUTTON: Record<NavProvider, NavButton> = {
  apple: { id: 'nav-apple', provider: 'apple' },
  google: { id: 'nav-google', provider: 'google' },
  waze: { id: 'nav-waze', provider: 'waze' },
};

/**
 * Synchronous fallback button set, shown until `resolveNavButtons` finishes its
 * install check. Uses only the guaranteed-present app per platform so the car
 * never shows a dead button: Apple Maps on CarPlay, Google on Android Auto.
 */
export function defaultNavButtons(os: string): NavButton[] {
  return os === 'ios' ? [BUTTON.apple] : [BUTTON.google, BUTTON.waze];
}

/**
 * The nav hand-off buttons to show on the car map. On Android Auto this is
 * static (Google + Waze). On CarPlay we only advertise apps the driver actually
 * has — Apple Maps is always present; Google Maps and Waze are added only when
 * `canOpen` confirms their scheme resolves (requires the schemes in
 * LSApplicationQueriesSchemes, to be added when iOS CarPlay is wired up). This is
 * why a driver with Google Maps on CarPlay gets a Google button, one without does
 * not. Android-only today (register.ts uses the static defaultNavButtons); kept
 * for the dormant iOS CarPlay path.
 */
export async function resolveNavButtons(
  os: string,
  canOpen: (url: string) => Promise<boolean>
): Promise<NavButton[]> {
  if (os !== 'ios') return [BUTTON.google, BUTTON.waze];
  const buttons: NavButton[] = [BUTTON.apple];
  const [hasGoogle, hasWaze] = await Promise.all([
    canOpen('comgooglemaps://').catch(() => false),
    canOpen('waze://').catch(() => false),
  ]);
  if (hasGoogle) buttons.push(BUTTON.google);
  if (hasWaze) buttons.push(BUTTON.waze);
  return buttons;
}
