/**
 * Tests for the shared motion constants and shake sequence
 * (ACTION_ITEMS.md UX4) — pins the shared step duration/easing and the
 * decaying back-and-forth shape used by both rider-app's OTP shake and
 * driver-app's PIN shake.
 */
import { Animated, Easing } from 'react-native';
import { TIMING, EASING, shakeHorizontal } from '../motion';

describe('TIMING / EASING', () => {
  it('exposes a single shared shake step duration', () => {
    expect(TIMING.shakeStep).toBe(50);
  });

  it('exposes a linear shake easing curve, distinct from the RN default', () => {
    expect(EASING.shake).toBe(Easing.linear);
    expect(EASING.standard).not.toBe(EASING.shake);
  });
});

describe('shakeHorizontal', () => {
  it('runs a 5-leg decaying sequence at the shared step duration/easing using the default amplitude', () => {
    const timingSpy = jest.spyOn(Animated, 'timing');
    const value = new Animated.Value(0);

    shakeHorizontal(value);

    expect(timingSpy).toHaveBeenCalledTimes(5);
    const calls = timingSpy.mock.calls.map(([, config]) => config);
    expect(calls.map(c => c.toValue)).toEqual([12, -12, 8, -8, 0]);
    for (const config of calls) {
      expect(config.duration).toBe(TIMING.shakeStep);
      expect(config.easing).toBe(EASING.shake);
      expect(config.useNativeDriver).toBe(true);
    }

    timingSpy.mockRestore();
  });

  it('honours a caller-supplied amplitude (e.g. driver-app PIN shake) without changing timing/easing', () => {
    const timingSpy = jest.spyOn(Animated, 'timing');
    const value = new Animated.Value(0);

    shakeHorizontal(value, [10, 6]);

    const calls = timingSpy.mock.calls.map(([, config]) => config);
    expect(calls.map(c => c.toValue)).toEqual([10, -10, 6, -6, 0]);
    for (const config of calls) {
      expect(config.duration).toBe(TIMING.shakeStep);
      expect(config.easing).toBe(EASING.shake);
    }

    timingSpy.mockRestore();
  });
});
