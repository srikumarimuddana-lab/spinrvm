// Binds the driver ride-state to the Android Auto head unit using
// @iternio/react-native-auto-play (Nitro-based, New-Architecture native).
//
// Architecture notes:
// - Importing the package runs its headless-task registration as a side effect,
//   so we lazy-require it behind a Platform.OS === 'android' guard. iOS CarPlay
//   needs an Apple-granted entitlement plus scene-delegate / Info.plist wiring
//   that this build does not have, so the package is never loaded on iOS.
// - registerAutoPlay() must be called at JS bundle load (from index.js), because
//   Android Auto can launch the JS context car-only — app/_layout.tsx never
//   mounts in that case, so anything gated on the phone UI would be missed.
// - We render an InformationTemplate (a PaneTemplate on Android): a read-only,
//   ride-centric screen. is_online lives in component-local state, not the
//   global store, so the car screen reflects the current ride rather than
//   exposing controls a driver shouldn't operate while driving.
import { Platform } from 'react-native';
import { useDriverStore } from '../../store/driverStore';
import { buildCarScreen, type CarRow } from './carScreen';

const ROOT_TEMPLATE_ID = 'spinr-aa-root';

// Map our neutral car-screen model onto iternio's TextRow shape. The template
// is keyed by id, so re-creating it with the same id upserts (title + rows)
// rather than stacking native templates.
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
  const { HybridAutoPlay, InformationTemplate } = require('@iternio/react-native-auto-play');

  let unsubscribe: (() => void) | null = null;
  let lastModel = '';

  const render = () => {
    const { rideState, activeRide } = useDriverStore.getState();
    const model = buildCarScreen(rideState, activeRide);

    // The store fires on unrelated fields (earnings, chat); only push to the
    // head unit when the visible model actually changes.
    const serialised = JSON.stringify(model);
    if (serialised === lastModel) {
      return;
    }
    lastModel = serialised;

    const template = new InformationTemplate({
      id: ROOT_TEMPLATE_ID,
      title: { text: model.title },
      // 1-4 rows; buildCarScreen guarantees that range.
      items: toInformationItems(model.items) as never,
    });
    template.setRootTemplate();
  };

  HybridAutoPlay.addListener('didConnect', () => {
    lastModel = '';
    render();
    unsubscribe = useDriverStore.subscribe(render);
  });

  HybridAutoPlay.addListener('didDisconnect', () => {
    unsubscribe?.();
    unsubscribe = null;
    lastModel = '';
  });
}
