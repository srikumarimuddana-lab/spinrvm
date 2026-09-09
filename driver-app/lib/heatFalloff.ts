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
export const HEAT_RING_STOPS = [1, 0.84, 0.68, 0.52, 0.36] as const;

/**
 * Notional Gaussian peak at r=0, for ONE cell on its own.
 *
 * Deliberately low. In the reference the hot core is not one saturated cell —
 * it is many overlapping contributions summing, while the base map stays
 * readable throughout. So a single cell contributes little and density does
 * the rest; see HEAT_NATIVE_LAYER_ALPHA for what a busy area actually reaches.
 *
 * The innermost ring is drawn at 0.36R rather than 0, so the alpha actually
 * painted at a lone cell's centre is lower still — `paintedPeakAlpha()`.
 */
export const HEAT_PEAK_ALPHA = 0.4;

/** Gaussian sigma, as a fraction of the outer radius. */
const SIGMA = 0.42;

/** Metres per degree of latitude, for sizing a blob off the server's grid. */
export const METERS_PER_LAT_DEG = 111_320;

/**
 * Blob outer radius as a fraction of one grid cell's latitude span.
 *
 * This is the single lever that decides whether the layer reads as separate
 * blobs or as a continuous field. Cell centres sit one span apart, so a radius
 * below 1 leaves each cell essentially standing alone. At 1.35 every cell
 * reaches its four edge neighbours (and stops short of the diagonals at 1.41),
 * so neighbouring contributions integrate into a wash the way a real density
 * kernel does, instead of tiling visible circles.
 *
 * Trade-off, stated plainly: colour is still chosen per cell and composited,
 * whereas a true heatmap sums density FIRST and then maps one colour. Widening
 * the kernel improves the shape and makes that colour error more visible where
 * a hot cell abuts a cold one. It is tolerable only because the whole ramp
 * lives in one hue family. Fixing it properly needs the server-rendered raster
 * tiles (PR #5142 B4-B6), not a bigger radius.
 */
export const HEAT_BLOB_RADIUS_FACTOR = 1.35;

/**
 * Layer opacity for Android's native <Heatmap>.
 *
 * Android sums density internally and hands back one composited layer, so it
 * must NOT use the single-cell `paintedPeakAlpha()` — that would render Android
 * far fainter than iOS, where overlapping neighbours build the core.
 *
 * Derived from the iOS stack rather than guessed: a cell surrounded by four
 * equally busy edge neighbours composites to
 *   1 - (1 - 0.277)(1 - 0.054)^4 = 0.421
 * using the discrete ring cumulatives (not the continuous Gaussian — the rings
 * are what actually get drawn). Still only a first-order match; the two need a
 * side-by-side look on real devices before anyone calls them equal.
 */
export const HEAT_NATIVE_LAYER_ALPHA = 0.42;

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
 * Centre of the grid cell a point falls in.
 *
 * Both renderers re-derive this from the server's centroid, and they must snap
 * to the same square or the phone and the car will draw the same demand in
 * slightly different places.
 */
export function cellCenter(lat: number, lng: number, cellLat: number, cellLng: number) {
  const baseLat = Math.floor(lat / cellLat) * cellLat;
  const baseLng = Math.floor(lng / cellLng) * cellLng;
  return { latitude: baseLat + cellLat / 2, longitude: baseLng + cellLng / 2 };
}

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
