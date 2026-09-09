/**
 * Radial falloff shared by all three demand-heat renderers.
 *
 * Android draws a real native <Heatmap> (a true density gradient). iOS runs
 * Apple Maps, where that component silently no-ops, so it stacks translucent
 * <Circle>s instead; Android Auto did the same with hard-edged squares. The
 * three therefore disagreed about what "busy" looks like even though they
 * share one colour ramp.
 *
 * The old iOS stand-in drew two circles per cell at fixed alphas (0.14 outer,
 * 0.32/0.5 inner). Stacked, that steps from 0.14 straight to ~0.57 at half the
 * radius — a visible hard edge, which is the bullseye look reported on iOS.
 *
 * This module keeps the ramp untouched and fixes only the shape: N nested
 * circles whose *stacked* opacity follows a Gaussian, so the step between
 * adjacent bands stays small and the blob reads as a fade rather than a target.
 *
 * Nothing here imports react-native, so it is pure and unit-testable.
 */

/** Ring boundaries as a fraction of the blob's outer radius, outermost first. */
export const HEAT_RING_STOPS = [1, 0.75, 0.5, 0.25] as const;

/**
 * Notional Gaussian peak at r=0. The innermost ring is drawn at 0.25R rather
 * than 0, so the alpha actually painted at the centre is lower than this —
 * use `paintedPeakAlpha()` when you need the real number.
 */
export const HEAT_PEAK_ALPHA = 0.65;

/** Gaussian sigma, as a fraction of the outer radius. */
const SIGMA = 0.45;

/**
 * Blob outer radius as a fraction of one grid cell's latitude span. Cell
 * centres sit one full span apart, so 0.7 overlaps neighbours by ~40% of the
 * spacing — enough to close the gaps between cells without smearing distinct
 * zones into one mass. (The old value was 0.62, which left visible seams.)
 */
export const HEAT_BLOB_RADIUS_FACTOR = 0.7;

/**
 * Ships dark. The soft path changes how an already-shipped driver-facing map
 * looks, and nothing in this repo can screenshot Apple Maps, Google Maps or an
 * Android Auto head unit — so it stays off until someone captures native
 * evidence on all three. Flip to true in its own commit + OTA.
 *
 * This is a build-time constant, not a remote kill switch: the renderer is
 * entirely client-side, so a remote toggle would mean a new app_settings
 * column plumbed through the heatmap endpoint. That is a reasonable follow-up
 * but is not needed to ship this dark.
 */
export const SOFT_HEAT_RENDER_ENABLED = false;

/**
 * Per-ring alpha for one cell, outermost first.
 *
 * Painter's algorithm: stacking circles largest-first gives a cumulative alpha
 * of `1 - Π(1 - αᵢ)`. Drawing each ring *at* its Gaussian target would compound
 * into something far more opaque than intended, so invert that relation and
 * solve for the per-ring value that lands the STACK on the target:
 *
 *     αₖ = (Tₖ - Tₖ₋₁) / (1 - Tₖ₋₁)
 *
 * @param intensity 0..1, the cell's weight relative to the strongest cell.
 */
export function ringAlphas(intensity: number): number[] {
  // NaN/Infinity must not reach a native shape: Math.min/max propagate NaN
  // rather than clamping it, and react-native-maps hands alpha straight to the
  // platform SDK. Treat a non-finite intensity as "no heat" instead.
  const safe = Number.isFinite(intensity) ? intensity : 0;
  const scale = Math.max(0, Math.min(1, safe)) * HEAT_PEAK_ALPHA;
  const out: number[] = [];
  let prev = 0; // cumulative target already reached by the wider rings
  for (const f of HEAT_RING_STOPS) {
    const target = scale * Math.exp(-(f * f) / (2 * SIGMA * SIGMA));
    // Guard the degenerate prev>=1 case so this can never divide by zero.
    const alpha = prev >= 1 ? 0 : (target - prev) / (1 - prev);
    out.push(Math.max(0, Math.min(1, alpha)));
    prev = target;
  }
  return out;
}

/**
 * Alpha actually painted at the centre of a full-intensity cell.
 *
 * Android's native <Heatmap> takes a single layer opacity instead of a ring
 * stack, so it uses this to land on the same peak translucency the iOS/Auto
 * stacks reach. It is a first-order match on peak opacity, not a colorimetric
 * one — the native gradient still maps colour its own way.
 */
export function paintedPeakAlpha(): number {
  return ringAlphas(1).reduce((acc, a) => acc + a * (1 - acc), 0);
}
