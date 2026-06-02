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
// - Two car surfaces, chosen by ride state:
//     * navigating_to_pickup / arrived_at_pickup / trip_in_progress → MapTemplate
//       with the stored route drawn on the surface (carSurface.tsx) and nav
//       hand-off buttons (Google / Waze) that launch live turn-by-turn.
//     * everything else (idle / ride_offered / trip_completed) → InformationTemplate
//       status screen (a Pane on Android) from the buildCarScreen model.
//   The map shows the route we ALREADY stored at ride creation, so there is no
//   live Routes/Directions API spend.
import { Linking, Platform } from 'react-native';
import { useDriverStore } from '../../store/driverStore';
import { buildCarScreen, type CarRow } from './carScreen';
import {
  buildHandoffUrl,
  defaultNavButtons,
  selectCarRoute,
  type NavProvider,
} from './carRoute';
import { CarMapSurface } from './carSurface';

const NAV_TEMPLATE_ID = 'spinr-aa-nav';
const INFO_TEMPLATE_ID = 'spinr-aa-info';

// Map-button icon. iternio's AutoImage accepts a bundled asset; reuse the car
// marker so the buttons render + are tappable. Swap for proper nav glyphs before
// release (would need a registered icon font via setIconFont).
const NAV_ICON = require('../../assets/images/car_marker.png');

const log = (...args: unknown[]) => {
  if (__DEV__) console.log('[android-auto]', ...args);
};
const noop = () => {};

// Map our neutral car-screen model onto iternio's TextRow shape.
function toInformationItems(rows: CarRow[]) {
  return rows.map((row) => ({
    type: 'text' as const,
    title: { text: row.text },
    ...(row.detailText ? { detailedText: { text: row.detailText } } : {}),
  }));
}

export default function registerAutoPlay(): void {
  if (Platform.OS !== 'android') {
    return;
  }

  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const autoPlay = require('@iternio/react-native-auto-play');
  const { HybridAutoPlay, MapTemplate, InformationTemplate } = autoPlay;

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

  // Rebuild the root only when the displayed surface actually changes, not on
  // every store tick (location updates fire constantly; setRootTemplate is
  // comparatively expensive). The key folds in ride identity so a same-leg ride
  // swap still re-centers the map.
  let lastKey: string | null = null;

  const apply = () => {
    // Only push templates when a car is actually connected.
    if (!HybridAutoPlay.isConnected?.()) return;

    const { rideState, activeRide } = useDriverStore.getState();
    const route = selectCarRoute(rideState, activeRide);

    if (route) {
      const rideId = (activeRide?.ride as { id?: string } | undefined)?.id ?? '';
      const key = `map:${route.leg}:${rideId}`;
      if (key === lastKey) return;
      lastKey = key;
      try {
        const tpl = new MapTemplate({
          id: NAV_TEMPLATE_ID,
          component: CarMapSurface,
          // We never run our own turn-by-turn; the system stop is a no-op.
          onStopNavigation: noop,
          mapButtons: navButtons.map((b) => ({
            type: 'custom' as const,
            image: { type: 'asset' as const, image: NAV_ICON },
            onPress: () => handoffToNav(b.provider),
          })),
        });
        tpl.setRootTemplate();
      } catch (e) {
        log('setRootTemplate (map) failed:', e);
      }
      return;
    }

    // Non-navigation states → status screen.
    const model = buildCarScreen(rideState, activeRide);
    const key = `info:${JSON.stringify(model)}`;
    if (key === lastKey) return;
    lastKey = key;
    try {
      const tpl = new InformationTemplate({
        id: INFO_TEMPLATE_ID,
        title: { text: model.title },
        items: toInformationItems(model.items) as never,
      });
      tpl.setRootTemplate();
    } catch (e) {
      log('setRootTemplate (info) failed:', e);
    }
  };

  let unsubscribe: (() => void) | null = null;

  const onConnect = () => {
    lastKey = null; // force a fresh root on (re)connect
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
