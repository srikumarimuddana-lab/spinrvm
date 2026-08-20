"use client";

// One categorical palette for every Analytics chart, so the four marketplace
// tabs read as a single system rather than four independent charts.
//
// Derived from the Spinr brand tokens per .claude/context/brand-spinr.md
// ("If a task needs colors beyond what's listed here (e.g. a data-viz
// categorical palette), derive them from this palette rather than picking an
// unrelated one") — info / success / warning / violet / danger.
//
// Dark mode uses SELECTED deeper steps, not a flip of the light set: the
// light steps for success (#10B981, L 0.696) and warning (#F59E0B, L 0.769)
// fall outside the acceptable lightness band against the #1C1C1E dark
// surface. Both sets were checked with the dataviz validator:
//
//   light (surface #FFFFFF): lightness PASS, chroma PASS, CVD separation
//     PASS (worst adjacent amber↔emerald ΔE 8.9 protan / 8.4 tritan),
//     normal-vision PASS (ΔE 23.8), contrast WARN on #10B981 (2.47:1) and
//     #F59E0B (2.09:1).
//   dark  (surface #1C1C1E): all six checks PASS.
//
// The light-mode contrast WARN is not dismissable — it obligates relief.
// Every chart using this palette therefore carries either direct value
// labels or an adjacent numeric readout, never colour as the only cue, and
// every multi-series chart also carries a legend.
//
// Hues are assigned in FIXED ORDER and never cycled. A sixth series folds
// into "Other" or becomes a separate chart — it never gets a generated hue.

export const CHART_PALETTE_LIGHT = [
    "#3B82F6", // info blue
    "#10B981", // success emerald
    "#F59E0B", // warning amber
    "#8B5CF6", // violet
    "#EF4444", // danger red
] as const;

export const CHART_PALETTE_DARK = [
    "#3B82F6", // info blue
    "#059669", // emerald, deeper step for the dark surface
    "#D97706", // amber, deeper step for the dark surface
    "#8B5CF6", // violet
    "#EF4444", // danger red
] as const;

/** Semantic slots, so a series keeps its colour when a filter changes the
 *  series count — colour follows the entity, never its rank. */
export interface ChartColors {
    neutral: string;
    good: string;
    warn: string;
    accent: string;
    bad: string;
    /** Ordered categorical ramp, for charts without semantic meaning. */
    series: readonly string[];
    /** Recharts axis/grid/tooltip chrome, from the theme's own CSS vars. */
    grid: string;
    axis: string;
    tooltip: { fontSize: number; borderRadius: number; border: string; background: string; color: string };
}

export function chartColors(isDark: boolean): ChartColors {
    const p = isDark ? CHART_PALETTE_DARK : CHART_PALETTE_LIGHT;
    return {
        neutral: p[0],
        good: p[1],
        warn: p[2],
        accent: p[3],
        bad: p[4],
        series: p,
        grid: "hsl(var(--border))",
        axis: "hsl(var(--muted-foreground))",
        tooltip: {
            fontSize: 12,
            borderRadius: 10,
            border: "1px solid hsl(var(--border))",
            background: "hsl(var(--card))",
            color: "hsl(var(--foreground))",
        },
    };
}
