/**
 * Presentation spec for the driver demand-heatmap overlay.
 *
 * Colors: curated sequential ramps, selected per service area by the admin
 * (service_areas.heatmap_color_theme, migration 204) and delivered via the
 * demand-heatmap payload. A curated enum rather than free-form colors so an
 * operator can't configure a ramp that's illegible to red-green colorblind
 * drivers: every ramp here is lightness-monotonic ("brighter = busier"
 * holds for every viewer) and validated with the dataviz palette checker —
 * adjacent-pair CVD separation passes on light and dark surfaces (worst
 * pairs: inferno ΔE 17 protan, ocean ΔE 22.8 deutan, viridis ΔE 19.4
 * deutan; target ≥ 12). The low-contrast bright end is relieved by the
 * on-map legend (identity is never color-alone). Unknown theme names fall
 * back to the default, so a theme can be retired server-side without
 * breaking older app builds.
 *
 * Weights: cell counts are log-damped before rendering so one downtown cell
 * with hundreds of requests doesn't max the intensity scale and flatten
 * every other neighbourhood to invisible — the overlay should show demand
 * *texture*, not a single hotspot.
 */

import type { DemandHeatmapPoint, DemandHeatmapTheme } from '@shared/hooks/queries';

export const DEMAND_RAMPS: Record<DemandHeatmapTheme, string[]> = {
  // Deep violet → magenta → orange → bright yellow (default).
  inferno: ['#1B0C41', '#781C6D', '#CF4446', '#F98C0A', '#F5F13E'],
  // Deep navy → blue → cyan → pale aqua.
  ocean: ['#0A1A4F', '#1D4E9E', '#1C8FCB', '#54D2E0', '#DFF6E8'],
  // Violet → slate blue → teal → green → yellow-green.
  viridis: ['#440154', '#3B528B', '#21918C', '#5EC962', '#FDE725'],
};

const DEFAULT_THEME: DemandHeatmapTheme = 'inferno';

export const rampColors = (theme?: DemandHeatmapTheme): string[] =>
  DEMAND_RAMPS[theme ?? DEFAULT_THEME] ?? DEMAND_RAMPS[DEFAULT_THEME];

export const HEATMAP_RADIUS = 40;

/**
 * Gradient + opacity tuned per map surface. Dark maps take a slightly
 * earlier ramp start and higher opacity (the dark low end recedes into dark
 * tiles by design); light maps start the ramp later so faint cells stay
 * subtle instead of greying the whole street grid.
 */
export const heatmapAppearance = (isDark: boolean, theme?: DemandHeatmapTheme) => ({
  opacity: isDark ? 0.75 : 0.6,
  gradient: {
    colors: rampColors(theme),
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
