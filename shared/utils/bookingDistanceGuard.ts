/**
 * Client-side guard against a mis-resolved dropoff coordinate.
 *
 * Recent-search / saved-place reuse can attach stale or wrong coordinates to a
 * dropoff while its address string still reads correctly. The tell-tale is a
 * dropoff whose coordinate sits almost on top of the pickup even though the two
 * addresses differ — nobody books a sub-100 m ride to a differently-named
 * place. Blocking that at submit forces a re-selection instead of creating a
 * ride priced on a bogus coordinate.
 *
 * Pure + framework-free so it can be unit-tested and shared by every booking
 * entry point (search, recent, AI proposal) at the single funnel.
 */

export interface GuardCoord {
  lat: number;
  lng: number;
  address?: string | null;
}

// A dropoff within this many metres of the pickup, with a materially different
// address, is treated as a mis-resolved pin. Deliberately tight to avoid
// blocking genuine (if rare) very-short trips.
export const DROPOFF_MIN_METERS = 100;

const EARTH_RADIUS_M = 6_371_000;

function toRad(deg: number): number {
  return (deg * Math.PI) / 180;
}

/** Great-circle distance in metres between two coordinates. */
export function haversineMeters(a: GuardCoord, b: GuardCoord): number {
  const dLat = toRad(b.lat - a.lat);
  const dLng = toRad(b.lng - a.lng);
  const lat1 = toRad(a.lat);
  const lat2 = toRad(b.lat);
  const h =
    Math.sin(dLat / 2) ** 2 + Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLng / 2) ** 2;
  return EARTH_RADIUS_M * 2 * Math.atan2(Math.sqrt(h), Math.sqrt(1 - h));
}

function normalizeAddress(value: string | null | undefined): string {
  return (value ?? '').trim().toLowerCase().replace(/\s+/g, ' ');
}

/**
 * True when the dropoff coordinate looks mis-resolved: it is within
 * `minMeters` of the pickup while the two addresses differ materially. Returns
 * false when either coordinate is missing, when the coordinates are far enough
 * apart to be plausible, or when the addresses match (a legitimate there-only
 * or same-place booking).
 */
export function dropoffLikelyMisresolved(
  pickup: GuardCoord | null | undefined,
  dropoff: GuardCoord | null | undefined,
  options: { minMeters?: number } = {},
): boolean {
  if (!pickup || !dropoff) return false;
  if (
    !Number.isFinite(pickup.lat) ||
    !Number.isFinite(pickup.lng) ||
    !Number.isFinite(dropoff.lat) ||
    !Number.isFinite(dropoff.lng)
  ) {
    return false;
  }
  const minMeters = options.minMeters ?? DROPOFF_MIN_METERS;
  if (haversineMeters(pickup, dropoff) >= minMeters) return false;

  const pickupAddress = normalizeAddress(pickup.address);
  const dropoffAddress = normalizeAddress(dropoff.address);
  // Can't judge without both addresses; matching addresses = legitimate.
  if (!pickupAddress || !dropoffAddress) return false;
  return pickupAddress !== dropoffAddress;
}
