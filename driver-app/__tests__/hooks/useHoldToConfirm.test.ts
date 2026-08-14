/**
 * Press-and-hold-for-N-ms confirmation gesture (ACTION_ITEMS.md B16), shared
 * by the driver Safety shield's own hold and the Safety overlay's "Alert
 * Emergency Contacts" hold button.
 *
 * Test-file placement note: this hook lives in shared/hooks/ (it's imported
 * via @shared/hooks/useHoldToConfirm), but its test lives here under
 * driver-app/__tests__/ rather than shared/hooks/__tests__/ — shared/'s own
 * __tests__ directories are not picked up by any CI job today (driver-app-test
 * and rider-app-test each run plain `jest` scoped to their own rootDir; no
 * job runs tests from shared/ directly). Placing it here is what actually
 * makes it run in CI, since this hook is driver-app-only (react-native-svg-
 * backed callers) anyway.
 *
 * Code under test: shared/hooks/useHoldToConfirm.ts
 */

import { act, renderHook } from '@testing-library/react-native';
import { useHoldToConfirm } from '@shared/hooks/useHoldToConfirm';

beforeEach(() => {
  jest.useFakeTimers();
});

afterEach(() => {
  jest.useRealTimers();
});

describe('useHoldToConfirm', () => {
  it('fires onConfirm exactly once after a full-duration hold', () => {
    const onConfirm = jest.fn();
    const { result } = renderHook(() => useHoldToConfirm({ durationMs: 3000, onConfirm }));

    act(() => {
      result.current.onPressIn();
    });
    expect(result.current.pressing).toBe(true);

    act(() => {
      jest.advanceTimersByTime(3000);
    });

    expect(onConfirm).toHaveBeenCalledTimes(1);
    // Resets after firing so a subsequent press starts clean.
    expect(result.current.pressing).toBe(false);
  });

  it('does not fire and resets progress if released before the duration', () => {
    const onConfirm = jest.fn();
    const { result } = renderHook(() => useHoldToConfirm({ durationMs: 3000, onConfirm }));

    act(() => {
      result.current.onPressIn();
    });
    act(() => {
      jest.advanceTimersByTime(1500);
    });
    act(() => {
      result.current.onPressOut();
    });
    act(() => {
      // Advance well past the original duration -- must not fire late.
      jest.advanceTimersByTime(3000);
    });

    expect(onConfirm).not.toHaveBeenCalled();
    expect(result.current.pressing).toBe(false);
  });

  it('a second full hold after an early release fires onConfirm normally', () => {
    const onConfirm = jest.fn();
    const { result } = renderHook(() => useHoldToConfirm({ durationMs: 3000, onConfirm }));

    act(() => {
      result.current.onPressIn();
    });
    act(() => {
      jest.advanceTimersByTime(1000);
    });
    act(() => {
      result.current.onPressOut();
    });

    act(() => {
      result.current.onPressIn();
    });
    act(() => {
      jest.advanceTimersByTime(3000);
    });

    expect(onConfirm).toHaveBeenCalledTimes(1);
  });
});
