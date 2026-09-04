/**
 * Tests for shared/components/CarMarker's onPositionChange callback — the
 * fix that lets a follow camera anchor on the marker's own (delayed,
 * route-snapped) rendered position instead of a separately-held raw GPS
 * fix. Ported from driver-app's own copy of this component (see
 * driver-app/__tests__/components/CarMarker.test.tsx, "onBearingChange"
 * describe block, 2026-08-31) after the same root cause was found to still
 * be present in rider-app's two follow-camera screens (ride-in-progress.tsx,
 * driver-arriving.tsx).
 *
 * Lives in rider-app/__tests__ (not shared/__tests__), matching this repo's
 * existing convention for shared/ component and util tests (e.g.
 * vehicleTracking.test.ts) — rider-app's jest config maps `@shared/*` to the
 * real module and includes `../shared` in `roots`.
 */
import React from 'react';
import { render, act } from '@testing-library/react-native';
import { CarMarker } from '@shared/components/CarMarker';
import { playbackPosition } from '@shared/utils/markerPlayback';

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

// markerPlayback is a value import CarMarker needs at runtime — stub the
// minimal surface it calls, same as driver-app's CarMarker.test.tsx.
jest.mock('@shared/utils/markerPlayback', () => ({
  PLAYBACK_DELAY_MS: 300,
  playbackPosition: jest.fn(() => null), // default: no buffered fix, ticker is a no-op
  pushFix: jest.fn(),
  shouldResetBuffer: () => false,
}));

jest.mock('expo-image', () => {
  const ReactActual = require('react');
  return { Image: (props: any) => ReactActual.createElement('ExpoImage', props) };
});

describe('CarMarker — onPositionChange (shared position source for rider follow cameras)', () => {
  const coord = { latitude: 50.4452, longitude: -104.6189 };
  const mockPlaybackPosition = playbackPosition as jest.Mock;

  beforeEach(() => {
    jest.useFakeTimers();
    // ~89m north of `coord` — clears the ticker's 0.5m churn guard so it
    // actually applies a position this tick instead of no-op'ing (parked).
    mockPlaybackPosition.mockReturnValue({
      coordinate: { latitude: 50.446, longitude: -104.6189 },
      bearing: 42,
      mode: 'interpolating',
    });
  });
  afterEach(() => {
    jest.useRealTimers();
    mockPlaybackPosition.mockReturnValue(null);
  });

  it('fires onPositionChange with the exact position the ticker renders that tick', () => {
    const onPositionChange = jest.fn();
    const { unmount } = render(
      <CarMarker coordinate={coord} onPositionChange={onPositionChange} />,
    );
    act(() => {
      jest.advanceTimersByTime(500); // one TICK_MS
    });
    // A follow camera anchoring on this position sees exactly where the icon
    // renders — the fix this test guards: before it, ride-in-progress.tsx
    // and driver-arriving.tsx anchored their camera on the raw (undelayed)
    // driver GPS fix instead, which can drift from the icon's actual
    // (PLAYBACK_DELAY_MS-delayed, route-snapped) position at driving speed.
    expect(onPositionChange).toHaveBeenCalledWith({
      latitude: 50.446,
      longitude: -104.6189,
    });
    unmount();
  });

  it('is optional — omitting it does not throw even though the ticker still selects a position', () => {
    const { unmount } = render(<CarMarker coordinate={coord} />);
    expect(() => act(() => jest.advanceTimersByTime(500))).not.toThrow();
    unmount();
  });
});
