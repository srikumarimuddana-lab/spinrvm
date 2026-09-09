/**
 * Color math shared between every demand-heatmap renderer (the iOS
 * layered-Circle fallback in components/dashboard/HeatmapCells.tsx and the
 * Skia raster-gradient overlay, HeatmapGradientOverlay.tsx) so they can never
 * disagree about what shade a given weight ratio should be.
 */

export function hexToRgb(hex: string): [number, number, number] {
  return [parseInt(hex.slice(1, 3), 16), parseInt(hex.slice(3, 5), 16), parseInt(hex.slice(5, 7), 16)];
}

/**
 * Continuous interpolation across a 5-step brand ramp instead of snapping to
 * one of 5 discrete buckets — design feedback 2026-09-09 (referencing Uber's
 * driver-app demand heatmap): discrete bucketed colors read as visibly
 * banded/blocky, not the soft continuous gradient a blurred heatmap gives.
 * Ramp entries sit at even positions along [0,1], matching the native
 * `<Heatmap>` gradient's own `startPoints` spacing, so every renderer agrees
 * on what a given shade means. `ratio` is clamped so an out-of-range weight
 * can't index past the ramp array.
 */
export function rampColorForRatio(ratio: number, ramp: readonly string[]): [number, number, number] {
  const clamped = Math.max(0, Math.min(1, ratio));
  const steps = ramp.length - 1;
  const pos = clamped * steps;
  const i = Math.min(steps - 1, Math.floor(pos));
  const t = pos - i;
  const [r1, g1, b1] = hexToRgb(ramp[i]);
  const [r2, g2, b2] = hexToRgb(ramp[i + 1]);
  return [r1 + (r2 - r1) * t, g1 + (g2 - g1) * t, b1 + (b2 - b1) * t];
}

export function rgbaString([r, g, b]: readonly [number, number, number], alpha: number): string {
  return `rgba(${Math.round(r)},${Math.round(g)},${Math.round(b)},${alpha})`;
}
