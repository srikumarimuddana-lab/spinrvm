// Single source of truth for demand/supply-ratio presentation across the
// admin dashboard (monitoring map overlay + heatmap Unmet Demand cards).
//
// Why this file exists
// --------------------
// These thresholds and colours were hand-copied into four separate places,
// which drifted immediately: the "oversupply" band alone rendered in three
// different purples (map fill, map legend swatch, card text) that did not
// match each other or the brand --chart-* tokens. Worse, the band labels
// suffixed ratios with "x" — the notation this codebase reserves for the
// surge MULTIPLIER — so a legend read "Critical (>=3.0x)", naming a fare
// multiplier that cannot legally exist (the cap is 2.5x).
//
// Band boundaries mirror backend/utils/surge_engine.py's SURGE_TIERS exactly,
// including the 0.8 split that a previous merged "Balanced (0.5-1.2)" band
// hid — that range spans two live surge tiers (1.25x and 1.5x), so calling it
// "balanced" told an operator nothing was happening while riders were already
// paying up to 50% more.

export interface DemandBand {
  /** Inclusive lower bound on demand/supply ratio. */
  min: number;
  /** Stable key for tests and data attributes. */
  key: "critical" | "high" | "elevated" | "building" | "balanced" | "oversupply";
  /** Human label. Deliberately carries no "x" suffix — this is a ratio. */
  label: string;
  /** Fill/text colour. Sourced from the brand --chart-* ramp. */
  color: string;
  /** Tailwind text colour class for card figures. */
  textClass: string;
  /** The surge multiplier this ratio produces, for operator context. */
  multiplier: number;
}

/* eslint-disable no-restricted-syntax -- deliberate 6-tier demand/supply-ratio
   heat gradient mirroring backend/utils/surge_engine.py's SURGE_TIERS, not a
   success/warning/destructive signal — too many states (and too gradient-like)
   for the 3-token system, see comment above (#2816) */
/** Ordered high-to-low; `bandForRatio` returns the first match. */
export const DEMAND_BANDS: DemandBand[] = [
  { min: 3.0, key: "critical", label: "Critical", color: "#ff3b30", textClass: "text-[#ff3b30]", multiplier: 2.5 },
  { min: 2.0, key: "high", label: "High", color: "#f97316", textClass: "text-orange-700 dark:text-orange-400", multiplier: 2.0 },
  { min: 1.2, key: "elevated", label: "Elevated", color: "#d97706", textClass: "text-amber-700 dark:text-amber-400", multiplier: 1.75 },
  { min: 0.8, key: "building", label: "Building", color: "#ca8a04", textClass: "text-yellow-700 dark:text-yellow-400", multiplier: 1.5 },
  { min: 0.5, key: "balanced", label: "Balanced", color: "#34c759", textClass: "text-green-700 dark:text-green-400", multiplier: 1.25 },
  { min: -Infinity, key: "oversupply", label: "Oversupply", color: "#7c3aed", textClass: "text-[#7c3aed] dark:text-violet-400", multiplier: 1.0 },
];
/* eslint-enable no-restricted-syntax */

/** Neutral treatment for an area with no data at all (see `isDormant`). */
export const NO_DATA_COLOR = "#9ca3af";

export function bandForRatio(ratio: number): DemandBand {
  const r = Number.isFinite(ratio) ? ratio : 0;
  return DEMAND_BANDS.find((b) => r >= b.min) ?? DEMAND_BANDS[DEMAND_BANDS.length - 1];
}

/**
 * Range text for a band, e.g. "2.0-3.0" or ">= 3.0".
 * Never suffixed with "x" — see the file header.
 */
export function bandRangeLabel(band: DemandBand): string {
  const idx = DEMAND_BANDS.findIndex((b) => b.key === band.key);
  if (idx === 0) return `≥ ${band.min.toFixed(1)}`;
  if (band.min === -Infinity) return `< ${DEMAND_BANDS[idx - 1].min.toFixed(1)}`;
  return `${band.min.toFixed(1)}–${DEMAND_BANDS[idx - 1].min.toFixed(1)}`;
}

/**
 * An area with neither demand nor supply is not "oversupplied" — it is
 * inactive. Colouring 0/0 the same as a genuinely quiet-but-staffed area (and
 * rendering it as a full green "healthy" bar, as an earlier version did) hides
 * dead or misconfigured areas from exactly the screen meant to surface them.
 */
export function isDormant(demand: number, supply: number): boolean {
  return (demand ?? 0) === 0 && (supply ?? 0) === 0;
}

/** Map fill colour for a service-area polygon. */
export function demandFillColor(demand: number, supply: number, ratio: number): string {
  if (isDormant(demand, supply)) return NO_DATA_COLOR;
  return bandForRatio(ratio).color;
}

/** Fill opacity scaled by pressure, clamped so polygons never obscure the map. */
export function demandFillOpacity(demand: number, supply: number, ratio: number): number {
  if (isDormant(demand, supply)) return 0.06;
  const r = Number.isFinite(ratio) ? ratio : 0;
  return Math.min(0.35, Math.max(0.08, 0.1 + r * 0.06));
}

/**
 * Green/red split for the per-area demand bar.
 *
 * Returns percentages that never exceed 100 combined and are never NaN. A
 * dormant area returns zero width for both so the caller can render a neutral
 * "no activity" track instead of a misleading full-green "healthy" bar.
 */
export function demandBarWidths(demand: number, supply: number): { supplyPct: number; gapPct: number } {
  const d = Math.max(0, demand ?? 0);
  const s = Math.max(0, supply ?? 0);
  if (isDormant(d, s)) return { supplyPct: 0, gapPct: 0 };
  const total = Math.max(d, s);
  if (total <= 0) return { supplyPct: 0, gapPct: 0 };
  const gap = Math.max(0, d - s);
  return {
    supplyPct: Math.min(100, (Math.min(s, total) / total) * 100),
    gapPct: Math.min(100, (gap / total) * 100),
  };
}

/**
 * Demand pressure = active demand minus idle supply.
 *
 * NOT a count of stranded riders: the backend's `demand_count` includes rides
 * that already have a driver assigned, en route, or in progress, while
 * `supply_count` counts only idle drivers. A fully-served rush hour therefore
 * produces a large positive number. Labelled accordingly everywhere it is
 * shown — an earlier "N unfulfilled requests" caption overstated a healthy
 * market by several times and invited unnecessary manual surge overrides.
 */
export function demandPressure(demand: number, supply: number): number {
  return Math.max(0, (demand ?? 0) - (supply ?? 0));
}
