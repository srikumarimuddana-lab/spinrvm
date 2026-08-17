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
  bump: () => void;
}

export const useCarSurfaceGeneration = create<CarSurfaceGenerationState>((set) => ({
  generation: 0,
  bump: () => set((s) => ({ generation: s.generation + 1 })),
}));

/** Remount the car surface's map. Safe to call from outside React. */
export const bumpCarSurfaceGeneration = (): void => {
  try {
    useCarSurfaceGeneration.getState().bump();
  } catch {
    // Never let a recovery mechanism be the thing that breaks the surface.
  }
};
