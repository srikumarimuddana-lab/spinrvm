// Registers the Android Auto root component with React Native's AppRegistry.
//
// IMPORTANT: this must run at JS bundle load, NOT inside app/_layout.tsx.
// Android Auto can launch the app's JS context "car-only" (the phone Activity
// never mounts), in which case the "main" component — and therefore _layout —
// never renders. The native CarPlaySession runs `runApplication("AndroidAuto")`,
// so the component under that exact name must already be registered. This
// module is imported from the custom entry (index.js) for that reason.
//
// iOS CarPlay is intentionally NOT wired here: it uses a scene-based flow and
// requires an Apple-granted CarPlay entitlement (com.apple.developer.carplay-*).
// Adding that entitlement without Apple's approval breaks iOS signing, so the
// iOS side stays dormant until the entitlement is granted.
import { useEffect, useRef } from 'react';
import { AppRegistry, Platform } from 'react-native';
import { useDriverStore } from '../../store/driverStore';
import { buildCarScreen } from './carScreen';

const ANDROID_AUTO_COMPONENT = 'AndroidAuto';
const ROOT_TEMPLATE_ID = 'spinr-aa-root';

if (Platform.OS === 'android') {
  // Lazy-require so the native CarPlay module is only touched on Android.
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const { CarPlay, PaneTemplate } = require('@g4rb4g3/react-native-carplay');

  const CarRoot = (): null => {
    // One PaneTemplate instance is created on first render and then updated in
    // place — recreating templates on every state change leaks native listeners.
    const templateRef = useRef<InstanceType<typeof PaneTemplate> | null>(null);
    const lastModelRef = useRef<string>('');

    useEffect(() => {
      const render = () => {
        const { rideState, activeRide } = useDriverStore.getState();
        const model = buildCarScreen(rideState, activeRide);

        // Skip redundant native round-trips: the store fires on unrelated
        // fields (earnings fetches, chat) that don't change the car screen.
        const serialised = JSON.stringify(model);
        if (serialised === lastModelRef.current) {
          return;
        }
        lastModelRef.current = serialised;

        const pane = { items: model.items };
        if (templateRef.current) {
          templateRef.current.updateTemplate({ title: model.title, pane });
        } else {
          templateRef.current = new PaneTemplate({
            id: ROOT_TEMPLATE_ID,
            title: model.title,
            pane,
          });
          CarPlay.setRootTemplate(templateRef.current);
        }
      };

      render();
      const unsubscribe = useDriverStore.subscribe(render);

      return () => {
        unsubscribe();
        templateRef.current?.destroy?.();
        templateRef.current = null;
        lastModelRef.current = '';
      };
    }, []);

    // The head unit renders native templates, not a React view tree.
    return null;
  };

  AppRegistry.registerComponent(ANDROID_AUTO_COMPONENT, () => CarRoot);
}
