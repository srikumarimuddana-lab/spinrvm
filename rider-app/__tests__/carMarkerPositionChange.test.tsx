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
import { Animated, Platform } from 'react-native';
import { Marker, AnimatedRegion } from 'react-native-maps';
import { Image } from 'expo-image';
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

const mockCaptureException = jest.fn();
jest.mock('@shared/services/errorReporting', () => ({
  captureException: (...args: unknown[]) => mockCaptureException(...args),
}));

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

/**
 * Ported from driver-app's own copy of this component (see
 * driver-app/__tests__/components/CarMarker.test.tsx, "Android ring-change
 * re-arms the frozen snapshot" describe block, 2026-09-05 fix) after the same
 * root cause was found to still be present in rider-app: driver-arriving.tsx,
 * driver-arrived.tsx, and ride-in-progress.tsx all mount CarMarker with the
 * `ring` prop already set on first render (screen navigation, or returning to
 * the app mid-ride), which races the car icon's image decode the same way
 * driver-app's offline->online mapKey remount did.
 */
describe('CarMarker — Android ring-change re-arms the frozen snapshot', () => {
  const coord = { latitude: 50.4452, longitude: -104.6189 };
  const originalPlatformOS = Platform.OS;

  beforeEach(() => {
    jest.useFakeTimers();
    Platform.OS = 'android';
  });
  afterEach(() => {
    jest.useRealTimers();
    Platform.OS = originalPlatformOS;
  });

  // Fires the car Image's onLoad, then lets its 350ms settle timer freeze
  // tracksViewChanges — the ordinary (working) mount path.
  function loadImageAndSettle(root: any) {
    act(() => {
      root.findByType(Image).props.onLoad();
    });
    act(() => {
      jest.advanceTimersByTime(350);
    });
  }

  it('immediately re-arms tracksViewChanges when the ring prop changes after freezing', () => {
    const { UNSAFE_root, rerender, unmount } = render(<CarMarker coordinate={coord} ring={null} />);
    loadImageAndSettle(UNSAFE_root);
    expect(UNSAFE_root.findByType(Marker).props.tracksViewChanges).toBe(false);

    // Ring appears (e.g. a ride status update) — must re-arm immediately,
    // before any timer advances, so the native renderer gets a fresh chance
    // to snapshot the car image alongside the now-visible ring.
    rerender(<CarMarker coordinate={coord} ring={{ color: '#10B981', pulsing: false }} />);
    expect(UNSAFE_root.findByType(Marker).props.tracksViewChanges).toBe(true);

    // And settles back to false on the same 350ms schedule, since the image
    // was already loaded before this ring change.
    act(() => {
      jest.advanceTimersByTime(350);
    });
    expect(UNSAFE_root.findByType(Marker).props.tracksViewChanges).toBe(false);

    unmount();
  });

  it('re-arms again when the ring disappears, clearing a stale frozen ring', () => {
    const { UNSAFE_root, rerender, unmount } = render(
      <CarMarker coordinate={coord} ring={{ color: '#10B981', pulsing: false }} />,
    );
    loadImageAndSettle(UNSAFE_root);
    expect(UNSAFE_root.findByType(Marker).props.tracksViewChanges).toBe(false);

    rerender(<CarMarker coordinate={coord} ring={null} />);
    expect(UNSAFE_root.findByType(Marker).props.tracksViewChanges).toBe(true);

    unmount();
  });

  it('does not re-freeze prematurely if the ring changes before the image has ever loaded', () => {
    const { UNSAFE_root, rerender, unmount } = render(<CarMarker coordinate={coord} ring={null} />);
    // No onLoad fired yet — tracksViewChanges is still true from mount.
    expect(UNSAFE_root.findByType(Marker).props.tracksViewChanges).toBe(true);

    rerender(<CarMarker coordinate={coord} ring={{ color: '#10B981', pulsing: false }} />);
    // Still true — must not schedule a 350ms freeze ahead of the image
    // actually loading, or this reproduces the exact bug being fixed.
    expect(UNSAFE_root.findByType(Marker).props.tracksViewChanges).toBe(true);
    act(() => {
      jest.advanceTimersByTime(350);
    });
    expect(UNSAFE_root.findByType(Marker).props.tracksViewChanges).toBe(true);

    // Only the hard cap (or a real onLoad) may freeze it from here.
    act(() => {
      jest.advanceTimersByTime(5000);
    });
    expect(UNSAFE_root.findByType(Marker).props.tracksViewChanges).toBe(false);

    unmount();
  });

  it('is a no-op when re-rendered with the same ring identity (no redundant re-arm)', () => {
    const ring = { color: '#10B981', pulsing: false };
    const { UNSAFE_root, rerender, unmount } = render(<CarMarker coordinate={coord} ring={ring} />);
    loadImageAndSettle(UNSAFE_root);
    expect(UNSAFE_root.findByType(Marker).props.tracksViewChanges).toBe(false);

    // Same color/pulsing, new object identity (e.g. a parent re-render) —
    // must NOT re-arm; only an actual identity (color/pulsing) change should.
    rerender(<CarMarker coordinate={coord} ring={{ color: '#10B981', pulsing: false }} />);
    expect(UNSAFE_root.findByType(Marker).props.tracksViewChanges).toBe(false);

    unmount();
  });
});

/**
 * Ported from driver-app's own copy of this component (see
 * driver-app/__tests__/components/CarMarker.test.tsx, "car-icon decode
 * failure retries then reports once" describe block, 2026-09-09 fix) after
 * the same gap was found in rider-app's copy: a bundled-asset decode failure
 * had no way back (onError only ever toggled the custom-vs-bundled image
 * choice, a no-op with no custom image), so the marker froze at the ring-only
 * snapshot forever with nothing surfaced to monitoring.
 */
describe('CarMarker — car-icon decode failure retries then reports once', () => {
  const coord = { latitude: 50.4452, longitude: -104.6189 };

  beforeEach(() => {
    jest.useFakeTimers();
    mockCaptureException.mockClear();
  });
  afterEach(() => {
    jest.useRealTimers();
  });

  it('retries a failed decode with backoff, remounting a fresh Image each time', () => {
    const { UNSAFE_getByType } = render(<CarMarker coordinate={coord} heading={90} />);

    const firstImage = UNSAFE_getByType(Image);
    // onError and the timer advance are deliberately in SEPARATE act() calls
    // — see driver-app's original test for why (onError's setState updater
    // schedules its setTimeout as a side effect of being invoked, and act()
    // only flushes/commits at the end of its own callback).
    act(() => {
      firstImage.props.onError();
    });
    act(() => {
      jest.advanceTimersByTime(1000);
    });

    // A retry bumps the Image's key, producing a distinct element instance —
    // proof the native view actually remounted to re-attempt the decode.
    const secondImage = UNSAFE_getByType(Image);
    expect(secondImage).not.toBe(firstImage);
    expect(mockCaptureException).not.toHaveBeenCalled();
  });

  it('reports to error tracking exactly once after MAX_IMAGE_RETRIES exhausted, never before', () => {
    const { UNSAFE_getByType } = render(<CarMarker coordinate={coord} heading={90} />);

    // 4 failures: 3 retries (0->1->2->3), the 4th finds retries exhausted.
    for (let i = 0; i < 4; i++) {
      act(() => {
        UNSAFE_getByType(Image).props.onError();
      });
      act(() => {
        jest.advanceTimersByTime(2000);
      });
    }

    expect(mockCaptureException).toHaveBeenCalledTimes(1);
    expect(mockCaptureException).toHaveBeenCalledWith(
      expect.any(Error),
      expect.objectContaining({ domain: 'rides', surface: 'rider-app' }),
    );

    // A further failure past exhaustion must not report again.
    act(() => {
      UNSAFE_getByType(Image).props.onError();
    });
    act(() => {
      jest.advanceTimersByTime(2000);
    });
    expect(mockCaptureException).toHaveBeenCalledTimes(1);
  });

  it('never reports once the image successfully loads', () => {
    const { UNSAFE_getByType } = render(<CarMarker coordinate={coord} heading={90} />);
    act(() => {
      UNSAFE_getByType(Image).props.onError();
    });
    act(() => {
      jest.advanceTimersByTime(1000);
    });
    act(() => {
      UNSAFE_getByType(Image).props.onLoad();
    });
    act(() => {
      jest.advanceTimersByTime(6000);
    });
    expect(mockCaptureException).not.toHaveBeenCalled();
  });
});

/**
 * Ported from driver-app's own copy of this component (2026-09-09 fix,
 * "green circle, no car" recurrence): the mount animation used to start at
 * opacity 0 / scale 0 on every platform, racing Android's one-shot marker
 * snapshot against a native-driven fade-in that doesn't reliably re-trigger
 * that snapshot. Starting at full opacity/scale on Android closes the race.
 */
describe('CarMarker — Android mount starts the car icon visible, not faded in', () => {
  const coord = { latitude: 50.4452, longitude: -104.6189 };
  const originalPlatformOS = Platform.OS;

  afterEach(() => {
    Platform.OS = originalPlatformOS;
  });

  it('mounts with opacity 1 / scale 1 on Android (no fade-in to race the snapshot)', () => {
    Platform.OS = 'android';
    const { UNSAFE_root, unmount } = render(<CarMarker coordinate={coord} />);
    // The mount Animated.Value itself (not a rendered style snapshot) is the
    // authoritative check — read via the rendered wrapper's style prop.
    const animatedWrapper = UNSAFE_root.findAllByType(Animated.View)[0];
    expect(animatedWrapper.props.style.opacity.__getValue()).toBe(1);
    expect(animatedWrapper.props.style.transform[0].scale.__getValue()).toBe(1);
    unmount();
  });

  it('mounts with opacity 0 / scale 0 on iOS (unchanged pop-in behavior — spring plays from there)', () => {
    Platform.OS = 'ios';
    const { UNSAFE_root, unmount } = render(<CarMarker coordinate={coord} />);
    const animatedWrapper = UNSAFE_root.findAllByType(Animated.View)[0];
    // Only the starting value is asserted here — the native-driven spring
    // itself isn't observable through fake timers in this test environment.
    // iOS has no marker-snapshot race to guard against (Marker.Animated
    // re-renders live), so starting faded-out and popping in is safe there;
    // the regression this file guards is Android-only (see the sibling test).
    expect(animatedWrapper.props.style.opacity.__getValue()).toBe(0);
    expect(animatedWrapper.props.style.transform[0].scale.__getValue()).toBe(0);
    unmount();
  });
});

/**
 * Ported from driver-app's own copy of this component (2026-09-11 fix,
 * live-testing report "icon moved through the building using the shortest
 * path"): shouldResetBuffer only ever compares a new fix against an
 * EXISTING last buffered fix, so the very first fix into a freshly-mounted/
 * empty buffer could never trigger the instant-snap path, no matter how far
 * the real position had moved. rider-app remounts this component on every
 * ride-phase screen transition the same way driver-app's mapKey remount did
 * (ride-options -> driver-arriving -> driver-arrived -> ride-in-progress),
 * so the identical race applies here.
 */
describe('CarMarker — first fix after a remount snaps instead of gliding (2026-09-11, "moved through the building")', () => {
  const staleMountCoord = { latitude: 50.4452, longitude: -104.6189 };
  // ~800m away — comfortably past SNAP_DISTANCE_M, representative of a
  // screen-transition remount after the assigned driver has moved.
  const realFirstFix = { latitude: staleMountCoord.latitude + 0.0072, longitude: staleMountCoord.longitude };
  const BASE = 1_700_000_000_000;

  beforeEach(() => {
    jest.useFakeTimers();
    jest.setSystemTime(BASE);
  });
  afterEach(() => {
    jest.useRealTimers();
  });

  it('Android: seeds androidCoord at the raw fix synchronously, before any ticker animation', () => {
    const originalPlatformOS = Platform.OS;
    Platform.OS = 'android';
    try {
      const { UNSAFE_getByType, rerender, unmount } = render(
        <CarMarker coordinate={staleMountCoord} fixTimestampMs={BASE} />,
      );
      expect(UNSAFE_getByType(Marker).props.coordinate).toEqual(staleMountCoord);

      jest.setSystemTime(BASE + 25_000); // driver moved during the screen transition
      rerender(<CarMarker coordinate={realFirstFix} fixTimestampMs={BASE + 25_000} />);

      // No jest.advanceTimersByTime here — this must be true immediately
      // after the prop-change effect runs, not after a playback tick.
      expect(UNSAFE_getByType(Marker).props.coordinate).toEqual(realFirstFix);

      unmount();
    } finally {
      Platform.OS = originalPlatformOS;
    }
  });

  it('iOS: seeds the AnimatedRegion at the raw fix synchronously via setValue', () => {
    const originalPlatformOS = Platform.OS;
    Platform.OS = 'ios';
    const setValueSpy = jest.spyOn(AnimatedRegion.prototype, 'setValue');
    try {
      const { rerender, unmount } = render(
        <CarMarker coordinate={staleMountCoord} fixTimestampMs={BASE} />,
      );
      // The mount-time ingest also seeds setValue once, harmlessly, at the
      // mount coordinate itself (bufferRef is empty then too) — this test
      // is about what happens on the NEXT fix, not the absence of the first.

      jest.setSystemTime(BASE + 25_000);
      rerender(<CarMarker coordinate={realFirstFix} fixTimestampMs={BASE + 25_000} />);

      expect(setValueSpy).toHaveBeenLastCalledWith({
        latitude: realFirstFix.latitude,
        longitude: realFirstFix.longitude,
        latitudeDelta: 0,
        longitudeDelta: 0,
      });

      unmount();
    } finally {
      setValueSpy.mockRestore();
      Platform.OS = originalPlatformOS;
    }
  });
});
