/**
 * Bounded location acquisition for the SOS critical path.
 *
 * A high-accuracy GPS fix can take 30s+ or never resolve indoors, in
 * parkades, or urban canyons — precisely where SOS gets pressed. The
 * emergency alert must reach the backend regardless, so location lookup is
 * hard-bounded: race the fresh fix against a short deadline, fall back to
 * the OS's cached last-known position (stale coords beat none in an
 * emergency), and finally proceed with no coordinates at all — the backend
 * tolerates a missing lat/lng on the SOS endpoint.
 */
import * as Location from 'expo-location';

/** Max wait for a fresh high-accuracy fix before falling back. */
export const SOS_FRESH_FIX_TIMEOUT_MS = 3000;
/** Max wait for the cached last-known position (reads local state; should be
 * near-instant, but nothing on this path is allowed to hang). */
export const SOS_LAST_KNOWN_TIMEOUT_MS = 1000;

export interface SOSCoords {
  lat?: number;
  lng?: number;
}

function withTimeout<T>(promise: Promise<T>, ms: number): Promise<T | null> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const deadline = new Promise<null>((resolve) => {
    timer = setTimeout(() => resolve(null), ms);
  });
  return Promise.race([
    // Swallow rejection into null: on this path any location failure means
    // "proceed without coords", never "block or throw before the alert".
    promise.catch(() => null),
    deadline,
  ]).finally(() => {
    if (timer !== undefined) clearTimeout(timer);
  });
}

/**
 * Resolve the best coordinates available within a hard time bound.
 * Never throws and never takes longer than roughly
 * `freshFixTimeoutMs + SOS_LAST_KNOWN_TIMEOUT_MS`.
 */
export async function getSOSLocation(
  freshFixTimeoutMs: number = SOS_FRESH_FIX_TIMEOUT_MS
): Promise<SOSCoords> {
  const fresh = await withTimeout(
    Location.getCurrentPositionAsync({ accuracy: Location.Accuracy.High }),
    freshFixTimeoutMs
  );
  if (fresh) {
    return { lat: fresh.coords.latitude, lng: fresh.coords.longitude };
  }

  const lastKnown = await withTimeout(
    Location.getLastKnownPositionAsync(),
    SOS_LAST_KNOWN_TIMEOUT_MS
  );
  if (lastKnown) {
    return { lat: lastKnown.coords.latitude, lng: lastKnown.coords.longitude };
  }

  return {};
}
