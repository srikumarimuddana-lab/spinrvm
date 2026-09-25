import { act, renderHook, waitFor } from '@testing-library/react-native';
import { AccessibilityInfo } from 'react-native';
import { useReduceMotion } from '../useReduceMotion';

type Listener = (enabled: boolean) => void;

describe('useReduceMotion', () => {
  let listener: Listener | null;
  let remove: jest.Mock;

  beforeEach(() => {
    listener = null;
    remove = jest.fn();
    jest.spyOn(AccessibilityInfo, 'addEventListener').mockImplementation(((
      _event: string,
      handler: Listener,
    ) => {
      listener = handler;
      return { remove };
    }) as any);
  });

  afterEach(() => {
    jest.restoreAllMocks();
  });

  it('defaults to false before the OS read resolves', () => {
    jest
      .spyOn(AccessibilityInfo, 'isReduceMotionEnabled')
      .mockReturnValue(new Promise(() => {}));
    const { result } = renderHook(() => useReduceMotion());
    expect(result.current).toBe(false);
  });

  it('reflects the OS setting once read', async () => {
    jest.spyOn(AccessibilityInfo, 'isReduceMotionEnabled').mockResolvedValue(true);
    const { result } = renderHook(() => useReduceMotion());
    await waitFor(() => expect(result.current).toBe(true));
  });

  it('updates live when the setting changes while mounted', async () => {
    jest.spyOn(AccessibilityInfo, 'isReduceMotionEnabled').mockResolvedValue(false);
    const { result } = renderHook(() => useReduceMotion());
    await waitFor(() => expect(listener).not.toBeNull());

    act(() => listener!(true));
    expect(result.current).toBe(true);

    act(() => listener!(false));
    expect(result.current).toBe(false);
  });

  it('stays false if the OS read rejects', async () => {
    jest
      .spyOn(AccessibilityInfo, 'isReduceMotionEnabled')
      .mockRejectedValue(new Error('unavailable'));
    const { result } = renderHook(() => useReduceMotion());
    await act(async () => {});
    expect(result.current).toBe(false);
  });

  it('removes the listener on unmount', () => {
    jest.spyOn(AccessibilityInfo, 'isReduceMotionEnabled').mockResolvedValue(false);
    const { unmount } = renderHook(() => useReduceMotion());
    unmount();
    expect(remove).toHaveBeenCalledTimes(1);
  });
});
