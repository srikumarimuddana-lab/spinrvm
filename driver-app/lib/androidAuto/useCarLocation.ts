/**
 * Live driver location for the Android Auto map surface.
 *
 * The phone's location pipeline lives in `useDriverDashboard` *component* state,
 * which is not mounted on a car-only cold launch (Android Auto can start the JS
 * context with no phone UI). So the car surface can't read it — it watches its
 * own fix here instead. This is a second, lightweight foreground watcher; the OS
 * coalesces GPS so it does not double the sensor cost.
 *
 * - Seeds from the dashboard's last-known fix in AsyncStorage
 *   (`spinr_driver_last_location`) so the map centers instantly, before the first
 *   live fix arrives.
 * - Only watches when foreground permission is ALREADY granted; it never prompts
 *   (a permission dialog triggered from the car surface would be a bad surprise).
 * - expo-location / AsyncStorage are lazy-required so this degrades to `null` in
 *   tests / web / Expo Go instead of crashing, matching carSurface.tsx.
 */
import { useEffect, useRef, useState } from 'react';

export interface CarLatLng {
  latitude: number;
  longitude: number;
  /** Course over ground in degrees, for rotating the car marker. */
  heading: number | null;
}

const LAST_LOCATION_KEY = 'spinr_driver_last_location';

/**
 * Oldest AsyncStorage seed we will draw.
 *
 * That cache is written in exactly two places, neither of them here:
 * `app/login.tsx` at sign-in, and `useDriverDashboard.ts` while the phone
 * dashboard is mounted. A driver who signed in at home and has not opened the
 * dashboard since is carrying a cache that literally holds WHERE THEY LOGGED IN
 * — which is exactly the "it shows where I started" symptom. There is no
 * timestamp on the legacy shape, so age is unknowable for those writes; the car
 * now stamps its own, and anything older than this is not drawn at all. Better a
 * moment with no marker than a confident marker in the wrong place.
 */
// Every threshold below is set against ROAD SPEED, not against I/O cost. At
// 60 km/h a vehicle covers ~17m per second, so a window measured in tens of
// seconds is measured in hundreds of metres — which is the difference between
// "my car is here" and "the app has lost me". Earlier values (15 min seed age,
// 30s cache write, 12s staleness) were chosen thinking about disk churn and were
// wrong for a moving car.
const MAX_SEED_AGE_MS = 60_000; // ~1 km at road speed — the outer limit of useful

// DISTANCE leads, time is only a backstop. A moving car trips the distance rule
// long before the timer — 50m is ~3s at 60 km/h, which is the cadence that
// matters — while a parked one trips neither often, so the cache stays quiet
// instead of writing to disk every few seconds at a rank.
/** Backstop so a stationary driver's timestamp still refreshes occasionally. */
const CACHE_WRITE_INTERVAL_MS = 15_000;
/** Primary rule: this far from the last written point writes immediately. */
const CACHE_WRITE_DISTANCE_M = 50;

/** A fix older than this is treated as stale and actively refreshed. */
const STALE_AFTER_MS = 5_000;
/** How often staleness is checked. Cheap — it usually decides to do nothing. */
const WATCHDOG_INTERVAL_MS = 3_000;

/** Metres between two coordinates (equirectangular — ample at these distances). */
function metresBetween(a: CarLatLng, b: CarLatLng): number {
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
 * it on return, remounting this hook. With the fix held only in component
 * state, that remount dropped it to null, the camera fell back to the last-known
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
function persistFix(fix: CarLatLng, force = false): void {
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

export function useCarLocation(): CarLatLng | null {
  const [loc, setLoc] = useState<CarLatLng | null>(lastFix);
  const subRef = useRef<{ remove: () => void } | null>(null);

  useEffect(() => {
    let cancelled = false;

    let Location: typeof import('expo-location') | null = null;
    try {
      // eslint-disable-next-line @typescript-eslint/no-require-imports
      Location = require('expo-location');
    } catch {
      Location = null;
    }

    // FIRST seed: the OS's own cached fix.
    //
    // This is what makes a car-only cold launch work. When Android Auto starts
    // the app with the phone UI closed, the process is not foregrounded, so
    // watchPositionAsync below may deliver nothing at all — Android throttles
    // foreground location for a backgrounded app with no foreground service,
    // which is exactly the state a driver who is offline or has force-closed
    // the app is in. getLastKnownPositionAsync reads the OS cache instead of
    // opening a session, so it returns immediately and works regardless.
    //
    // Without it the map fell back to the AsyncStorage value the PHONE writes —
    // absent entirely if the app has never run the dashboard — and then to
    // Saskatoon, which is simply the wrong city for most drivers.
    (async () => {
      if (!Location) return;
      try {
        const { status } = await Location.getForegroundPermissionsAsync();
        if (cancelled || status !== 'granted') return;
        const last = await Location.getLastKnownPositionAsync();
        if (cancelled || !last?.coords) return;
        const next = {
          latitude: last.coords.latitude,
          longitude: last.coords.longitude,
          heading: last.coords.heading ?? null,
        };
        setLoc((prev) => {
          const chosen = prev ?? next;
          lastFix = chosen;
          return chosen;
        });
      } catch {
        // No cached fix — the AsyncStorage seed and live watcher still apply.
      }
    })();

    // SECOND: actively request a CURRENT fix, and let it overwrite the cache.
    //
    // This is the fix for "the map shows where I started". The other seeds and
    // the module-level lastFix are all *stale by construction* — they exist so a
    // remount has something to draw immediately. But a remount is exactly what
    // happens every time the car's reversing/side camera takes the screen and
    // gives it back, and on that path the driver has usually MOVED. Showing them
    // a several-minute-old position as though it were current is worse than
    // showing nothing: it looks like the app has lost the plot.
    //
    // watchPositionAsync alone cannot cover this. It re-subscribes on remount and
    // its first callback can be many seconds out, and while the phone app is
    // backgrounded (idle driver, screen off) Android throttles foreground updates
    // hard — which is why the position could sit stale for minutes. A direct
    // request is serviced immediately where a subscription is not.
    //
    // Unlike the seeds below, this one DOES overwrite: a freshly-requested fix is
    // by definition better than anything cached.
    (async () => {
      if (!Location) return;
      try {
        const { status } = await Location.getForegroundPermissionsAsync();
        if (cancelled || status !== 'granted') return;
        const current = await Location.getCurrentPositionAsync({
          accuracy: Location.Accuracy.High,
        });
        if (cancelled || !current?.coords) return;
        const next = {
          latitude: current.coords.latitude,
          longitude: current.coords.longitude,
          heading: current.coords.heading ?? null,
        };
        lastFix = next;
        lastFixAt = Date.now();
        setLoc(next);
        persistFix(next, true); // bypass throttle: freshest thing we will have
      } catch {
        // Timed out or unavailable — the cached seeds and the watcher stand.
      }
    })();

    // Seed the camera from the last-known fix the phone pipeline persisted, so an
    // idle car screen opens centered on the driver instead of waiting for GPS.
    (async () => {
      try {
        // eslint-disable-next-line @typescript-eslint/no-require-imports
        const AsyncStorage = require('@react-native-async-storage/async-storage').default;
        const raw = await AsyncStorage.getItem(LAST_LOCATION_KEY);
        if (cancelled || !raw) return;
        const { lat, lng, at } = JSON.parse(raw) as { lat?: number; lng?: number; at?: number };
        // `at` is absent on the legacy login/dashboard writes. Treat undatable
        // entries as too old to trust: those are precisely the ones that can be
        // hours stale. Once the car has written once, this cache is fresh.
        const ageOk = typeof at === 'number' && Date.now() - at < MAX_SEED_AGE_MS;
        if (ageOk && Number.isFinite(lat) && Number.isFinite(lng)) {
          // Don't clobber a live fix that may have already landed — including
          // one carried across a surface remount in `lastFix`.
          const seeded = { latitude: lat as number, longitude: lng as number, heading: null };
          setLoc((prev) => {
            const next = prev ?? lastFix ?? seeded;
            lastFix = next;
            return next;
          });
        }
      } catch {
        // No last-known fix yet — the live watcher below will center us.
      }
    })();

    (async () => {
      if (!Location) return;
      try {
        const { status } = await Location.getForegroundPermissionsAsync();
        if (cancelled || status !== 'granted') return;
        const sub = await Location.watchPositionAsync(
          {
            // Matches TRIP_CADENCE in utils/backgroundLocation.ts rather than the
            // idle cadence. The car surface is a moving-vehicle view the driver
            // is actively looking at, and Balanced accuracy can fall back to
            // network positioning — which is both coarse and, critically,
            // carries no bearing, so the car marker had nothing to rotate to.
            accuracy: Location.Accuracy.High,
            timeInterval: 2000,
            distanceInterval: 5,
          },
          (p) => {
            const next = {
              latitude: p.coords.latitude,
              longitude: p.coords.longitude,
              heading: p.coords.heading ?? null,
            };
            lastFix = next; // survives the next surface teardown/rebuild
            lastFixAt = Date.now();
            setLoc(next);
            persistFix(next);
          },
        );
        if (cancelled) {
          sub.remove();
          return;
        }
        subRef.current = sub;
      } catch {
        // Sensor unavailable / permission revoked mid-session — keep last-known.
      }
    })();

    // Watchdog against a starved subscription.
    //
    // watchPositionAsync is not a guarantee. While the phone app is backgrounded
    // — an idle driver with the screen off, which is most of a shift spent
    // cruising for work — Android throttles foreground location updates heavily,
    // and the callback can go quiet for minutes. The marker then sits at a
    // position the driver left long ago, which is precisely what makes the car
    // screen look broken.
    //
    // So: if no fix has landed in STALE_AFTER_MS, ask for one directly. Direct
    // requests are serviced where a subscription is throttled. This does nothing
    // while the watcher is healthy — it only spends battery on the exact failure
    // it exists to cover, and Android Auto generally means the phone is charging.
    const staleWatchdog = setInterval(() => {
      if (cancelled || !Location) return;
      if (Date.now() - lastFixAt < STALE_AFTER_MS) return;
      (async () => {
        try {
          const { status } = await Location.getForegroundPermissionsAsync();
          if (cancelled || status !== 'granted') return;
          const current = await Location.getCurrentPositionAsync({
            accuracy: Location.Accuracy.High,
          });
          if (cancelled || !current?.coords) return;
          const next = {
            latitude: current.coords.latitude,
            longitude: current.coords.longitude,
            heading: current.coords.heading ?? null,
          };
          lastFix = next;
          lastFixAt = Date.now();
          setLoc(next);
          persistFix(next);
        } catch {
          // Still unavailable — try again on the next tick.
        }
      })();
    }, WATCHDOG_INTERVAL_MS);

    return () => {
      cancelled = true;
      clearInterval(staleWatchdog);
      try {
        subRef.current?.remove();
      } catch {
        /* already torn down */
      }
      subRef.current = null;
    };
  }, []);

  return loc;
}
