/**
 * Android Auto in-dash route map (Path B *look*, Path A *cost*).
 *
 * On an active ride this sets a Google `MapTemplate` as the car root and draws
 * the route we already stored (`planned_route_polyline`) onto the head-unit map
 * surface, with the destination pinned. Tapping a map button hands live
 * turn-by-turn to Google Maps / Waze — so the driver gets the branded in-dash
 * look with ZERO live Routes/Navigation-SDK spend.
 *
 * The surface `component` is registered by the fork's MapTemplate
 * (`AppRegistry.registerComponent(this.id, …)`) and rendered onto the car
 * surface; it reads the SAME `useDriverStore` the phone uses (single source of
 * truth). Cross-platform — Android Auto (separate AppRegistry root) and CarPlay
 * (phone JS context, driven from app/_layout.tsx). Fully guarded: a no-op on
 * Expo Go / web / missing native module.
 *
 * UNPROVEN ON HARDWARE: like the rest of car/, this is validated at the JS level
 * only. The map button icons and on-surface render must still be confirmed on an
 * EAS dev build + Android Auto DHU (see docs/carplay-android-auto.md).
 */
import React from 'react';
import { Linking, Platform, StyleSheet, View } from 'react-native';
import { useDriverStore } from '../store/driverStore';
import {
  buildHandoffUrl,
  defaultNavButtons,
  providerForButton,
  resolveNavButtons,
  selectCarRoute,
  type NavButton,
  type NavProvider,
} from './carRoute';

const NAV_TEMPLATE_ID = 'spinr-car-nav';
const IDLE_TEMPLATE_ID = 'spinr-car-idle';
const SPINR_RED = '#ee2b2b';

// Placeholder glyph for the map-button icons (CarPlay/Android Auto map buttons
// are always icons). Reuses a bundled asset so the buttons render + are tappable
// in the emulator; swap for proper nav/Waze glyphs before release.
// eslint-disable-next-line @typescript-eslint/no-require-imports
const NAV_ICON = require('../assets/images/car_marker.png');

const log = (...args: unknown[]) => {
  if (__DEV__) console.log('[car-nav]', ...args);
};
const noop = () => {};

// ── Surface component: the branded route map drawn from the stored polyline ──
// react-native-maps' native view is absent in Expo Go / web / tests, so it is
// lazy-required and the component degrades to null instead of crashing.
export function CarMapSurface(): React.ReactElement | null {
  // Hooks first — unconditional, before any early return (rules-of-hooks).
  const rideState = useDriverStore((s) => s.rideState);
  const activeRide = useDriverStore((s) => s.activeRide);
  const route = selectCarRoute(rideState, activeRide);

  let Maps: typeof import('react-native-maps') | null = null;
  try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    Maps = require('react-native-maps');
  } catch {
    Maps = null;
  }
  if (!Maps || !route) return null;

  const MapView = Maps.default;
  const { Marker, Polyline } = Maps;

  return (
    <View style={styles.fill}>
      <MapView
        style={styles.fill}
        // The projected car surface is non-interactive (Android Auto drives
        // interaction through template buttons, not in-surface touches).
        pointerEvents="none"
        initialRegion={{
          latitude: route.destination.latitude,
          longitude: route.destination.longitude,
          latitudeDelta: 0.05,
          longitudeDelta: 0.05,
        }}
      >
        {route.polyline.length >= 2 && (
          <Polyline coordinates={route.polyline} strokeColor={SPINR_RED} strokeWidth={6} />
        )}
        <Marker coordinate={route.destination} title={route.destinationLabel} />
      </MapView>
    </View>
  );
}

// ── Hand off live turn-by-turn to the driver's nav app ──
export function handoffToNav(provider: NavProvider): void {
  const { rideState, activeRide } = useDriverStore.getState();
  const route = selectCarRoute(rideState, activeRide);
  if (!route) {
    log('hand-off skipped — no active route');
    return;
  }
  const url = buildHandoffUrl(provider, route.destination, Platform.OS);
  log('hand-off →', provider, url); // visible in Metro when testing on a head unit
  Linking.openURL(url).catch((e) => log('hand-off openURL failed:', e));
}

// ── Template builders (constructors injected → unit-testable w/o native) ──
type MapCtor = new (cfg: Record<string, unknown>) => object;
type ListCtor = new (cfg: Record<string, unknown>) => object;

export function buildNavMapTemplate(MapTemplateCtor: MapCtor, buttons: NavButton[]): object {
  // `buttons` is resolved per platform + installed apps (see resolveNavButtons).
  // PRESSES are handled by a CarPlay.emitter listener in initCarNav, NOT a config
  // callback: on Android the fork parses `mapButtons` as an ActionStrip and emits
  // `buttonPressed` (not the iOS `mapButtonPressed`), so a config `onMapButtonPressed`
  // would be dead on Android. One emitter listener covers both platforms and avoids
  // stale per-template callbacks firing after rebuilds.
  return new MapTemplateCtor({
    id: NAV_TEMPLATE_ID,
    component: CarMapSurface,
    mapButtons: buttons.map((b) => ({ id: b.id, image: NAV_ICON })),
  });
}

export function buildIdleTemplate(ListTemplateCtor: ListCtor): object {
  const item = { id: 'idle', text: 'Spinr', detailText: "You're online — waiting for trips" };
  return new ListTemplateCtor({
    id: IDLE_TEMPLATE_ID,
    title: 'Spinr',
    sections: [{ items: [item] }],
    items: [item],
  });
}

// ── Wire it up: drive the car root from the ride state machine ──
// Runs on both car platforms: Android Auto calls this from its AppRegistry root
// (car/androidAutoEntry.tsx); CarPlay calls it from app/_layout.tsx (shared JS
// context). The fork's CarPlay singleton abstracts the platform difference.
export function initCarNav(): () => void {
  if (Platform.OS !== 'android' && Platform.OS !== 'ios') return noop;

  let carplay: typeof import('@g4rb4g3/react-native-carplay');
  try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    carplay = require('@g4rb4g3/react-native-carplay');
  } catch (e) {
    log('native module unavailable — skipping (expected in Expo Go):', e);
    return noop;
  }
  const { CarPlay, MapTemplate, ListTemplate } = carplay;

  // Cold Android Auto launch runs this separate JS root with the phone dashboard
  // (and its hydrate + fetchActiveRide) never mounted, so the store would sit at
  // `idle` and show the idle list instead of the active route. Restore the
  // persisted ride here. Safe + idempotent: hydrateDriverRideState only fills an
  // empty store, never clobbers a live ride/offer, and is AsyncStorage-only (no
  // auth). The store subscription below rebuilds the root once state lands.
  // (Network reconciliation via fetchActiveRide needs auth bootstrap — deferred.)
  useDriverStore.getState().hydrateDriverRideState?.().catch(() => {});

  // Seed with the guaranteed app; the real set (incl. Google Maps / Waze if the
  // driver has them) is resolved asynchronously below and triggers a rebuild.
  let navButtons = defaultNavButtons(Platform.OS);
  let currentTemplate: { destroy?: () => void } | null = null;

  // Rebuild the root only when the displayed leg changes (idle → pickup →
  // dropoff), not on every store tick — location updates fire constantly and
  // setRootTemplate is comparatively expensive.
  let lastKey: string | null = null;
  const apply = () => {
    const { rideState, activeRide } = useDriverStore.getState();
    const route = selectCarRoute(rideState, activeRide);
    const key = route ? route.leg : 'idle';
    if (key === lastKey) return;
    lastKey = key;
    try {
      const tpl = route
        ? buildNavMapTemplate(MapTemplate as unknown as MapCtor, navButtons)
        : buildIdleTemplate(ListTemplate as unknown as ListCtor);
      // The fork's setRootTemplate type union omits MapTemplate (its TS types lag
      // the runtime, which accepts it as a root) — cast to the expected param.
      CarPlay.setRootTemplate(tpl as Parameters<typeof CarPlay.setRootTemplate>[0]);
      // Tear down the previous template: the fork registers per-instance emitter
      // listeners keyed by the (shared) template id, so dropping the old instance
      // without destroy() leaks listeners that keep firing. (Codex P2)
      const prev = currentTemplate;
      currentTemplate = tpl as { destroy?: () => void };
      prev?.destroy?.();
    } catch (e) {
      log('setRootTemplate failed:', e);
    }
  };

  // Route head-unit button presses to the nav hand-off via ONE emitter listener
  // per event: iOS emits `mapButtonPressed` ({id}); Android parses mapButtons as
  // an ActionStrip and emits `buttonPressed` ({buttonId}). (Codex P1)
  const onButton = (e: { id?: string; buttonId?: string }) => {
    const id = e.buttonId ?? e.id;
    if (id) handoffToNav(providerForButton(navButtons, id, Platform.OS));
  };

  const onConnect = () => {
    lastKey = null; // force a fresh root on (re)connect
    apply();
  };

  const buttonSubs: Array<{ remove: () => void }> = [];
  let unsub = noop;
  try {
    CarPlay.registerOnConnect(onConnect);
    unsub = useDriverStore.subscribe(apply);
    buttonSubs.push(CarPlay.emitter.addListener('mapButtonPressed', onButton));
    buttonSubs.push(CarPlay.emitter.addListener('buttonPressed', onButton));
    if (CarPlay.connected) onConnect();
  } catch (e) {
    log('init failed:', e);
    return noop;
  }

  // Detect which nav apps the driver actually has (CarPlay varies — some have
  // Google Maps, some don't). If the resolved set differs from the seed, rebuild
  // the current root so the extra buttons appear.
  resolveNavButtons(Platform.OS, (url) => Linking.canOpenURL(url))
    .then((resolved) => {
      const changed =
        resolved.length !== navButtons.length ||
        resolved.some((b, i) => b.id !== navButtons[i].id);
      navButtons = resolved;
      if (changed && CarPlay.connected) {
        lastKey = null;
        apply();
      }
    })
    .catch((e) => log('nav-app detection failed:', e));

  return () => {
    try {
      CarPlay.unregisterOnConnect(onConnect);
      unsub();
      buttonSubs.forEach((s) => s.remove());
      currentTemplate?.destroy?.();
    } catch {
      /* no-op */
    }
  };
}

const styles = StyleSheet.create({ fill: { flex: 1 } });
