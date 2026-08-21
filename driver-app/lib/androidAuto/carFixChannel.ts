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
/** When the CURRENT bearing was last established (not merely carried). */
let lastHeadingAt = 0;
/** How the CURRENT bearing was arrived at. Drives the on-screen readout. */
let lastHeadingSource: HeadingSource = 'none';

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
 * Bearing from `a` to `b` in degrees [0, 360). Great-circle initial bearing —
 * the same formula CarMarker.tsx uses for its own fallback, kept here so the
 * whole surface agrees on one course instead of two components each guessing.
 */
export function bearingBetween(a: CarLatLng, b: CarLatLng): number {
  const toRad = (d: number) => (d * Math.PI) / 180;
  const dLng = toRad(b.longitude - a.longitude);
  const y = Math.sin(dLng) * Math.cos(toRad(b.latitude));
  const x =
    Math.cos(toRad(a.latitude)) * Math.sin(toRad(b.latitude)) -
    Math.sin(toRad(a.latitude)) * Math.cos(toRad(b.latitude)) * Math.cos(dLng);
  return ((Math.atan2(y, x) * 180) / Math.PI + 360) % 360;
}

/**
 * Far enough between two fixes for the direction between them to BE a course.
 *
 * Below this, the "movement" is GPS noise and its bearing is random — which is
 * worse than no bearing at all, because a random bearing looks authoritative.
 * 10m is about half a second at road speed and several times typical urban
 * scatter.
 */
export const MIN_COURSE_MOVE_M = 10;

/**
 * Oldest previous fix a course may be derived FROM.
 *
 * Distance alone is not enough to make a direction a course: it also has to be
 * recent. `seedCarFix` can put a fix up to MAX_SEED_AGE_MS old — in the worst
 * case the position the driver logged in at — into the cache without stamping
 * the age clock, and a process resumed after a spell in the background holds a
 * fix from wherever it was suspended. Deriving across either baseline produces
 * the direction of the whole intervening journey, not of the car, and it would
 * then be treated as freshly established for CARRIED_HEADING_MAX_AGE_MS.
 *
 * 15s spans a couple of watchdog cycles, so a normally-delivering pipeline
 * always derives; anything longer is a gap we have no course for and should
 * say so.
 */
export const MAX_COURSE_BASELINE_AGE_MS = 15_000;

/**
 * How long a bearing survives with nothing to confirm it.
 *
 * ─── Why the car marker pointed the wrong way for a whole trip ───────────────
 * Only `watchPositionAsync` produces a course over ground; a one-shot
 * `getCurrentPositionAsync` almost never does, and reports 0 or -1 instead. The
 * car has THREE such one-shot paths (the startup fix, and the staleness
 * watchdog every 3s) where the phone dashboard has none — and on Android Auto
 * they are the paths that DOMINATE, because the phone is backgrounded in a
 * cradle and Android throttles its foreground watcher hard.
 *
 * The first version of this module answered that by carrying the last real
 * bearing forward — with no expiry. That fixed the marker snapping to north
 * every few seconds and replaced it with something worse: on a starved watcher
 * the bearing from one early reading (often the one taken pulling out of a
 * parking spot) was republished for the whole drive, so the car icon sat
 * pointing north up while the driver went west, through every turn. Being
 * non-null, it also SUPPRESSED CarMarker's own derive-from-travel fallback.
 *
 * So a carried bearing now expires. Past this age, if the driver has not moved
 * far enough for a derived course either, the honest answer is null — the map
 * goes north-up and the marker shows the direction of travel rather than a
 * confident lie. 12s is a couple of watchdog cycles: long enough to bridge a
 * gap between watcher callbacks, short enough that it cannot outlive a turn.
 */
export const CARRIED_HEADING_MAX_AGE_MS = 12_000;

/**
 * The bearing a fix should actually be stored with.
 *
 * Priority, best evidence first:
 *   1. A real GPS course on this fix.
 *   2. The direction from the previous fix, once the driver has moved
 *      MIN_COURSE_MOVE_M — and only when that fix is itself recent enough to
 *      be a baseline (MAX_COURSE_BASELINE_AGE_MS). This is the signal that is
 *      always available at road speed and always current — the one the old
 *      carry-forward hid.
 *   3. The previous bearing, but only while it is younger than
 *      CARRIED_HEADING_MAX_AGE_MS.
 *   4. null. The surface treats that as "no course", which is a state it draws
 *      correctly (north-up map, marker on travel direction).
 *
 * Pure, so all four branches are testable without a head unit. Returns the
 * fix AND how its bearing was arrived at — the caller needs that to know
 * whether the bearing's clock restarts (GPS/derived) or keeps running
 * (carried); an "is it a different number?" test gets this wrong the moment a
 * fresh reading happens to match the carried one.
 */
export type HeadingSource = 'gps' | 'derived' | 'carried' | 'none';

export function resolveHeading(
  next: CarLatLng,
  prev: CarLatLng | null,
  prevHeadingAgeMs: number,
  prevFixAgeMs: number = 0,
): { fix: CarLatLng; source: HeadingSource } {
  // Negative is expo's "unknown" sentinel; null is "provider gave nothing".
  const own = next.heading;
  if (typeof own === 'number' && Number.isFinite(own) && own >= 0) {
    return { fix: next, source: 'gps' };
  }

  if (
    prev &&
    prevFixAgeMs <= MAX_COURSE_BASELINE_AGE_MS &&
    metresBetween(prev, next) >= MIN_COURSE_MOVE_M
  ) {
    return { fix: { ...next, heading: bearingBetween(prev, next) }, source: 'derived' };
  }

  const carried = prev?.heading;
  if (
    typeof carried === 'number' &&
    Number.isFinite(carried) &&
    carried >= 0 &&
    prevHeadingAgeMs <= CARRIED_HEADING_MAX_AGE_MS
  ) {
    return { fix: { ...next, heading: carried }, source: 'carried' };
  }

  return { fix: { ...next, heading: null }, source: 'none' };
}

/**
 * Adopt a fix WITHOUT notifying subscribers.
 *
 * For the hook's own watcher and seeds, which already hold the value in React
 * state — re-publishing it would loop straight back into the setState that
 * produced it.
 *
 * Returns what was actually stored, whose bearing may be derived from movement
 * or carried from the previous fix (see `resolveHeading`) — callers should use
 * the return value for their own setState rather than the fix they passed in,
 * or the marker and the module cache disagree about which way the car points.
 */
export function adoptCarFix(fix: CarLatLng): CarLatLng {
  const now = Date.now();
  const { fix: merged, source } = resolveHeading(
    fix,
    lastFix,
    lastHeadingAt === 0 ? Infinity : now - lastHeadingAt,
    // Infinity for a seeded fix: seedCarFix deliberately never stamps the age
    // clock, so a seed can never become the baseline for a derived course.
    carFixAgeMs(),
  );
  // Stamped only when this fix ESTABLISHED a bearing (GPS course, or derived
  // from real movement). A carried one keeps the age of the reading it came
  // from, or it would renew itself on every watchdog tick and never expire —
  // which is exactly the bug this block exists to end.
  if (source === 'gps' || source === 'derived') lastHeadingAt = now;
  lastHeadingSource = source;
  lastFix = merged;
  lastFixAt = now;
  return merged;
}

/**
 * How the bearing currently in `lastFix` was arrived at.
 *
 * Read by the surface's heading readout: "which way am I pointing" is only half
 * the answer when the marker looks wrong — the other half is WHERE that number
 * came from, and 'gps' vs 'derived' vs 'carried' is the difference between a
 * healthy pipeline and one running on a two-fix guess.
 */
export const getHeadingSource = (): HeadingSource => lastHeadingSource;

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
  // Subscribers get the MERGED fix, not the raw one — a background task fix with
  // no course would otherwise re-render the marker pointing north even though
  // the module cache still holds the true bearing.
  const merged = adoptCarFix(fix);
  persistFix(merged);
  for (const l of fixListeners) {
    try {
      l(merged);
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
  lastHeadingAt = 0;
  lastHeadingSource = 'none';
  lastCacheWriteAt = 0;
  lastCachedPoint = null;
  publishedSinceRead = 0;
  fixListeners.clear();
}
