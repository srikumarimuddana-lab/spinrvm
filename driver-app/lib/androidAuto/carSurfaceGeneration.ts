/**
 * A counter the car surface keys its map on, so the surface can be remounted
 * from outside React.
 *
 * Why this exists: on a car-only cold launch the MapTemplate can be created
 * before the JS bundle's assets are resolvable — Android Auto starts the app
 * itself after a force close, and `register.ts` builds the template as soon as a
 * connection appears. The map then comes up empty and the button icons render as
 * broken-image placeholders. Until now the only cure was for the driver to
 * unplug and replug the phone, which is not something to ask of anyone.
 *
 * `register.ts` bumps this shortly after connecting; `carSurface.tsx` includes it
 * in the MapView `key`, so the map remounts once with everything loaded. When the
 * first build was already fine the remount is invisible.
 *
 * Same channel pattern as carMapCamera.ts and carDebug.ts: the surface is
 * non-interactive and register.ts lives outside the component tree, so a store is
 * the only way for one to reach the other.
 */
import { create } from 'zustand';

interface CarSurfaceGenerationState {
  generation: number;
  /**
   * Whether the CURRENT generation's native map has attached (its onMapReady
   * fired). Cleared by every bump, since a bump is a fresh native map.
   *
   * Why it is tracked: the post-connect self-heal in register.ts used to
   * remount unconditionally at +1.2 s and +4 s. A remount of a map that is
   * already good is not invisible on Android — each one is a brand-new Google
   * Maps GL view inside the VirtualDisplay Presentation (tile cache, GL
   * context, camera reset) on a process that also runs the phone map. The
   * 2026-09-11 test ride hit a 256 MB heap OOM 59 s after a relaunch
   * (CRIMSON-SMOKE-7445-SX) with Android Auto connected, and the head unit
   * showed Google's default world camera after a remount whose onMapReady
   * never re-fired. Remounting only a map that has not come up keeps the
   * cold-launch cure and drops the churn.
   */
  mapReady: boolean;
  bump: () => void;
  markMapReady: () => void;
}

export const useCarSurfaceGeneration = create<CarSurfaceGenerationState>((set) => ({
  generation: 0,
  mapReady: false,
  bump: () => set((s) => ({ generation: s.generation + 1, mapReady: false })),
  markMapReady: () => set({ mapReady: true }),
}));

/** Remount the car surface's map. Safe to call from outside React. */
export const bumpCarSurfaceGeneration = (): void => {
  try {
    useCarSurfaceGeneration.getState().bump();
  } catch {
    // Never let a recovery mechanism be the thing that breaks the surface.
  }
};

/** True once the current map instance has attached. Safe outside React. */
export const isCarSurfaceMapReady = (): boolean => {
  try {
    return useCarSurfaceGeneration.getState().mapReady;
  } catch {
    return false;
  }
};
