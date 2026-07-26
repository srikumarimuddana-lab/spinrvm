/**
 * Car route selection + nav hand-off (Path B *look*, Path A *cost*).
 *
 * The Android Auto map draws the shared uniform route spec: a single straight
 * pickup→dropoff line (orange→red gradient, via @shared RouteLine), so there is
 * no live Routes/Directions API spend. Turn-by-turn itself is handed off to the
 * driver's Google Maps / Waze (free, their account) — mirroring the existing
 * `openNavigation()` deep link in `hooks/useDriverDashboard.ts`.
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
  /** Ride pickup — orange end of the uniform straight route line. */
  pickup: LatLng;
  /** Ride dropoff — red end of the uniform straight route line. */
  dropoff: LatLng;
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
  const pickup: LatLng = { latitude: ride.pickup_lat, longitude: ride.pickup_lng };
  const dropoff: LatLng = { latitude: ride.dropoff_lat, longitude: ride.dropoff_lng };
  const destination: LatLng = toDropoff ? dropoff : pickup;

  if (!Number.isFinite(destination.latitude) || !Number.isFinite(destination.longitude)) {
    return null;
  }

  return {
    destination,
    destinationLabel: toDropoff
      ? ride.dropoff_address || 'Dropoff'
      : ride.pickup_address || 'Pickup',
    leg: toDropoff ? 'dropoff' : 'pickup',
    // Uniform route spec: the map draws ONE straight pickup→dropoff line
    // (orange→red gradient) regardless of leg. The stored road polyline is no
    // longer rendered here; GPS reconstruction still feeds distance + the audit.
    pickup,
    dropoff,
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
