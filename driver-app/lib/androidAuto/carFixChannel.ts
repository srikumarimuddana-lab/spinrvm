/**
 * The driver's last known position, and the channel that carries new ones.
 *
 * Deliberately holds NO React import. It is imported by `utils/backgroundLocation.ts`
 * and `carLocationTask.ts`, both of which run as headless TaskManager handlers in a
 * JS context with no component tree — pulling React onto that path buys nothing and
 * costs bundle-load time on the exact cold start this whole feature exists to fix.
 *
 * Three consumers, one source of truth:
 *   - `useCarLocation`      subscribes and re-renders the car map
 *   - `register.ts`         reads `getLastCarFix()` for the SOS payload
 *   - the location tasks    call `publishCarFix()` from outside React
 *
 * Extracted from useCarLocation.ts; the reasoning in each comment below is
 * unchanged from where it was first written.
 */

export interface CarLatLng {
  latitude: number;
  longitude: number;
  /** Course over ground in degrees, for rotating the car marker. */
  heading: number | null;
}

/**
 * Shared last-known-location cache. Written by `app/login.tsx` at sign-in and by
 * `useDriverDashboard` while the phone dashboard is mounted; ALSO written here so
 * a car-only session isn't stuck with whatever the phone last left behind.
 */
export const LAST_LOCATION_KEY = 'spinr_driver_last_location';

// Every threshold below is set against ROAD SPEED, not against I/O cost. At
// 60 km/h a vehicle covers ~17m per second, so a window measured in tens of
// seconds is measured in hundreds of metres — which is the difference between
// "my car is here" and "the app has lost me". Earlier values (15 min seed age,
// 30s cache write, 12s staleness) were chosen thinking about disk churn and were
// wrong for a moving car.

/**
 * Oldest AsyncStorage seed we will draw.
 *
 * A driver who signed in at home and has not opened the dashboard since is
 * carrying a cache that literally holds WHERE THEY LOGGED IN — which is exactly
 * the "it shows where I started" symptom. There is no timestamp on the legacy
 * shape, so age is unknowable for those writes; the car now stamps its own, and
 * anything older than this is not drawn at all. Better a moment with no marker
 * than a confident marker in the wrong place.
 */
export const MAX_SEED_AGE_MS = 60_000; // ~1 km at road speed — the outer limit of useful

// DISTANCE leads, time is only a backstop. A moving car trips the distance rule
// long before the timer — 50m is ~3s at 60 km/h, which is the cadence that
// matters — while a parked one trips neither often, so the cache stays quiet
// instead of writing to disk every few seconds at a rank.
/** Backstop so a stationary driver's timestamp still refreshes occasionally. */
const CACHE_WRITE_INTERVAL_MS = 15_000;
/** Primary rule: this far from the last written point writes immediately. */
const CACHE_WRITE_DISTANCE_M = 50;

/** Metres between two coordinates (equirectangular — ample at these distances). */
export function metresBetween(a: CarLatLng, b: CarLatLng): number {
  const R = 6_371_000;
  const dLat = ((b.latitude - a.latitude) * Math.PI) / 180;
  const dLng = ((b.longitude - a.longitude) * Math.PI) / 180;
  const meanLat = ((a.latitude + b.latitude) / 2) * (Math.PI / 180);
  const x = dLng * Math.cos(meanLat);
  return Math.sqrt(dLat * dLat + x * x) * R;
}

/**
 * Last fix, held at MODULE scope so it outlives the component.
 *
 * The car surface is a React root inside a Presentation on a VirtualDisplay.
 * When the head unit takes the screen away — the reversing camera on a slow
 * manoeuvre is the common one — Android Auto destroys the surface and rebuilds
 * it on return, remounting the hook. With the fix held only in component state,
 * that remount dropped it to null, the camera fell back to the last-known
 * AsyncStorage value (or Saskatoon), and the map visibly jumped away and back
 * once the next GPS fix landed a few seconds later.
 *
 * Keeping it here means a remount re-renders at the driver's actual position
 * immediately. Same reasoning as carMapCamera.ts holding zoom outside the tree.
 */
let lastFix: CarLatLng | null = null;
/** When `lastFix` was set, so staleness is measurable. */
let lastFixAt = 0;

/**
 * The last fix, readable from outside React.
 *
 * `register.ts` runs outside the component tree and needs coordinates for the
 * emergency payload — an SOS without a position is far less use to a safety
 * team. Returns null when no fix has landed yet; the emergency endpoint takes
 * lat/lng as optional, so a positionless alert still sends rather than being
 * blocked on GPS.
 */
export const getLastCarFix = (): CarLatLng | null => lastFix;

/** Milliseconds since `lastFix` was set. `Infinity` when there has never been one. */
export const carFixAgeMs = (): number => (lastFixAt === 0 ? Infinity : Date.now() - lastFixAt);

/**
 * Adopt a fix WITHOUT notifying subscribers.
 *
 * For the hook's own watcher and seeds, which already hold the value in React
 * state — re-publishing it would loop straight back into the setState that
 * produced it.
 */
export function adoptCarFix(fix: CarLatLng): void {
  lastFix = fix;
  lastFixAt = Date.now();
}

/** Seed the module cache only if nothing better has landed. Returns what won. */
export function seedCarFix(fix: CarLatLng): CarLatLng {
  const chosen = lastFix ?? fix;
  lastFix = chosen;
  // Deliberately does NOT touch lastFixAt: a seed is stale by construction, and
  // stamping it "now" would tell the staleness watchdog everything is fine.
  return chosen;
}

/**
 * Fixes published since the last read, for measuring whether a location task is
 * actually delivering.
 *
 * This exists because of a specific unknown: when Android refuses a background
 * foreground-service start, carLocationTask falls back to a plain background
 * task, and whether Android then throttles it depends on our process importance
 * while Android Auto has our CarAppService bound. That is not answerable from
 * source — only from a real head unit. Counting arrivals is how we find out.
 */
let publishedSinceRead = 0;

/** Read and reset the counter. Called on carSession's refresh tick. */
export function consumeFixCount(): number {
  const n = publishedSinceRead;
  publishedSinceRead = 0;
  return n;
}

/** Subscribers notified when a fix arrives from OUTSIDE the React tree. */
const fixListeners = new Set<(fix: CarLatLng) => void>();

/**
 * Listen for fixes published by the headless location tasks.
 *
 * A plain module variable is not enough for the surface — React would never
 * re-render — so the tasks push, and the mounted hook pulls through this.
 */
export function subscribeCarFix(listener: (fix: CarLatLng) => void): () => void {
  fixListeners.add(listener);
  return () => {
    fixListeners.delete(listener);
  };
}

/**
 * Publish a fix from a background task. Updates the module cache, refreshes the
 * shared AsyncStorage entry, and re-renders any mounted surface.
 */
export function publishCarFix(fix: CarLatLng): void {
  publishedSinceRead += 1;
  adoptCarFix(fix);
  persistFix(fix);
  for (const l of fixListeners) {
    try {
      l(fix);
    } catch {
      // A bad subscriber must not stop the others.
    }
  }
}

let lastCacheWriteAt = 0;
let lastCachedPoint: CarLatLng | null = null;

/**
 * Refresh the shared last-location cache from the car.
 *
 * The car was a pure READER of a cache only the phone ever wrote, so on a
 * car-only session it could never go stale-proof: whatever login wrote hours ago
 * was all the next cold start had. Writing here means the cache reflects where
 * the driver actually is, and the `at` stamp lets the reader reject anything
 * old. Throttled — a disk write every 2s would be pointless churn.
 */
export function persistFix(fix: CarLatLng, force = false): void {
  const now = Date.now();
  if (!force) {
    const elapsed = now - lastCacheWriteAt;
    const moved = lastCachedPoint ? metresBetween(lastCachedPoint, fix) : Infinity;
    // Either condition is enough: a driver moving fast trips the distance rule
    // long before the timer, and one sitting still trips neither.
    if (elapsed < CACHE_WRITE_INTERVAL_MS && moved < CACHE_WRITE_DISTANCE_M) return;
  }
  lastCacheWriteAt = now;
  lastCachedPoint = fix;
  try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const AsyncStorage = require('@react-native-async-storage/async-storage').default;
    // `lat`/`lng` keep the exact shape useDriverDashboard.ts:444 and login.tsx
    // read; `at` is additive and those readers destructure only lat/lng, so it
    // is invisible to them.
    AsyncStorage.setItem(
      LAST_LOCATION_KEY,
      JSON.stringify({ lat: fix.latitude, lng: fix.longitude, at: now }),
    ).catch(() => {});
  } catch {
    // AsyncStorage absent (tests/web) — the cache simply is not refreshed.
  }
}

/** @internal Test-only — reset module state between cases. */
export function _resetCarFixChannel(): void {
  lastFix = null;
  lastFixAt = 0;
  lastCacheWriteAt = 0;
  lastCachedPoint = null;
  publishedSinceRead = 0;
  fixListeners.clear();
}
