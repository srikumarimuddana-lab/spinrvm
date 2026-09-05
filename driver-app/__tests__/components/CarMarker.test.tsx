import React from 'react';
import { render, act } from '@testing-library/react-native';
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
  }
  return { __esModule: true, Marker, AnimatedRegion };
});

// markerPlayback is a value import CarMarker needs at runtime; no driver-app
// mock exists for it (unlike vehicleTracking, which jest.config.js maps to
// the real module), so stub the minimal surface CarMarker calls.
jest.mock('@shared/utils/markerPlayback', () => ({
  PLAYBACK_DELAY_MS: 300,
  // jest.fn() (not a plain arrow) so individual tests can override the
  // return value to exercise the ticker's bearing-selection path.
  playbackPosition: jest.fn(() => null), // default: no buffered fix, ticker is a no-op
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
