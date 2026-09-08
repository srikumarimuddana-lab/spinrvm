/**
 * useSplashPhase — when the launch splash is allowed to fade.
 *
 * The subtle rule this pins down: app/index.tsx is a transparent routing gate,
 * so fading as soon as the app is ready would cross-fade onto a blank screen
 * and let the real one slide in afterwards. We wait for a real route, but never
 * forever.
 *
 * Code under test: driver-app/hooks/useSplashPhase.ts
 */
import { act, renderHook } from '@testing-library/react-native';
import {
  SPLASH_EXIT_ROUTE_WAIT_MS,
  SPLASH_EXIT_SETTLE_MS,
  useSplashPhase,
} from '../../hooks/useSplashPhase';

beforeEach(() => {
  jest.useFakeTimers();
});

afterEach(() => {
  jest.useRealTimers();
});

describe('useSplashPhase', () => {
  it('stays on the intro while the app is still booting', () => {
    const { result } = renderHook(() => useSplashPhase({ navReady: false, pathname: '/' }));
    act(() => {
      jest.advanceTimersByTime(SPLASH_EXIT_ROUTE_WAIT_MS * 2);
    });
    expect(result.current.phase).toBe('intro');
  });

  it('fades shortly after a real route mounts', () => {
    // The parameter is annotated rather than destructured so renderHook can
    // infer its Props type — @testing-library/react-native 13 widens it to
    // `unknown` otherwise, and `result.current` goes with it.
    const { result, rerender } = renderHook(
      (props: { navReady: boolean; pathname: string }) => useSplashPhase(props),
      { initialProps: { navReady: true, pathname: '/' } },
    );

    // Still on the routing gate: hold, don't fade onto a blank screen.
    act(() => {
      jest.advanceTimersByTime(SPLASH_EXIT_SETTLE_MS + 10);
    });
    expect(result.current.phase).toBe('intro');

    rerender({ navReady: true, pathname: '/login' });
    act(() => {
      jest.advanceTimersByTime(SPLASH_EXIT_SETTLE_MS + 1);
    });
    expect(result.current.phase).toBe('exit');
  });

  it('gives up waiting for a route rather than stranding the splash', () => {
    const { result } = renderHook(() => useSplashPhase({ navReady: true, pathname: '/' }));
    act(() => {
      jest.advanceTimersByTime(SPLASH_EXIT_ROUTE_WAIT_MS + 1);
    });
    expect(result.current.phase).toBe('exit');
  });

  it('exits promptly when the app opens straight onto the driver home', () => {
    const { result } = renderHook(() =>
      useSplashPhase({ navReady: true, pathname: '/driver' }),
    );
    act(() => {
      jest.advanceTimersByTime(SPLASH_EXIT_SETTLE_MS + 1);
    });
    expect(result.current.phase).toBe('exit');
  });

  it('finishes at done, and stays there', () => {
    const { result } = renderHook(() => useSplashPhase({ navReady: true, pathname: '/login' }));
    act(() => {
      jest.advanceTimersByTime(SPLASH_EXIT_SETTLE_MS + 1);
    });
    expect(result.current.phase).toBe('exit');

    act(() => {
      result.current.onExitComplete();
    });
    expect(result.current.phase).toBe('done');

    act(() => {
      result.current.onExitComplete();
      jest.advanceTimersByTime(SPLASH_EXIT_ROUTE_WAIT_MS);
    });
    expect(result.current.phase).toBe('done');
  });
});
