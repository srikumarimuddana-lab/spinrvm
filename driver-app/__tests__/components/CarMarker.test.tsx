import React from 'react';
import { render, act } from '@testing-library/react-native';
import { CarMarker } from '../../components/CarMarker';

// react-native-maps requires native modules Jest can't load — stub with
// components that support everything CarMarker actually uses: a ref with
// animateMarkerToCoordinate (Android path), Marker.Animated (iOS path), and
// a constructible AnimatedRegion with .timing().start().
jest.mock('react-native-maps', () => {
  const ReactActual = require('react');
  const Marker = ReactActual.forwardRef((props: any, ref: any) => {
    ReactActual.useImperativeHandle(ref, () => ({ animateMarkerToCoordinate: jest.fn() }), []);
    return ReactActual.createElement('Marker', props, props.children);
  });
  (Marker as any).Animated = ReactActual.forwardRef((props: any, ref: any) => {
    ReactActual.useImperativeHandle(ref, () => ({}), []);
    return ReactActual.createElement('MarkerAnimated', props, props.children);
  });
  class AnimatedRegion {
    timing(_config: any) {
      return { start: (cb?: () => void) => cb?.() };
    }
  }
  return { __esModule: true, Marker, AnimatedRegion };
});

// markerPlayback is a value import CarMarker needs at runtime; no driver-app
// mock exists for it (unlike vehicleTracking, which jest.config.js maps to
// the real module), so stub the minimal surface CarMarker calls.
jest.mock('@shared/utils/markerPlayback', () => ({
  PLAYBACK_DELAY_MS: 300,
  playbackPosition: () => null, // no buffered fix yet — ticker becomes a no-op
  pushFix: jest.fn(),
  shouldResetBuffer: () => false,
}));

jest.mock('expo-image', () => {
  const ReactActual = require('react');
  return { Image: (props: any) => ReactActual.createElement('ExpoImage', props) };
});

describe('CarMarker — mount bounce-in (round 8)', () => {
  beforeEach(() => {
    jest.useFakeTimers();
  });
  afterEach(() => {
    jest.useRealTimers();
  });

  it('mounts, runs its playback ticker and settle timers, and unmounts cleanly', () => {
    const { unmount } = render(
      <CarMarker coordinate={{ latitude: 50.4452, longitude: -104.6189 }} heading={90} />,
    );

    // Advance past the playback tick (500ms) and the settle/hard-cap window
    // (5000ms) so every internal timer this component owns has fired at
    // least once — this is what would throw/warn on a leaked interval or a
    // ref access during a delayed effect.
    act(() => {
      jest.advanceTimersByTime(6000);
    });

    expect(() => unmount()).not.toThrow();
  });

  it('remounting (e.g. after the mapKey remount that recovers a stale marker) does not throw', () => {
    const coord = { latitude: 50.4452, longitude: -104.6189 };
    const { unmount } = render(<CarMarker coordinate={coord} identifier="a" />);
    act(() => {
      jest.advanceTimersByTime(1000);
    });
    unmount();

    // A fresh mount with a new identifier simulates the MapView key remount:
    // the whole subtree unmounts and a new CarMarker instance mounts, which
    // re-triggers the one-shot bounce-in spring.
    expect(() =>
      render(<CarMarker coordinate={coord} identifier="a-remounted" />),
    ).not.toThrow();
    act(() => {
      jest.advanceTimersByTime(6000);
    });
  });
});
