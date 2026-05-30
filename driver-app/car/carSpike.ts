/**
 * CarPlay / Android Auto smoke-test bootstrap (SPIKE).
 *
 * Renders ONE hardcoded list template on the head unit when a car connects, to
 * prove the @g4rb4g3/react-native-carplay native integration links and that the
 * connect/disconnect events + an item callback reach JS. This is the JS half of
 * the spike in docs/carplay-android-auto.md (checklist steps 5-6).
 *
 * Intentionally self-contained and easy to delete: the real car-UI layer
 * (car/useCarInterface.ts wired to useDriverStore) replaces it once the spike
 * passes. Toggle with CAR_SPIKE_ENABLED.
 *
 * Safe-by-default: the native module is lazy-required and every call is guarded,
 * so a missing/unlinked module (e.g. Expo Go) degrades to a no-op instead of
 * crashing app startup — same gating pattern the app uses for LogRocket/Firebase.
 */

// Type-only import — erased at compile time, so it does NOT pull the native
// module in at JS load. The runtime handle is obtained via require() below.
import type { ListTemplate } from '@g4rb4g3/react-native-carplay';

export const CAR_SPIKE_ENABLED = true;

const SPIKE_TEMPLATE_ID = 'spinr-car-spike';

const log = (...args: unknown[]) => {
  if (__DEV__) {
    console.log('[car-spike]', ...args);
  }
};

const noop = () => {};

/**
 * Builds the single smoke-test template. Pure factory (constructor injected) so
 * it is unit-testable without the native module.
 */
export function buildSpikeTemplate(ListTemplateCtor: typeof ListTemplate): ListTemplate {
  const item = {
    id: 'spike-ok',
    text: 'Spinr CarPlay / Android Auto',
    detailText: 'Native bridge OK — tap to confirm callback',
  };
  return new ListTemplateCtor({
    id: SPIKE_TEMPLATE_ID,
    title: 'Spinr (spike)',
    // iOS CarPlay reads `sections`; Android Auto reads `items`. Provide both so
    // the one template renders on either head unit.
    sections: [{ header: 'Spike', items: [item] }],
    items: [item],
    onItemSelect: async ({ index }: { index: number }) => {
      log('item selected — JS callback reached, index =', index);
    },
  });
}

/**
 * Registers connect/disconnect handlers; on connect, sets the spike template as
 * root. Returns a cleanup that unregisters the handlers. No-op if the spike is
 * disabled or the native module is unavailable.
 */
export function initCarSpike(): () => void {
  if (!CAR_SPIKE_ENABLED) return noop;

  let carplay: typeof import('@g4rb4g3/react-native-carplay');
  try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    carplay = require('@g4rb4g3/react-native-carplay');
  } catch (e) {
    log('native module not available — skipping (expected in Expo Go):', e);
    return noop;
  }

  const { CarPlay, ListTemplate: ListTemplateCtor } = carplay;

  const onConnect = () => {
    log('car connected — setting spike root template');
    try {
      CarPlay.setRootTemplate(buildSpikeTemplate(ListTemplateCtor));
    } catch (e) {
      log('setRootTemplate failed:', e);
    }
  };
  const onDisconnect = () => log('car disconnected');

  try {
    CarPlay.registerOnConnect(onConnect);
    CarPlay.registerOnDisconnect(onDisconnect);
  } catch (e) {
    log('handler registration failed:', e);
    return noop;
  }

  return () => {
    try {
      CarPlay.unregisterOnConnect(onConnect);
      CarPlay.unregisterOnDisconnect(onDisconnect);
    } catch {
      /* no-op */
    }
  };
}
