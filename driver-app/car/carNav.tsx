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
 * truth). Android-only and fully guarded — a no-op on iOS / Expo Go / missing
 * native module.
 *
 * UNPROVEN ON HARDWARE: like the rest of car/, this is validated at the JS level
 * only. The map button icons and on-surface render must still be confirmed on an
 * EAS dev build + Android Auto DHU (see docs/carplay-android-auto.md).
 */
import React from 'react';
import { Linking, Platform, StyleSheet, View } from 'react-native';
import { useDriverStore } from '../store/driverStore';
import { buildHandoffUrl, selectCarRoute, type NavProvider } from './carRoute';

const NAV_TEMPLATE_ID = 'spinr-car-nav';
const IDLE_TEMPLATE_ID = 'spinr-car-idle';
const SPINR_RED = '#ee2b2b';

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
  const url = buildHandoffUrl(provider, route.destination);
  Linking.openURL(url).catch((e) => log('hand-off openURL failed:', e));
}

// ── Template builders (constructors injected → unit-testable w/o native) ──
type MapCtor = new (cfg: Record<string, unknown>) => object;
type ListCtor = new (cfg: Record<string, unknown>) => object;

export function buildNavMapTemplate(MapTemplateCtor: MapCtor): object {
  return new MapTemplateCtor({
    id: NAV_TEMPLATE_ID,
    component: CarMapSurface,
    // Android Auto interaction is via template buttons (driver-distraction
    // model), not in-surface touchables. Each hands off to a nav app.
    mapButtons: [{ id: 'nav-google' }, { id: 'nav-waze' }],
    onMapButtonPressed: (e: { id: string }) => {
      handoffToNav(e.id === 'nav-waze' ? 'waze' : 'google');
    },
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
export function initCarNav(): () => void {
  if (Platform.OS !== 'android') return noop;

  let carplay: typeof import('@g4rb4g3/react-native-carplay');
  try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    carplay = require('@g4rb4g3/react-native-carplay');
  } catch (e) {
    log('native module unavailable — skipping (expected in Expo Go):', e);
    return noop;
  }
  const { CarPlay, MapTemplate, ListTemplate } = carplay;

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
        ? buildNavMapTemplate(MapTemplate as unknown as MapCtor)
        : buildIdleTemplate(ListTemplate as unknown as ListCtor);
      // The fork's setRootTemplate type union omits MapTemplate (its TS types lag
      // the runtime, which accepts it as a root) — cast to the expected param.
      CarPlay.setRootTemplate(tpl as Parameters<typeof CarPlay.setRootTemplate>[0]);
    } catch (e) {
      log('setRootTemplate failed:', e);
    }
  };

  const onConnect = () => {
    lastKey = null; // force a fresh root on (re)connect
    apply();
  };

  let unsub = noop;
  try {
    CarPlay.registerOnConnect(onConnect);
    unsub = useDriverStore.subscribe(apply);
    if (CarPlay.connected) onConnect();
  } catch (e) {
    log('init failed:', e);
    return noop;
  }

  return () => {
    try {
      CarPlay.unregisterOnConnect(onConnect);
      unsub();
    } catch {
      /* no-op */
    }
  };
}

const styles = StyleSheet.create({ fill: { flex: 1 } });
