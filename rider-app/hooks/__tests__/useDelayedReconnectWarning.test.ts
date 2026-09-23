import { act, renderHook } from '@testing-library/react-native';
import {
  RIDER_RECONNECT_WARNING_DELAY_MS,
  useDelayedReconnectWarning,
} from '../useDelayedReconnectWarning';

beforeEach(() => {
  jest.useFakeTimers();
});

afterEach(() => {
  jest.useRealTimers();
});

describe('useDelayedReconnectWarning', () => {
  it('suppresses short reconnects', () => {
    const { result, rerender } = renderHook(
      (props: { reconnecting: boolean; offline: boolean }) =>
        useDelayedReconnectWarning(props.reconnecting, props.offline),
      { initialProps: { reconnecting: true, offline: false } },
    );

    act(() => {
      jest.advanceTimersByTime(RIDER_RECONNECT_WARNING_DELAY_MS - 1);
    });
    expect(result.current).toBe(false);

    rerender({ reconnecting: false, offline: false });
    act(() => {
      jest.advanceTimersByTime(RIDER_RECONNECT_WARNING_DELAY_MS);
    });
    expect(result.current).toBe(false);
  });

  it('shows a notice only after a sustained reconnect and hides it on recovery', () => {
    const { result, rerender } = renderHook(
      (props: { reconnecting: boolean; offline: boolean }) =>
        useDelayedReconnectWarning(props.reconnecting, props.offline),
      { initialProps: { reconnecting: true, offline: false } },
    );

    act(() => {
      jest.advanceTimersByTime(RIDER_RECONNECT_WARNING_DELAY_MS);
    });
    expect(result.current).toBe(true);

    rerender({ reconnecting: false, offline: false });
    expect(result.current).toBe(false);
  });

  it('suppresses the ride notice when the offline banner can explain the interruption', () => {
    const { result, rerender } = renderHook(
      (props: { reconnecting: boolean; offline: boolean }) =>
        useDelayedReconnectWarning(props.reconnecting, props.offline),
      { initialProps: { reconnecting: true, offline: true } },
    );

    act(() => {
      jest.advanceTimersByTime(RIDER_RECONNECT_WARNING_DELAY_MS);
    });
    expect(result.current).toBe(false);

    rerender({ reconnecting: true, offline: false });
    act(() => {
      jest.advanceTimersByTime(RIDER_RECONNECT_WARNING_DELAY_MS);
    });
    expect(result.current).toBe(true);
  });
});
