import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import { HeatmapGradientOverlay } from '../../components/dashboard/HeatmapGradientOverlay';

/**
 * Separate file, deliberately: the "Skia native module missing" scenario
 * needs `@shopify/react-native-skia`'s require() to throw for the WHOLE
 * file, which is incompatible with HeatmapGradientOverlay.test.tsx's own
 * file-level mock (a working, mockable module). Jest isolates module
 * registries per FILE, not per describe block — a nested
 * jest.isolateModules() inside that other file was tried and rejected: it
 * ended up loading a second copy of `react` for the isolated require(),
 * distinct from the one this file's own JSX/TestRenderer uses, which broke
 * hooks with "Cannot read properties of null (reading 'useMemo')" — a
 * test-infra artifact, not a real bug. A dedicated file sidesteps that
 * entirely by keeping exactly one `react` module instance for everything.
 */
jest.mock('@shopify/react-native-skia', () => {
  throw new Error('Native module RNSkiaModule tried to be registered twice');
});

jest.mock('@shared/theme/ThemeContext', () => ({
  useTheme: () => ({ colors: { heatmapRamp: ['#ffe3e0', '#ffb3ac', '#ff7a6e', '#ff3b30', '#b71c1c'] } }),
}));

describe('HeatmapGradientOverlay — degrades gracefully when the native module is unavailable', () => {
  // Simulates a JS-only OTA update reaching a native binary built before
  // this dependency existed (HM-32) — @shopify/react-native-skia's
  // require() throws (missing native module). The component must survive
  // by rendering nothing rather than crashing the driver dashboard screen.
  it('renders null instead of throwing when @shopify/react-native-skia fails to load', () => {
    let renderer!: TestRenderer.ReactTestRenderer;
    expect(() => {
      act(() => {
        renderer = TestRenderer.create(
          <HeatmapGradientOverlay
            cells={[{ lat: 52.1, lng: -106.6, weight: 5 }]}
            region={{ latitude: 52.1, longitude: -106.6, latitudeDelta: 0.02, longitudeDelta: 0.02 }}
            cellLatDeg={0.01}
            cellLngDeg={0.01}
            viewport={{ width: 400, height: 800 }}
          />,
        );
      });
    }).not.toThrow();
    expect(renderer.toJSON()).toBeNull();
  });
});
