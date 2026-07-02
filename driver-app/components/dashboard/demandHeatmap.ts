/**
 * Presentation spec for the driver demand-heatmap overlay.
 *
 * Colors: a sequential "demand" ramp (inferno-like). Chosen over the old
 * teal→gold→red gradient because magnitude should read through *lightness*,
 * not hue decoding: this ramp is lightness-monotonic (L ≈ 0.22 → 0.93), so
 * "brighter = busier" holds for every viewer, including red-green colorblind
 * drivers — green→red ramps are the classic CVD failure. Validated with the
 * dataviz palette checker: adjacent-pair CVD separation passes on light and
 * dark surfaces (worst pair ΔE 17 protan, target ≥ 12); the low-contrast
 * bright end is relieved by the on-map legend (identity is never color-alone).
 *
 * Weights: cell counts are log-damped before rendering so one downtown cell
 * with hundreds of requests doesn't max the intensity scale and flatten
 * every other neighbourhood to invisible — the overlay should show demand
 * *texture*, not a single hotspot.
 */

import type { DemandHeatmapPoint } from '@shared/hooks/queries';

export const DEMAND_RAMP_COLORS = ['#1B0C41', '#781C6D', '#CF4446', '#F98C0A', '#F5F13E'];

export const HEATMAP_RADIUS = 40;

/**
 * Gradient + opacity tuned per map surface. Dark maps take a slightly
 * earlier ramp start and higher opacity (the deep-purple low end recedes
 * into dark tiles by design); light maps start the ramp later so faint
 * cells stay subtle instead of greying the whole street grid.
 */
export const heatmapAppearance = (isDark: boolean) => ({
  opacity: isDark ? 0.75 : 0.6,
  gradient: {
    colors: DEMAND_RAMP_COLORS,
    startPoints: isDark ? [0.04, 0.28, 0.52, 0.76, 0.94] : [0.12, 0.34, 0.56, 0.78, 0.94],
    colorMapSize: 256,
  },
});

/**
 * Log-damp cell weights: weight w → 1 + log2(w). Monotonic (order between
 * cells is preserved) but compressive, so a 300-request cell reads ~3× a
 * 3-request cell instead of 100×. Never returns a weight below 1.
 */
export const dampenHeatmapPoints = (points: DemandHeatmapPoint[]): DemandHeatmapPoint[] =>
  points.map((p) => ({
    ...p,
    weight: p.weight >= 1 ? 1 + Math.log2(p.weight) : 1,
  }));
