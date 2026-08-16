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

/**
 * How far one unit of pan translation moves the camera, as a fraction of the
 * visible span.
 *
 * iternio hands `onDidPan` a translation already divided by the surface scale
 * (VirtualRenderer.kt's onScroll), but not the surface dimensions — so mapping
 * it to degrees exactly would need a width we do not have here. This constant
 * approximates "a full-width drag pans about one screen" for a typical head
 * unit. It is a feel constant, not a measurement: if panning is too fast or too
 * slow on real hardware, tune this one number. The debug panel reports the live
 * offset so it can be tuned by observation.
 */
const PAN_UNITS_PER_SCREEN = 700;

interface CarMapCameraState {
  /** Current map span (latitude/longitude delta in degrees). */
  delta: number;
  /**
   * Manual pan away from the driver, in degrees. Zero on both axes means the
   * camera is following the driver; anything else means the driver has dragged
   * the map and we must STOP following, or the next GPS fix would yank the view
   * back mid-drag.
   */
  offsetLat: number;
  offsetLng: number;
  zoomIn: () => void;
  zoomOut: () => void;
  /** Apply a pan translation from the head unit. */
  pan: (translationX: number, translationY: number) => void;
  /** Clear the pan and resume following the driver. Leaves zoom alone — the
   *  driver chose that deliberately; only the position was incidental. */
  recenter: () => void;
  /** Reset to the default span — called on (re)connect so each session starts framed. */
  reset: () => void;
}

export const useCarMapCamera = create<CarMapCameraState>((set) => ({
  delta: DEFAULT_DELTA,
  offsetLat: 0,
  offsetLng: 0,
  zoomIn: () => set((s) => ({ delta: zoomInDelta(s.delta) })),
  zoomOut: () => set((s) => ({ delta: zoomOutDelta(s.delta) })),
  pan: (translationX, translationY) =>
    set((s) => {
      const scale = s.delta / PAN_UNITS_PER_SCREEN;
      return {
        // Dragging the map right moves the viewport LEFT (west), hence the
        // sign flip on X; screen Y grows downward while latitude grows north,
        // so Y flips too.
        offsetLng: s.offsetLng - translationX * scale,
        offsetLat: s.offsetLat + translationY * scale,
      };
    }),
  recenter: () => set({ offsetLat: 0, offsetLng: 0 }),
  reset: () => set({ delta: DEFAULT_DELTA, offsetLat: 0, offsetLng: 0 }),
}));

/** True when the driver has panned away from their own position. */
export const isPanned = (s: Pick<CarMapCameraState, 'offsetLat' | 'offsetLng'>): boolean =>
  s.offsetLat !== 0 || s.offsetLng !== 0;
