import { useCallback, useEffect, useState } from 'react';

export type SplashPhase = 'intro' | 'exit' | 'done';

/** Beat between the first real route mounting and the splash starting to fade. */
export const SPLASH_EXIT_SETTLE_MS = 250;
/**
 * Hard cap on waiting for that route. app/index.tsx routes on network-dependent
 * state (active ride, consent), so if it stalls we fade anyway rather than hold
 * the splash open indefinitely.
 */
export const SPLASH_EXIT_ROUTE_WAIT_MS = 1500;

type Args = {
  /** True once fonts/auth/location are ready and the navigator is mounted. */
  navReady: boolean;
  /** Current route. '/' is the transparent routing gate, not a real screen. */
  pathname: string;
};

/**
 * Drives the splash's exit.
 *
 * Fading the moment `navReady` flips would cross-fade onto app/index.tsx, which
 * is a blank view that then replaces itself with /login (or /(tabs)) — the
 * rider would see the splash dissolve into nothing and the real screen slide in
 * after it. So we wait for a route that isn't the gate, then a short settle, and
 * cap the wait so a slow route can never strand the splash.
 */
export function useSplashPhase({ navReady, pathname }: Args) {
  const [phase, setPhase] = useState<SplashPhase>('intro');

  useEffect(() => {
    if (!navReady || phase !== 'intro') return;
    const delay = pathname === '/' ? SPLASH_EXIT_ROUTE_WAIT_MS : SPLASH_EXIT_SETTLE_MS;
    const timer = setTimeout(() => setPhase('exit'), delay);
    // A pathname change ('/' -> '/login') cancels the cap and restarts on the
    // short settle instead.
    return () => clearTimeout(timer);
  }, [navReady, pathname, phase]);

  const onExitComplete = useCallback(() => {
    setPhase(current => (current === 'exit' ? 'done' : current));
  }, []);

  return { phase, onExitComplete };
}
