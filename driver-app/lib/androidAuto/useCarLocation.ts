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

    // Seed the camera from the last-known fix the phone pipeline persisted, so an
    // idle car screen opens centered on the driver instead of waiting for GPS.
    (async () => {
      try {
        // eslint-disable-next-line @typescript-eslint/no-require-imports
        const AsyncStorage = require('@react-native-async-storage/async-storage').default;
        const raw = await AsyncStorage.getItem(LAST_LOCATION_KEY);
        if (cancelled || !raw) return;
        const { lat, lng } = JSON.parse(raw) as { lat?: number; lng?: number };
        if (Number.isFinite(lat) && Number.isFinite(lng)) {
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
            accuracy: Location.Accuracy.Balanced,
            timeInterval: 4000,
            distanceInterval: 10,
          },
          (p) => {
            const next = {
              latitude: p.coords.latitude,
              longitude: p.coords.longitude,
              heading: p.coords.heading ?? null,
            };
            lastFix = next; // survives the next surface teardown/rebuild
            setLoc(next);
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

    return () => {
      cancelled = true;
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
