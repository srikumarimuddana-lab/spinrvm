/**
 * shared/components/CarMarker.tsx (the rider-app copy) — the pulsing
 * presence ring must not loop when the OS Reduce Motion setting is on.
 * Its driver-app fork is covered by driver-app/__tests__/components/
 * CarMarker.test.tsx; keep the two in step (docs/known-forks.md).
 *
 * Mock header copied from carMarkerPositionChange.test.tsx.
 */
import React from 'react';
import { render, act } from '@testing-library/react-native';
import { Animated } from 'react-native';
import { CarMarker } from '@shared/components/CarMarker';

// Controls the OS Reduce Motion setting as seen through the shared hook.
let mockReduceMotion = false;
// Stateful like the real hook: flipping the setting re-renders the
// component through its own state, which React.memo(CarMarker) cannot skip.
const mockReduceMotionSubscribers = new Set<(v: boolean) => void>();
jest.mock('@shared/hooks/useReduceMotion', () => {
  const ReactActual = require('react');
  return {
    useReduceMotion: () => {
      const [value, setValue] = ReactActual.useState(mockReduceMotion);
      ReactActual.useEffect(() => {
        mockReduceMotionSubscribers.add(setValue);
        return () => {
          mockReduceMotionSubscribers.delete(setValue);
        };
      }, []);
      return value;
    },
  };
});
const setMockReduceMotion = (value: boolean) => {
  mockReduceMotion = value;
  act(() => {
    mockReduceMotionSubscribers.forEach((set) => set(value));
  });
};

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
    // Real react-native-maps' AnimatedRegion.setValue() sets the underlying
    // Animated.Values immediately, no animation — CarMarker's ingestFix
    // calls this on iOS to seed the marker at the first fix after a remount
    // instead of gliding to it (see that file's comment).
    setValue(_region: any) {}
  }
  return { __esModule: true, Marker, AnimatedRegion };
});

// markerPlayback is a value import CarMarker needs at runtime — stub the
// minimal surface it calls, same as driver-app's CarMarker.test.tsx.
jest.mock('@shared/utils/markerPlayback', () => {
  // pushFix/shouldResetBuffer are the REAL implementations (cheap, pure array
  // ops) wrapped in jest.fn() so bufferRef.current genuinely accumulates —
  // most describe blocks below never look at that array (playbackPosition
  // stays fully mocked, so the ticker's rendered position/bearing is always
  // whatever a test sets it to), but the "jump-triggered reset" describe
  // block needs isFirstFix to actually turn false after a real fix has been
  // ingested, which a permanent no-op mock could never produce. Ported from
  // driver-app's own CarMarker.test.tsx (2026-09-12 fix).
  const actual = jest.requireActual('@shared/utils/markerPlayback');
  return {
    PLAYBACK_DELAY_MS: 300,
    playbackPosition: jest.fn(() => null), // default: no buffered fix, ticker is a no-op
    pushFix: jest.fn(actual.pushFix),
    shouldResetBuffer: jest.fn(actual.shouldResetBuffer),
  };
});

jest.mock('expo-image', () => {
  const ReactActual = require('react');
  return { Image: (props: any) => ReactActual.createElement('ExpoImage', props) };
});

const mockCaptureException = jest.fn();
jest.mock('@shared/services/errorReporting', () => ({
  captureException: (...args: unknown[]) => mockCaptureException(...args),
}));

describe('CarMarker — pulsing ring respects Reduce Motion (shared copy)', () => {
  const coord = { latitude: 50.4452, longitude: -104.6189 };
  let loopSpy: jest.SpyInstance;

  beforeEach(() => {
    jest.useFakeTimers();
    loopSpy = jest.spyOn(Animated, 'loop');
  });
  afterEach(() => {
    loopSpy.mockRestore();
    mockReduceMotion = false;
    jest.useRealTimers();
  });

  it('starts the ring pulse loop when Reduce Motion is off', () => {
    const { unmount } = render(
      <CarMarker coordinate={coord} ring={{ color: '#F59E0B', pulsing: true }} />,
    );
    expect(loopSpy).toHaveBeenCalledTimes(1);
    unmount();
  });

  it('keeps the ring static (no loop) when Reduce Motion is on', () => {
    mockReduceMotion = true;
    const { unmount } = render(
      <CarMarker coordinate={coord} ring={{ color: '#F59E0B', pulsing: true }} />,
    );
    act(() => {
      jest.advanceTimersByTime(1400 * 3);
    });
    expect(loopSpy).not.toHaveBeenCalled();
    expect(() => unmount()).not.toThrow();
  });

  it('stops a running ring pulse when Reduce Motion turns on mid-session', () => {
    const ring = { color: '#F59E0B', pulsing: true };
    const { unmount } = render(<CarMarker coordinate={coord} ring={ring} />);
    const running = loopSpy.mock.results[0].value;
    const stopSpy = jest.spyOn(running, 'stop');

    setMockReduceMotion(true);
    expect(stopSpy).toHaveBeenCalled();
    expect(loopSpy).toHaveBeenCalledTimes(1);
    unmount();
  });
});
