/**
 * Live driver location for the Android Auto map surface.
 *
 * The phone's location pipeline lives in `useDriverDashboard` *component* state,
 * which is not mounted on a car-only cold launch (Android Auto can start the JS
 * context with no phone UI). So the car surface can't read it — it watches its
 * own fix here instead. This is a second, lightweight foreground watcher; the OS
 * coalesces GPS so it does not double the sensor cost.
 *
 * This hook is the FOREGROUND half. The continuous half — the one that keeps
 * working when the phone app is closed — is `carLocationTask.ts` / the online
 * driver's `spinr-background-location` service, both of which publish through
 * `carFixChannel.ts`. This hook subscribes to that channel, so whichever source
 * is live drives the same marker.
 *
 * - Seeds from the last-known fix in AsyncStorage (`spinr_driver_last_location`)
 *   so the map centers instantly, before the first live fix arrives.
 * - Only watches when foreground permission is ALREADY granted; it never prompts
 *   (a permission dialog triggered from the car surface would be a bad surprise).
 * - expo-location / AsyncStorage are lazy-required so this degrades to `null` in
 *   tests / web / Expo Go instead of crashing, matching carSurface.tsx.
 */
import { useEffect, useRef, useState } from 'react';
import {
  adoptCarFix,
  carFixAgeMs,
  getLastCarFix,
  LAST_LOCATION_KEY,
  MAX_SEED_AGE_MS,
  persistFix,
  seedCarFix,
  subscribeCarFix,
  type CarLatLng,
} from './carFixChannel';

export { getLastCarFix, getHeadingSource, type CarLatLng } from './carFixChannel';

/** A fix older than this is treated as stale and actively refreshed. */
const STALE_AFTER_MS = 5_000;
/** How often staleness is checked. Cheap — it usually decides to do nothing. */
const WATCHDOG_INTERVAL_MS = 3_000;

export function useCarLocation(): CarLatLng | null {
  const [loc, setLoc] = useState<CarLatLng | null>(getLastCarFix);
  const subRef = useRef<{ remove: () => void } | null>(null);

  // Fixes from the foreground service arrive outside React; this is how they
  // reach the rendered map. Subscribed first so a task that is already running
  // when the surface mounts starts driving it immediately.
  useEffect(() => subscribeCarFix(setLoc), []);

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
        setLoc(
          seedCarFix({
            latitude: last.coords.latitude,
            longitude: last.coords.longitude,
            heading: last.coords.heading ?? null,
          }),
        );
      } catch {
        // No cached fix — the AsyncStorage seed and live watcher still apply.
      }
    })();

    // SECOND: actively request a CURRENT fix, and let it overwrite the cache.
    //
    // This is the fix for "the map shows where I started". The other seeds and
    // the module-level last fix are all *stale by construction* — they exist so a
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
    // Unlike the seeds, this one DOES overwrite: a freshly-requested fix is by
    // definition better than anything cached.
    (async () => {
      if (!Location) return;
      try {
        const { status } = await Location.getForegroundPermissionsAsync();
        if (cancelled || status !== 'granted') return;
        const current = await Location.getCurrentPositionAsync({
          accuracy: Location.Accuracy.High,
        });
        if (cancelled || !current?.coords) return;
        // adoptCarFix returns the MERGED fix — a one-shot request carries no
        // course over ground, so this keeps the last real bearing instead of
        // snapping the marker to north. Same at both other call sites below.
        const next = adoptCarFix({
          latitude: current.coords.latitude,
          longitude: current.coords.longitude,
          heading: current.coords.heading ?? null,
        });
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
          // one carried across a surface remount.
          setLoc(
            seedCarFix({ latitude: lat as number, longitude: lng as number, heading: null }),
          );
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
            // The one path that genuinely produces a course over ground, so
            // this is normally where a fresh bearing enters the system.
            const next = adoptCarFix({
              latitude: p.coords.latitude,
              longitude: p.coords.longitude,
              heading: p.coords.heading ?? null,
            }); // survives the next surface teardown/rebuild
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
    // while the watcher (or a foreground-service task) is healthy — it only
    // spends battery on the exact failure it exists to cover, and Android Auto
    // generally means the phone is charging.
    const staleWatchdog = setInterval(() => {
      if (cancelled || !Location) return;
      if (carFixAgeMs() < STALE_AFTER_MS) return;
      (async () => {
        try {
          const { status } = await Location.getForegroundPermissionsAsync();
          if (cancelled || status !== 'granted') return;
          const current = await Location.getCurrentPositionAsync({
            accuracy: Location.Accuracy.High,
          });
          if (cancelled || !current?.coords) return;
          // The watchdog is the path that broke marker rotation on Android
          // Auto: with the phone backgrounded in a cradle the throttled watcher
          // goes quiet, so this fires every few seconds and its course-less fix
          // became the dominant one. Merging keeps the last real bearing.
          const next = adoptCarFix({
            latitude: current.coords.latitude,
            longitude: current.coords.longitude,
            heading: current.coords.heading ?? null,
          });
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
