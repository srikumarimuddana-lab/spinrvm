import { useCallback, useEffect } from 'react';
import * as ExpoRouter from 'expo-router';
import { getLogRocketInstance } from '../services/logRocketInstance';

// Screen-level jest suites across both apps widely `jest.mock('expo-router', () =>
// ({ useRouter: ..., useLocalSearchParams: ... }))` with only the exports that
// screen already used — a real `useFocusEffect` import would throw
// "is not a function" in every one of those suites. Resolved once, at module
// load, from whatever the (possibly partially mocked) 'expo-router' module
// actually exports: the real hook in the app, a plain mount/unmount effect
// under a partial test mock. Not derived from props/render state, so picking
// a fixed hook this way doesn't violate rules-of-hooks.
type FocusEffectHook = (effect: () => void | (() => void)) => void;
const useFocusEffectOrMount: FocusEffectHook =
  typeof ExpoRouter.useFocusEffect === 'function'
    ? (ExpoRouter.useFocusEffect as FocusEffectHook)
    : (cb) => useEffect(cb, []);

// #1231 Finding 17: LogRocket session replay can capture card numbers,
// government-ID photos, and live safety/SOS state unless those screens are
// explicitly excluded. Call this in any screen that shows payment entry,
// an identity/vehicle document upload, or the SOS/emergency flow — it
// pauses view capture while the screen is focused and resumes it on
// blur/unmount, so the recording gap is scoped to exactly that screen
// rather than disabling replay app-wide.
//
// No-op when LogRocket isn't initialized (Expo Go, web, Android default-off,
// or the kill flag) — pauseViewCapture/unpauseViewCapture are only ever
// called on a real, running SDK instance.
export function useLogRocketPrivacyScreen(): void {
  useFocusEffectOrMount(
    useCallback(() => {
      const LogRocket = getLogRocketInstance();
      if (!LogRocket) return undefined;
      try {
        LogRocket.pauseViewCapture();
      } catch (e) {
        console.log('[LogRocket] pauseViewCapture failed:', e);
      }
      return () => {
        try {
          LogRocket.unpauseViewCapture();
        } catch (e) {
          console.log('[LogRocket] unpauseViewCapture failed:', e);
        }
      };
    }, [])
  );
}
