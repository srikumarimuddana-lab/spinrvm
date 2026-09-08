/**
 * BrandSplash — the launch frame (driver-app).
 *
 * Guards the contracts app/_layout.tsx depends on, which are easy to break by
 * accident and impossible to notice in a simulator that boots instantly:
 *   - the native splash is only hidden once the mark has actually painted,
 *   - the exit always completes, even if an Animated callback is dropped,
 *   - reduce-motion users get no travel or spin,
 *   - no loading spinner ever appears up front.
 *
 * Code under test: driver-app/components/BrandSplash.tsx
 */
import React from 'react';
import { AccessibilityInfo, ActivityIndicator, StyleSheet } from 'react-native';
import { act, fireEvent, render } from '@testing-library/react-native';
import BrandSplash from '../../components/BrandSplash';
import { SPLASH_PROVENANCE, SPLASH_TAGLINE, SPLASH_TIMING } from '../../constants/splash';

// Scoped to setTimeout/clearTimeout only. Leaving requestAnimationFrame real
// matters here: this component drives native-driver Animated.timing, whose
// wiring breaks under a faked RAF (same reason as verifyEmailScreen.test.tsx).
beforeEach(() => {
  jest.useFakeTimers({
    doNotFake: [
      'nextTick', 'queueMicrotask',
      'requestAnimationFrame', 'cancelAnimationFrame',
      'requestIdleCallback', 'cancelIdleCallback',
      'setImmediate', 'clearImmediate',
      'setInterval', 'clearInterval',
      'Date', 'hrtime', 'performance',
    ],
  });
  jest.spyOn(AccessibilityInfo, 'isReduceMotionEnabled').mockResolvedValue(false);
});

afterEach(() => {
  jest.useRealTimers();
  jest.restoreAllMocks();
});

/**
 * Reads a style value whether React Native handed us the flattened number or
 * the Animated node itself — which of the two you get depends on the driver,
 * and the contract under test is the value, not the representation.
 */
const styleValue = (node: { props: { style?: unknown } }, key: string) => {
  const flat = StyleSheet.flatten(node.props.style as never) as Record<string, unknown>;
  const value = flat?.[key] as { __getValue?: () => unknown } | undefined;
  return value && typeof value.__getValue === 'function' ? value.__getValue() : value;
};

/** Let the reduce-motion promise settle before asserting. */
const settle = async () => {
  await act(async () => {
    await Promise.resolve();
  });
};

describe('BrandSplash', () => {
  it('renders the halo, the wordmark and the mark, labelled for screen readers', async () => {
    const view = render(<BrandSplash />);
    await settle();

    expect(view.getByTestId('brand-splash-glow')).toBeTruthy();
    expect(view.getByTestId('brand-splash-letters')).toBeTruthy();
    expect(view.getByTestId('brand-splash-mark')).toBeTruthy();
    // The driver lockup carries the "Driver" descriptor under the wordmark.
    expect(view.getByTestId('brand-splash-descriptor')).toBeTruthy();
    expect(view.getByText(SPLASH_TAGLINE)).toBeTruthy();
    expect(view.getByText(SPLASH_PROVENANCE)).toBeTruthy();

    const labelled = view.getByLabelText('Spinr Driver');
    expect(labelled.props.accessibilityRole).toBe('image');
  });

  it('shows no loading spinner while it is doing its job', async () => {
    const view = render(<BrandSplash />);
    await settle();
    expect(view.UNSAFE_queryAllByType(ActivityIndicator)).toHaveLength(0);
    expect(view.queryByTestId('brand-splash-trace')).toBeNull();
  });

  it('reports ready once — and only once the mark has painted', async () => {
    const onNativeHideReady = jest.fn();
    const view = render(<BrandSplash onNativeHideReady={onNativeHideReady} />);
    await settle();

    expect(onNativeHideReady).not.toHaveBeenCalled();

    fireEvent(view.getByTestId('brand-splash-mark'), 'load');
    expect(onNativeHideReady).toHaveBeenCalledTimes(1);

    // A second decode (re-layout, source re-resolve) must not hide twice.
    fireEvent(view.getByTestId('brand-splash-mark'), 'load');
    expect(onNativeHideReady).toHaveBeenCalledTimes(1);
  });

  it('always finishes its exit, even if the animation callback never lands', async () => {
    const onExitComplete = jest.fn();
    const view = render(<BrandSplash onExitComplete={onExitComplete} />);
    await settle();

    view.rerender(<BrandSplash phase="exit" onExitComplete={onExitComplete} />);
    // Nothing can have completed in zero elapsed time.
    expect(onExitComplete).not.toHaveBeenCalled();

    act(() => {
      jest.advanceTimersByTime(SPLASH_TIMING.exitMs + SPLASH_TIMING.exitFallbackMs + 10);
    });
    // Exactly once: the animation callback and the fallback timer share a guard,
    // so whichever arrives first wins and the other is a no-op.
    expect(onExitComplete).toHaveBeenCalledTimes(1);
  });

  it('admits to a slow boot only after the threshold', async () => {
    const view = render(<BrandSplash />);
    await settle();

    act(() => {
      jest.advanceTimersByTime(SPLASH_TIMING.slowBootMs - 1);
    });
    expect(view.queryByTestId('brand-splash-trace')).toBeNull();

    act(() => {
      jest.advanceTimersByTime(2);
    });
    expect(view.getByTestId('brand-splash-trace')).toBeTruthy();
  });

  it('does not move the mark when the user asked for reduced motion', async () => {
    (AccessibilityInfo.isReduceMotionEnabled as jest.Mock).mockResolvedValue(true);
    const view = render(<BrandSplash />);
    await settle();

    const transform = styleValue(view.getByTestId('brand-splash-mark'), 'transform') as
      | { rotate?: string }[]
      | undefined;
    // The mark is parked at its resting position: no half-turn left to unwind.
    expect(transform?.some(entry => entry.rotate === '-180deg')).toBeFalsy();
  });

  it('holds the type back until the brand font is real', async () => {
    const view = render(<BrandSplash fontsReady={false} />);
    await settle();

    act(() => {
      jest.advanceTimersByTime(SPLASH_TIMING.taglineDelayMs + SPLASH_TIMING.taglineMs + 100);
    });
    expect(styleValue(view.getByTestId('brand-splash-tagline'), 'opacity')).toBe(0);
  });
});
