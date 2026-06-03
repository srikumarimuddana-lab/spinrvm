/**
 * Car-map camera (zoom) state, shared between the head-unit map buttons and the
 * rendered surface.
 *
 * The projected Android Auto surface is non-interactive (`pointerEvents="none"`
 * in carSurface.tsx) — Android Auto routes all interaction through template map
 * buttons, never in-surface pinch/drag. So zoom can only be driven by the
 * MapTemplate's map buttons (register.ts), whose `onPress` callbacks live outside
 * the React surface component. This tiny store is the channel between them:
 *   - register.ts buttons → `useCarMapCamera.getState().zoomIn()/zoomOut()`
 *   - carSurface.tsx       → subscribes to `delta` and feeds it to the MapView
 *
 * `delta` is a react-native-maps `latitudeDelta`/`longitudeDelta` (degrees of the
 * visible span): smaller = more zoomed in. The pure helpers are exported so the
 * zoom math is unit-tested without zustand or a head unit.
 */
import { create } from 'zustand';

/** Closest zoom — ~street level. */
export const MIN_DELTA = 0.004;
/** Farthest zoom — ~city level. Keeps the driver's dot from getting lost. */
export const MAX_DELTA = 0.4;
/** Default span on connect — ~neighborhood; comfortable follow-me framing. */
export const DEFAULT_DELTA = 0.02;
/** Multiplier per zoom step. 1.5 ≈ a noticeable but not jarring jump. */
const ZOOM_FACTOR = 1.5;

/** Keep the span within [MIN_DELTA, MAX_DELTA] so zoom never runs away. */
export const clampDelta = (delta: number): number => {
  if (!Number.isFinite(delta)) return DEFAULT_DELTA;
  return Math.min(MAX_DELTA, Math.max(MIN_DELTA, delta));
};

/** Zoom in one step (smaller span), clamped at MIN_DELTA. */
export const zoomInDelta = (delta: number): number => clampDelta(delta / ZOOM_FACTOR);

/** Zoom out one step (larger span), clamped at MAX_DELTA. */
export const zoomOutDelta = (delta: number): number => clampDelta(delta * ZOOM_FACTOR);

interface CarMapCameraState {
  /** Current map span (latitude/longitude delta in degrees). */
  delta: number;
  zoomIn: () => void;
  zoomOut: () => void;
  /** Reset to the default span — called on (re)connect so each session starts framed. */
  reset: () => void;
}

export const useCarMapCamera = create<CarMapCameraState>((set) => ({
  delta: DEFAULT_DELTA,
  zoomIn: () => set((s) => ({ delta: zoomInDelta(s.delta) })),
  zoomOut: () => set((s) => ({ delta: zoomOutDelta(s.delta) })),
  reset: () => set({ delta: DEFAULT_DELTA }),
}));
