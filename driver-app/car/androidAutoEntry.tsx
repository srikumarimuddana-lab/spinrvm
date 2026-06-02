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
import { initCarNav } from './carNav';

// Android Auto renders Google car *templates*, not RN views, so this root renders
// nothing itself — it drives the template layer via initCarNav(), which sets a
// MapTemplate (the in-dash route map drawn from the stored polyline) as the car
// root on an active ride and an idle ListTemplate otherwise. The map surface
// component is rendered separately by the fork onto the car surface.
function AndroidAutoRoot(): null {
  useEffect(() => {
    const cleanup = initCarNav();
    return cleanup;
  }, []);
  return null;
}

// Instrument-cluster surface: on cluster-equipped vehicles the fork runs a
// SEPARATE "AndroidAutoCluster" root alongside the main one (CarPlaySession.kt,
// by displayType). It must NOT call initCarNav — a second instance would
// double-register the global button-press listeners and fire duplicate nav
// hand-offs for a single tap. The cluster is guidance-only here, so it's a no-op.
function AndroidAutoClusterRoot(): null {
  return null;
}

export { AndroidAutoRoot, AndroidAutoClusterRoot };

export function registerAndroidAuto(): void {
  if (Platform.OS !== 'android') return;
  try {
    AppRegistry.registerComponent('AndroidAuto', () => AndroidAutoRoot);
    // The fork requests "AndroidAutoCluster" for the instrument-cluster surface;
    // register a SEPARATE no-op root (not AndroidAutoRoot) so a cluster launch
    // doesn't crash AND doesn't run a second initCarNav (duplicate-hand-off fix).
    AppRegistry.registerComponent('AndroidAutoCluster', () => AndroidAutoClusterRoot);
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
