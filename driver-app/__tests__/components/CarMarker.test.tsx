import React from 'react';
import { render, act } from '@testing-library/react-native';
import { Platform } from 'react-native';
import { Marker, AnimatedRegion } from 'react-native-maps';
import { Image } from 'expo-image';
import { CarMarker } from '../../components/CarMarker';
import { playbackPosition, pushFix } from '@shared/utils/markerPlayback';

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

// markerPlayback is a value import CarMarker needs at runtime; no driver-app
// mock exists for it (unlike vehicleTracking, which jest.config.js maps to
// the real module), so stub the minimal surface CarMarker calls.
jest.mock('@shared/utils/markerPlayback', () => {
  // pushFix/shouldResetBuffer are the REAL implementations (cheap, pure array
  // ops) wrapped in jest.fn() so bufferRef.current genuinely accumulates —
  // most describe blocks below never look at that array (playbackPosition
  // stays fully mocked, so the ticker's rendered position/bearing is always
  // whatever a test sets it to, independent of the real buffer), but the
  // "jump-triggered reset" describe block needs isFirstFix to actually turn
  // false after a real fix has been ingested, which a permanent no-op mock
  // could never produce.
  const actual = jest.requireActual('@shared/utils/markerPlayback');
  return {
    PLAYBACK_DELAY_MS: 300,
    // jest.fn() (not a plain arrow) so individual tests can override the
    // return value to exercise the ticker's bearing-selection path.
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

describe('CarMarker — car-icon decode failure retries then reports once (2026-09-09, "green circle, never a car")', () => {
  beforeEach(() => {
    jest.useFakeTimers();
    mockCaptureException.mockClear();
  });
  afterEach(() => {
    jest.useRealTimers();
  });

  it('retries a failed decode with backoff, remounting a fresh Image each time', () => {
    const { UNSAFE_getByType } = render(
      <CarMarker coordinate={{ latitude: 50.4452, longitude: -104.6189 }} heading={90} />,
    );

    const firstImage = UNSAFE_getByType(Image);
    // onError and the timer advance are deliberately in SEPARATE act() calls:
    // onError's setState updater schedules its setTimeout as a side effect of
    // being invoked, and act() only flushes/commits at the end of its own
    // callback — combining both statements into one act() call would advance
    // the fake clock before React has actually run the updater and
    // registered the timer, which never happens in real usage (a native
    // onError callback is itself already an async boundary).
    act(() => {
      firstImage.props.onError();
    });
    act(() => {
      jest.advanceTimersByTime(1000);
    });

    // A retry bumps the Image's key, producing a distinct element instance —
    // proof the native view actually remounted to re-attempt the decode,
    // not just re-rendered with the same broken one.
    const secondImage = UNSAFE_getByType(Image);
    expect(secondImage).not.toBe(firstImage);
    expect(mockCaptureException).not.toHaveBeenCalled();
  });

  it('reports to error tracking exactly once after MAX_IMAGE_RETRIES exhausted, never before', () => {
    const { UNSAFE_getByType } = render(
      <CarMarker coordinate={{ latitude: 50.4452, longitude: -104.6189 }} heading={90} />,
    );

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
      expect.objectContaining({ domain: 'drivers', surface: 'driver-app' }),
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
    const { UNSAFE_getByType } = render(
      <CarMarker coordinate={{ latitude: 50.4452, longitude: -104.6189 }} heading={90} />,
    );
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

describe('CarMarker — state-colored presence ring (round 9)', () => {
  beforeEach(() => {
    jest.useFakeTimers();
  });
  afterEach(() => {
    jest.useRealTimers();
  });

  const coord = { latitude: 50.4452, longitude: -104.6189 };

  it('renders a static ring (pulsing: false) without starting a loop', () => {
    const { unmount } = render(
      <CarMarker coordinate={coord} ring={{ color: '#10B981', pulsing: false }} />,
    );
    act(() => {
      jest.advanceTimersByTime(6000);
    });
    expect(() => unmount()).not.toThrow();
  });

  it('runs the pulse loop through several cycles and unmounts cleanly (loop.stop() reached)', () => {
    const { unmount } = render(
      <CarMarker coordinate={coord} ring={{ color: '#F59E0B', pulsing: true }} />,
    );
    // Several 1400ms pulse cycles, well past every other internal timer too.
    act(() => {
      jest.advanceTimersByTime(1400 * 4);
    });
    expect(() => unmount()).not.toThrow();
  });

  it('starting without a ring, then re-rendering into a pulsing ring, does not throw', () => {
    const { rerender, unmount } = render(<CarMarker coordinate={coord} />);
    act(() => {
      jest.advanceTimersByTime(1000);
    });
    expect(() =>
      rerender(<CarMarker coordinate={coord} ring={{ color: '#EF4444', pulsing: true }} />),
    ).not.toThrow();
    act(() => {
      jest.advanceTimersByTime(2800);
    });
    // Toggling pulsing off (e.g. a ride transitioning idle -> in-trip) must
    // stop the loop rather than leaving it running against a stale target.
    expect(() =>
      rerender(<CarMarker coordinate={coord} ring={{ color: '#EF4444', pulsing: false }} />),
    ).not.toThrow();
    act(() => {
      jest.advanceTimersByTime(2800);
    });
    expect(() => unmount()).not.toThrow();
  });
});

describe('CarMarker — onBearingChange (shared bearing source for map camera + icon)', () => {
  const coord = { latitude: 50.4452, longitude: -104.6189 };
  const mockPlaybackPosition = playbackPosition as jest.Mock;

  beforeEach(() => {
    jest.useFakeTimers();
    // ~89m north of `coord` — clears both the ticker's 0.5m churn guard and
    // selectBearing's MIN_BEARING_MOVE_M(3m), so the ticker actually selects
    // and applies a bearing this tick instead of no-op'ing.
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

  it('fires onBearingChange with the same bearing applied to the icon rotation, every tick a bearing is selected', () => {
    const onBearingChange = jest.fn();
    const { unmount } = render(
      <CarMarker coordinate={coord} onBearingChange={onBearingChange} />,
    );
    act(() => {
      jest.advanceTimersByTime(500); // one TICK_MS
    });
    // A caller (the map camera) reading this callback's value sees exactly
    // what the marker's own icon just rotated to — the fix this test guards:
    // before it, the camera computed a second, independent bearing that
    // could (and did) disagree with the icon.
    expect(onBearingChange).toHaveBeenCalledWith(42);
    unmount();
  });

  it('is optional — omitting it does not throw even though the ticker still selects a bearing', () => {
    const { unmount } = render(<CarMarker coordinate={coord} />);
    expect(() => act(() => jest.advanceTimersByTime(500))).not.toThrow();
    unmount();
  });

  it('fires onPositionChange with the exact position the ticker renders that tick', () => {
    const onPositionChange = jest.fn();
    const { unmount } = render(
      <CarMarker coordinate={coord} onPositionChange={onPositionChange} />,
    );
    act(() => {
      jest.advanceTimersByTime(500);
    });
    // A caller (the map camera) anchoring on this position sees exactly
    // where the icon renders — the fix this test guards: before it, a
    // follow camera anchored on the raw (undelayed) GPS fix instead, which
    // could drift far enough from the icon's actual (delayed) position that
    // the icon rendered outside the visible map area at speed.
    expect(onPositionChange).toHaveBeenCalledWith({
      latitude: 50.446,
      longitude: -104.6189,
    });
    unmount();
  });

  it('uses the playback spline bearing when the tick chord is under 3 m, so heading 0 cannot pin the car north', () => {
    // ~1.1 m north of `coord` — under selectBearing's MIN_BEARING_MOVE_M(3)
    // but interpolating, so coalescePlaybackBearing must take p.bearing 180
    // (south) instead of the Android placeholder heading 0.
    mockPlaybackPosition.mockReturnValue({
      coordinate: { latitude: 50.44521, longitude: -104.6189 },
      bearing: 180,
      mode: 'interpolating',
    });
    const onBearingChange = jest.fn();
    const { unmount } = render(
      <CarMarker coordinate={coord} heading={0} onBearingChange={onBearingChange} />,
    );
    act(() => {
      jest.advanceTimersByTime(500);
    });
    expect(onBearingChange).toHaveBeenCalledWith(180);
    unmount();
  });

  it('emits world-space bearing to onBearingChange even when the map camera is already rotated', () => {
    const mapHeadingRef = { current: 90 };
    const onBearingChange = jest.fn();
    const { unmount } = render(
      <CarMarker
        coordinate={coord}
        mapHeadingRef={mapHeadingRef}
        onBearingChange={onBearingChange}
      />,
    );
    act(() => {
      jest.advanceTimersByTime(500);
    });
    // Camera must keep receiving 42, not visualRotationDegrees(42, 90).
    // Feeding the offset back would lock course-up at heading 0.
    expect(onBearingChange).toHaveBeenCalledWith(42);
    unmount();
  });
});

describe('CarMarker — route-snap continuity hint survives a re-anchored live route (2026-09-11, "car drives sideways")', () => {
  // Degrees per metre at ~50°N: 1e-5° lat ≈ 1.11 m; use 1e-4° steps ≈ 11 m.
  const LAT0 = 50.4452;
  const LNG0 = -104.6189;
  const north = (m: number) => LAT0 + m * 9e-6;
  const east = (m: number) => LNG0 + m * 1.4e-5;
  const mockPlaybackPosition = playbackPosition as jest.Mock;

  beforeEach(() => {
    jest.useFakeTimers();
  });
  afterEach(() => {
    jest.useRealTimers();
    mockPlaybackPosition.mockReturnValue(null);
  });

  it('re-bases the segment hint on the new polyline instead of ratcheting into the post-turn segment', () => {
    // Route 1: a long straight road north, 12 segments of ~11 m. The car is
    // ~66 m up it, so the ticker snaps to segment ~6 and keeps that index.
    const route1 = Array.from({ length: 13 }, (_, i) => ({ latitude: north(i * 11), longitude: LNG0 }));
    const car = { latitude: north(66), longitude: LNG0 };
    mockPlaybackPosition.mockReturnValue({ coordinate: car, bearing: 0, mode: 'interpolating' });

    const onBearingChange = jest.fn();
    const { rerender, unmount } = render(
      <CarMarker coordinate={car} routeCoordinates={route1} onBearingChange={onBearingChange} />,
    );
    act(() => { jest.advanceTimersByTime(500); });
    expect(onBearingChange).toHaveBeenLastCalledWith(expect.any(Number));
    const straightBearing = onBearingChange.mock.calls.at(-1)![0] as number;
    expect(Math.min(straightBearing, 360 - straightBearing)).toBeLessThan(5); // north

    // Route 2: the live-route poll re-anchors at the car — segment 0 now
    // starts where the car is — and the road turns EAST 30 m ahead. With the
    // old index (~6) carried forward, the constrained search began at the
    // post-turn segments, found the corner within 35 m, and the icon took the
    // east-bound bearing while the car still drove north.
    const route2 = [
      car,
      { latitude: north(66 + 11), longitude: LNG0 },
      { latitude: north(66 + 22), longitude: LNG0 },
      { latitude: north(66 + 30), longitude: LNG0 }, // corner
      { latitude: north(66 + 30), longitude: east(11) },
      { latitude: north(66 + 30), longitude: east(22) },
      { latitude: north(66 + 30), longitude: east(33) },
      { latitude: north(66 + 30), longitude: east(44) },
      { latitude: north(66 + 30), longitude: east(55) },
      { latitude: north(66 + 30), longitude: east(66) },
    ];
    // Car has crept ~4 m north — still on the straight, well before the corner.
    const carNext = { latitude: north(70), longitude: LNG0 };
    mockPlaybackPosition.mockReturnValue({ coordinate: carNext, bearing: 0, mode: 'interpolating' });
    rerender(
      <CarMarker coordinate={carNext} routeCoordinates={route2} onBearingChange={onBearingChange} />,
    );
    act(() => { jest.advanceTimersByTime(500); });

    const afterRefresh = onBearingChange.mock.calls.at(-1)![0] as number;
    // Still north (0/360), NOT east (90).
    expect(Math.min(afterRefresh, 360 - afterRefresh)).toBeLessThan(10);
    expect(Math.abs(afterRefresh - 90)).toBeGreaterThan(45);
    unmount();
  });
});

describe('CarMarker — Android rotation interpolates through a turn, not a single snap (2026-09-09, "no smooth animation")', () => {
  const coord = { latitude: 50.4452, longitude: -104.6189 };
  const originalPlatformOS = Platform.OS;
  const mockPlaybackPosition = playbackPosition as jest.Mock;

  beforeEach(() => {
    jest.useFakeTimers();
    Platform.OS = 'android';
    // ~89m north of `coord`, bearing 90 — a sharp turn from the initial
    // heading=0 — clears both the ticker's 0.5m churn guard and
    // selectBearing's MIN_BEARING_MOVE_M so the tick actually applies it.
    mockPlaybackPosition.mockReturnValue({
      coordinate: { latitude: 50.446, longitude: -104.6189 },
      bearing: 90,
      mode: 'interpolating',
    });
  });
  afterEach(() => {
    jest.useRealTimers();
    Platform.OS = originalPlatformOS;
    mockPlaybackPosition.mockReturnValue(null);
  });

  it('passes through intermediate rotation values instead of jumping straight to the target', () => {
    const { UNSAFE_root, unmount } = render(
      <CarMarker coordinate={coord} heading={0} />,
    );

    act(() => {
      jest.advanceTimersByTime(500); // one TICK_MS — selects bearing 90, starts the tween
    });

    const seenValues = new Set<number>();
    for (let i = 0; i < 6; i++) {
      act(() => {
        jest.advanceTimersByTime(16); // ~1 animation frame
      });
      seenValues.add(UNSAFE_root.findByType(Marker).props.rotation);
    }

    // At least one sampled frame lands strictly between the start (0) and
    // target (90) heading — proof rotation is interpolated across frames,
    // not stepped to the target in a single jump the way it was before this
    // fix (which set androidRotation to the target directly, once, per tick).
    const midValues = [...seenValues].filter((v) => v > 0 && v < 90);
    expect(midValues.length).toBeGreaterThan(0);

    // And it settles exactly at the target once the tween completes.
    act(() => {
      jest.advanceTimersByTime(1000);
    });
    expect(UNSAFE_root.findByType(Marker).props.rotation).toBe(90);

    unmount();
  });

  it('does not throw across repeated ticks, unmount included (RAF loop cleans up)', () => {
    const { unmount } = render(<CarMarker coordinate={coord} heading={0} />);
    expect(() => {
      act(() => {
        jest.advanceTimersByTime(2000);
      });
    }).not.toThrow();
    expect(() => unmount()).not.toThrow();
  });
});

describe('CarMarker — physics-based jump rejection (ingestFix)', () => {
  const mockPushFix = pushFix as jest.Mock;
  const start = { latitude: 50.4452, longitude: -104.6189 };
  // A realistic epoch anchor: ingestFix falls back to Date.now() whenever a
  // fix's timestampMs is more than 60s away from it (guards a nonsense
  // device clock) — fake-timing Date.now() itself to track each render's
  // intended fixTimestampMs (rather than tiny synthetic values like `1_000`)
  // is what lets these tests control elapsed real time precisely instead of
  // depending on how long the test runner actually takes between rerenders.
  const BASE = 1_700_000_000_000;

  beforeEach(() => {
    jest.useFakeTimers();
    jest.setSystemTime(BASE);
    mockPushFix.mockClear();
  });
  afterEach(() => {
    jest.useRealTimers();
  });

  it('drops a fix implying an impossible speed instead of feeding it to the buffer', () => {
    const { rerender, unmount } = render(
      <CarMarker coordinate={start} fixTimestampMs={BASE} />,
    );
    expect(mockPushFix).toHaveBeenCalledTimes(1);

    // ~600m away, 2ms later — several hundred m/s, impossible for a vehicle.
    jest.setSystemTime(BASE + 2);
    const glitch = { latitude: start.latitude + 0.0054, longitude: start.longitude };
    rerender(<CarMarker coordinate={glitch} fixTimestampMs={BASE + 2} />);
    // ingestFix must have bailed before calling pushFix again.
    expect(mockPushFix).toHaveBeenCalledTimes(1);

    unmount();
  });

  it('still accepts a normal, plausible fix update', () => {
    const { rerender, unmount } = render(
      <CarMarker coordinate={start} fixTimestampMs={BASE} />,
    );
    expect(mockPushFix).toHaveBeenCalledTimes(1);

    // ~11m away, 2s later — an ordinary driving-speed segment.
    jest.setSystemTime(BASE + 2_000);
    const next = { latitude: start.latitude + 0.0001, longitude: start.longitude };
    rerender(<CarMarker coordinate={next} fixTimestampMs={BASE + 2_000} />);
    expect(mockPushFix).toHaveBeenCalledTimes(2);

    unmount();
  });

  it('accepts a large jump after a real elapsed gap (background/tunnel), not just small moves', () => {
    const { rerender, unmount } = render(
      <CarMarker coordinate={start} fixTimestampMs={BASE} />,
    );
    expect(mockPushFix).toHaveBeenCalledTimes(1);

    // ~3km away, 5 minutes later — a legitimate gap (~10 m/s average), not a glitch.
    jest.setSystemTime(BASE + 5 * 60_000);
    const afterGap = { latitude: start.latitude + 0.027, longitude: start.longitude };
    rerender(<CarMarker coordinate={afterGap} fixTimestampMs={BASE + 5 * 60_000} />);
    expect(mockPushFix).toHaveBeenCalledTimes(2);

    unmount();
  });
});

describe('CarMarker — first fix after a remount snaps instead of gliding (2026-09-11, "moved through the building")', () => {
  // Simulates index.tsx's mapKey remount on going back online: a fresh
  // CarMarker instance mounts with a STALE coordinate (wherever the driver
  // was before going offline), then the first real GPS fix after reacquiring
  // a signal can be a real few-hundred-metre jump. Before this fix, that
  // first fix had no "last buffered fix" for shouldResetBuffer to compare
  // against, so it always animated a glide instead of snapping — this
  // describe block asserts the instant-seed path instead.
  const staleMountCoord = { latitude: 50.4452, longitude: -104.6189 };
  // ~800m away — comfortably past SNAP_DISTANCE_M, representative of "moved
  // a distance while offline."
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

      jest.setSystemTime(BASE + 25_000); // GPS reacquiring after going online
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

describe('CarMarker — a jump-triggered reset does not re-open the raw-heading fallback (2026-09-12, "facing east")', () => {
  // hasMovementBearingRef exists so a platform-placeholder heading (Android's
  // literal 0 for "no bearing", or any other stale/garbage value) can never
  // override a direction the marker has already established from real
  // movement — see selectBearing()'s own doc comment. Before this fix, ANY
  // buffer reset (isFirstFix OR shouldResetBuffer) cleared that latch, which
  // re-opened the exact placeholder-heading window on every jump a live
  // vehicle goes through (offline->online remount, ride-end mapKey bump, a
  // real background/tunnel gap) — not just on a genuine first-ever mount.
  // Live-testing report: the marker snapping to a wrong heading ("facing
  // east") right after one of these resets.
  const mockPlaybackPosition = playbackPosition as jest.Mock;
  const mountCoord = { latitude: 50.4452, longitude: -104.6189 };
  // ~89m due north — clears MIN_BEARING_MOVE_M so the first tick selects a
  // real 'travel' bearing and sets hasMovementBearingRef true.
  const drivingNorthTo = { latitude: 50.446, longitude: -104.6189 };
  // ~800m away — comfortably past SNAP_DISTANCE_M(500), so shouldResetBuffer
  // (now the REAL implementation — see the module mock above) returns true.
  const jumpTarget = { latitude: mountCoord.latitude + 0.0072, longitude: mountCoord.longitude };
  const BASE = 1_700_000_000_000;

  beforeEach(() => {
    jest.useFakeTimers();
    jest.setSystemTime(BASE);
  });
  afterEach(() => {
    jest.useRealTimers();
    mockPlaybackPosition.mockReturnValue(null);
  });

  it('freezes the bearing instead of trusting a raw heading right after the jump, once movement had already established one', () => {
    mockPlaybackPosition.mockReturnValue({
      coordinate: drivingNorthTo,
      bearing: 0, // due north — a clean, distinct value from the "wrong" 90 below
      mode: 'interpolating',
    });
    const onBearingChange = jest.fn();
    const { rerender, unmount } = render(
      <CarMarker
        coordinate={mountCoord}
        heading={0}
        fixTimestampMs={BASE}
        onBearingChange={onBearingChange}
      />,
    );

    // Establish a real movement bearing (source: 'travel') — hasMovementBearingRef
    // is now true.
    act(() => { jest.advanceTimersByTime(500); });
    expect(onBearingChange).toHaveBeenCalledWith(0);
    expect(onBearingChange).toHaveBeenCalledTimes(1);

    // A real, plausible jump (~800m in 25s ≈ 32 m/s — under MAX_PLAUSIBLE_SPEED_MPS,
    // so isImplausibleJump does not reject it), arriving with a "wrong" raw
    // heading of 90 (east) — exactly the live-testing report's symptom.
    jest.setSystemTime(BASE + 25_000);
    mockPlaybackPosition.mockReturnValue({
      // ~1.1m from the reset anchor (jumpTarget, which the reset sets as
      // prevTargetRef.current): far enough to clear the ticker's 0.5m
      // "parked, skip churn" guard (which would return before ever calling
      // selectBearing, making this test pass vacuously either way) but well
      // under MIN_BEARING_MOVE_M(3), so selectBearing takes the "under
      // threshold" branch that falls back to the raw reported heading.
      coordinate: { latitude: jumpTarget.latitude + 0.00001, longitude: jumpTarget.longitude },
      bearing: null,
      mode: 'waiting', // not interpolating/extrapolating — no playback-spline override
    });
    rerender(
      <CarMarker
        coordinate={jumpTarget}
        heading={90}
        fixTimestampMs={BASE + 25_000}
        onBearingChange={onBearingChange}
      />,
    );
    act(() => { jest.advanceTimersByTime(500); });

    // Before this fix: the reset cleared hasMovementBearingRef, so the raw
    // heading (90) would win and onBearingChange would fire with it. After
    // this fix: hasMovementBearingRef survives a non-first-fix reset, so
    // selectBearing returns {bearing: null, source: 'none'} and the ticker
    // emits nothing this tick — the icon stays frozen at its last known
    // (north) heading instead of snapping to a placeholder-adjacent guess.
    expect(onBearingChange).not.toHaveBeenCalledWith(90);
    expect(onBearingChange).toHaveBeenCalledTimes(1);

    unmount();
  });
});

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

    // Ring appears (e.g. going online while idle) — must re-arm immediately,
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

  it('re-arms again when the ring disappears (e.g. going back offline), clearing a stale frozen ring', () => {
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

describe('CarMarker — iOS Apple Maps rotates the car PNG via view transform', () => {
  const coord = { latitude: 50.4452, longitude: -104.6189 };
  const originalPlatformOS = Platform.OS;

  beforeEach(() => {
    jest.useFakeTimers();
    Platform.OS = 'ios';
  });
  afterEach(() => {
    jest.useRealTimers();
    Platform.OS = originalPlatformOS;
  });

  it('renders an iOS-only rotate wrapper around the car image', () => {
    const { getByTestId, unmount } = render(<CarMarker coordinate={coord} heading={90} />);
    expect(getByTestId('car-marker-ios-rotate')).toBeTruthy();
    unmount();
  });

  it('does not freeze tracksViewChanges after image load (a frozen snapshot would pin the PNG north)', () => {
    const { UNSAFE_root, unmount } = render(<CarMarker coordinate={coord} heading={180} />);
    act(() => {
      UNSAFE_root.findByType(Image).props.onLoad();
    });
    act(() => {
      jest.advanceTimersByTime(350);
    });
    // iOS uses Marker.Animated (host name MarkerAnimated in the maps mock).
    const marker = UNSAFE_root.findByType('MarkerAnimated' as any);
    expect(marker.props.tracksViewChanges).toBe(true);
    unmount();
  });
});

describe('CarMarker — Android does not use the iOS rotate wrapper', () => {
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

  it('does not render car-marker-ios-rotate (Marker.rotation is the Android path)', () => {
    const { queryByTestId, unmount } = render(<CarMarker coordinate={coord} heading={90} />);
    expect(queryByTestId('car-marker-ios-rotate')).toBeNull();
    unmount();
  });
});
