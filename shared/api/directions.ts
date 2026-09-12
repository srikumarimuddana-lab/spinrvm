/**
 * Cross-surface client for the backend's Directions proxy (R7,
 * docs/audit/ride-experience/ROADMAP.md): GET /maps/directions.
 *
 * Rider-app/driver-app screens that render a `MapViewDirections` fallback
 * line (used only when the backend hasn't already supplied a route
 * polyline) call this instead of Google directly once
 * `app_settings.directions_proxy_enabled` is on for that call site — the
 * server-held Maps key never reaches the device for that request. Every
 * migrated call site must keep the on-device `MapViewDirections` fallback
 * for when this call itself fails (network error, proxy 5xx, budget
 * exhaustion) — see the roadmap item's "do not remove the fallback
 * outright" note. This function throws on any such failure so callers can
 * catch it and fall through to `MapViewDirections` unchanged.
 */
import api from './client';

export interface LatLng {
  latitude: number;
  longitude: number;
}

/** Same shape a `MapViewDirections.onReady` callback provides, so an
 * existing onReady handler can be reused with only its data source swapped. */
export interface DirectionsRouteResult {
  coordinates: LatLng[];
  /** Kilometres, or null if the backend couldn't compute it. */
  distance: number | null;
  /** Minutes, or null if the backend couldn't compute it. */
  duration: number | null;
}

interface DirectionsProxyResponse {
  coordinates: [number, number][];
  distance_km: number | null;
  duration_minutes: number | null;
}

const fmt = (point: LatLng): string => `${point.latitude},${point.longitude}`;

/**
 * Fetch a road route via the backend Directions proxy.
 *
 * `waypoints` order is sent exactly as given — the backend never optimizes
 * it (a rider's stops are priced/dispatched in the order they were entered).
 * Rejects (throws) on any network/backend failure; never returns a partial
 * or empty-but-successful result to paper over one.
 */
export async function fetchDirectionsRoute(
  origin: LatLng,
  destination: LatLng,
  waypoints?: LatLng[],
): Promise<DirectionsRouteResult> {
  const params = new URLSearchParams();
  params.set('origin', fmt(origin));
  params.set('destination', fmt(destination));
  if (waypoints && waypoints.length > 0) {
    params.set('waypoints', waypoints.map(fmt).join('|'));
  }

  const { data } = await api.get<DirectionsProxyResponse>(`/maps/directions?${params.toString()}`);

  return {
    coordinates: (data.coordinates || []).map(([latitude, longitude]) => ({ latitude, longitude })),
    distance: data.distance_km ?? null,
    duration: data.duration_minutes ?? null,
  };
}
