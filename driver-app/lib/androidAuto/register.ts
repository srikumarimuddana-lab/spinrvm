// Drives the Android Auto head unit from the driver ride-state machine using
// @iternio/react-native-auto-play (Nitro-based, New-Architecture native).
//
// Architecture notes:
// - Importing the package runs its headless-task registration as a side effect,
//   so we lazy-require it behind a Platform.OS === 'android' guard. iOS CarPlay
//   needs an Apple-granted entitlement plus scene-delegate wiring this build
//   does not have, so the package is never loaded on iOS.
// - registerAutoPlay() must be called at JS bundle load (from index.js): Android
//   Auto can launch the JS context car-only, where the phone route layout never
//   mounts. We act only on HybridAutoPlay 'didConnect'.
// - ONE car surface: a live MapTemplate shown in every ride state. carSurface.tsx
//   always draws the driver's current location (a car marker) so the idle car
//   screen is a useful map, not a blank status pane, and overlays the stored
//   route + destination during a ride. The map shows the route we ALREADY stored
//   at ride creation, so there is no live Routes/Directions API spend.
// - Map buttons (the only interaction Android Auto allows on the surface): zoom
//   out / zoom in in every state, plus Google / Waze turn-by-turn hand-off
//   buttons while navigating.
import { Linking, Platform } from 'react-native';
import { useDriverStore } from '../../store/driverStore';
import {
  buildHandoffUrl,
  defaultNavButtons,
  selectCarRoute,
  type NavProvider,
} from './carRoute';
import { CarMapSurface } from './carSurface';
import { useCarMapCamera } from './carMapCamera';

const NAV_TEMPLATE_ID = 'spinr-aa-nav';

// Map-button icons. iternio's AutoImage accepts a bundled asset. The nav-handoff
// glyph still reuses the car marker (swap for proper nav glyphs before release);
// the zoom glyphs are purpose-built +/- template icons.
const NAV_ICON = require('../../assets/images/car_marker.png');
const ZOOM_IN_ICON = require('../../assets/images/zoom_in.png');
const ZOOM_OUT_ICON = require('../../assets/images/zoom_out.png');

const log = (...args: unknown[]) => {
  if (__DEV__) console.log('[android-auto]', ...args);
};
const noop = () => {};

export default function registerAutoPlay(): void {
  if (Platform.OS !== 'android') {
    return;
  }

  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const autoPlay = require('@iternio/react-native-auto-play');
  const { HybridAutoPlay, MapTemplate } = autoPlay;

  // Android Auto: Google + Waze hand-off buttons (both ~always present).
  const navButtons = defaultNavButtons(Platform.OS);

  // Hand live turn-by-turn to the driver's nav app for the current destination.
  const handoffToNav = (provider: NavProvider) => {
    const { rideState, activeRide } = useDriverStore.getState();
    const route = selectCarRoute(rideState, activeRide);
    if (!route) {
      log('hand-off skipped — no active route');
      return;
    }
    const { latitude, longitude } = route.destination;
    const url = buildHandoffUrl(provider, route.destination, Platform.OS);
    // Universal web Maps URL as a last resort so a missing/disabled nav app never
    // leaves the button dead (google.navigation: throws with no Maps app).
    const webFallback = `https://www.google.com/maps/dir/?api=1&destination=${latitude},${longitude}&travelmode=driving`;
    log('hand-off →', provider, url);
    Linking.openURL(url).catch(() =>
      Linking.openURL(webFallback).catch((e) => log('hand-off failed:', e)),
    );
  };

  // Zoom buttons drive the (non-interactive) surface through the shared camera
  // store; carSurface.tsx reads `delta` and reframes the map.
  const zoomButtons = [
    {
      type: 'custom' as const,
      image: { type: 'asset' as const, image: ZOOM_OUT_ICON },
      onPress: () => useCarMapCamera.getState().zoomOut(),
    },
    {
      type: 'custom' as const,
      image: { type: 'asset' as const, image: ZOOM_IN_ICON },
      onPress: () => useCarMapCamera.getState().zoomIn(),
    },
  ];

  // Rebuild the root only when the displayed surface actually changes, not on
  // every store tick (location + zoom never round-trip through here — the surface
  // reads them directly — so setRootTemplate fires only on a leg/ride change).
  // The key folds in ride identity so a same-leg ride swap still re-centers.
  let lastKey: string | null = null;

  const apply = () => {
    // Only push templates when a car is actually connected.
    if (!HybridAutoPlay.isConnected?.()) return;

    const { rideState, activeRide } = useDriverStore.getState();
    const route = selectCarRoute(rideState, activeRide);

    const rideId = (activeRide?.ride as { id?: string } | undefined)?.id ?? '';
    const leg = route ? route.leg : 'idle';
    const key = `map:${leg}:${rideId}`;
    if (key === lastKey) return;
    lastKey = key;

    // Nav hand-off buttons only while navigating; zoom buttons in every state.
    const navHandoffButtons = route
      ? navButtons.map((b) => ({
          type: 'custom' as const,
          image: { type: 'asset' as const, image: NAV_ICON },
          onPress: () => handoffToNav(b.provider),
        }))
      : [];

    try {
      const tpl = new MapTemplate({
        id: NAV_TEMPLATE_ID,
        component: CarMapSurface,
        // We never run our own turn-by-turn; the system stop is a no-op.
        onStopNavigation: noop,
        mapButtons: [...navHandoffButtons, ...zoomButtons],
      });
      tpl.setRootTemplate();
    } catch (e) {
      log('setRootTemplate (map) failed:', e);
    }
  };

  let unsubscribe: (() => void) | null = null;

  const onConnect = () => {
    lastKey = null; // force a fresh root on (re)connect
    useCarMapCamera.getState().reset(); // start each session framed at the default zoom
    apply();
    if (!unsubscribe) {
      unsubscribe = useDriverStore.subscribe(apply);
    }
  };

  try {
    HybridAutoPlay.addListener('didConnect', onConnect);
    HybridAutoPlay.addListener('didDisconnect', () => {
      unsubscribe?.();
      unsubscribe = null;
      lastKey = null;
    });
    // Cold-launch while a head unit is ALREADY connected: the connect event may
    // have fired during native init before this listener existed, so apply now.
    if (HybridAutoPlay.isConnected?.()) {
      onConnect();
    }
  } catch (e) {
    log('Android Auto registration failed:', e);
  }
}
