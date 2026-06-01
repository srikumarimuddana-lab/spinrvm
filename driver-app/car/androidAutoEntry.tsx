/**
 * Android Auto JS entry point (SPIKE).
 *
 * The fork starts a SEPARATE React root for Android Auto: its native `CarPlaySession`
 * calls `AppRegistry.runApplication("AndroidAuto", ...)` (and `"AndroidAutoCluster"`
 * for the instrument cluster). That root is NOT the phone's expo-router tree, so the
 * car UI must be registered here as its own AppRegistry component — otherwise a
 * head-unit / DHU launch has no JS to run and never sets a template. (Codex P1)
 *
 * Registered at module load from the real bundle entry (driver-app/index.js) before
 * expo-router/entry registers the phone root, so the component exists when native
 * cold-launches the car surface via runApplication. Android-only and fully guarded
 * — a no-op on iOS / Expo Go / missing native module.
 */
import { useEffect } from 'react';
import { AppRegistry, Platform } from 'react-native';
import { initCarSpike } from './carSpike';

// Android Auto renders Google car *templates*, not RN views, so this root renders
// nothing — it just drives the template layer via initCarSpike() (which sets the
// root template on connect; the car is already connected when this root mounts).
function AndroidAutoRoot(): null {
  useEffect(() => {
    const cleanup = initCarSpike();
    return cleanup;
  }, []);
  return null;
}

export { AndroidAutoRoot };

export function registerAndroidAuto(): void {
  if (Platform.OS !== 'android') return;
  try {
    AppRegistry.registerComponent('AndroidAuto', () => AndroidAutoRoot);
    // The fork requests "AndroidAutoCluster" for the instrument-cluster surface;
    // register the same root so a cluster launch doesn't crash on a missing component.
    AppRegistry.registerComponent('AndroidAutoCluster', () => AndroidAutoRoot);
    // The fork needs its headless task registered so timers keep firing while the
    // screen is off. It is only the default export of a subpath (not the index), so
    // require it defensively — a render still works without it.
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const registerHeadlessTask = require('@g4rb4g3/react-native-carplay/lib/CarPlayHeadlessJsTask').default;
    if (typeof registerHeadlessTask === 'function') registerHeadlessTask();
  } catch {
    /* native module unavailable (e.g. Expo Go) — Android Auto simply won't run */
  }
}

registerAndroidAuto();
