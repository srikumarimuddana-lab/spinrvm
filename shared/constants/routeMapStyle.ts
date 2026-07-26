/**
 * THE single source of truth for how a ride route is drawn on EVERY surface
 * (rider-app, driver-app, admin-dashboard, and the backend Static-Maps PNG).
 *
 * Uniform rule — the same on every map, live or completed, planned or actual:
 *   • ONE straight line from pickup → destination.
 *   • Coloured as an orange → red gradient (orange at pickup, red at dropoff).
 *   • No per-segment / gap / planned-vs-actual styling. GPS reconstruction still
 *     feeds distance + the regulatory audit; it no longer changes the picture.
 *   • Markers: green pickup, red dropoff, amber completion — one style/size.
 *
 * Do NOT restyle a route inline in any screen. Two maps drawing the same ride
 * differently is exactly the inconsistency this module exists to remove. Every
 * renderer derives its colours from the constants + helpers below.
 */

/** Gradient endpoints for the straight pickup→destination line. */
export const ROUTE_GRADIENT_START = '#FF9500'; // orange — at the pickup end
export const ROUTE_GRADIENT_END = '#EE2B2B'; // red — at the destination end
export const ROUTE_STROKE_WIDTH = 4;
/** Number of interpolated segments used to fake a gradient on engines without
 *  native gradient strokes (react-native-maps, MapLibre, Google, PNG). */
export const ROUTE_GRADIENT_SEGMENTS = 24;

/** Marker pin fills + one shared size — green pickup / red dropoff / amber completion. */
export const ROUTE_PIN_COLORS = {
  pickup: '#10B981',
  dropoff: '#EF4444',
  completion: '#F59E0B',
} as const;
export const ROUTE_MARKER_SIZE = 30;

/** A [lat, lng] point. */
export type LatLng = [number, number];

const _hex = (n: number) => Math.max(0, Math.min(255, Math.round(n))).toString(16).padStart(2, '0');

function _parse(hex: string): [number, number, number] {
  const h = hex.replace('#', '');
  return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)];
}

/** Colour on the orange→red gradient at position t∈[0,1] (0 = pickup, 1 = dropoff). */
export function routeGradientColorAt(t: number): string {
  const clamped = Number.isFinite(t) ? Math.max(0, Math.min(1, t)) : 0;
  const [r1, g1, b1] = _parse(ROUTE_GRADIENT_START);
  const [r2, g2, b2] = _parse(ROUTE_GRADIENT_END);
  return `#${_hex(r1 + (r2 - r1) * clamped)}${_hex(g1 + (g2 - g1) * clamped)}${_hex(b1 + (b2 - b1) * clamped)}`;
}

export interface RouteGradientSegment {
  /** Two [lat, lng] endpoints of this sub-segment. */
  coordinates: [LatLng, LatLng];
  /** Interpolated orange→red colour for this sub-segment. */
  color: string;
}

/**
 * Break the straight pickup→destination line into `count` coloured sub-segments
 * so any map engine can render the orange→red gradient. Engine-agnostic: returns
 * [lat, lng] pairs; a GeoJSON caller swaps to [lng, lat] itself.
 *
 * Returns [] when either endpoint is missing or the two points coincide.
 */
export function buildStraightRouteGradient(
  pickup: LatLng | null | undefined,
  destination: LatLng | null | undefined,
  count: number = ROUTE_GRADIENT_SEGMENTS,
): RouteGradientSegment[] {
  if (!pickup || !destination) return [];
  const [plat, plng] = pickup;
  const [dlat, dlng] = destination;
  if (![plat, plng, dlat, dlng].every((n) => Number.isFinite(n))) return [];
  if (plat === dlat && plng === dlng) return [];
  const n = Math.max(1, Math.floor(count));
  const segs: RouteGradientSegment[] = [];
  for (let i = 0; i < n; i++) {
    const t0 = i / n;
    const t1 = (i + 1) / n;
    segs.push({
      coordinates: [
        [plat + (dlat - plat) * t0, plng + (dlng - plng) * t0],
        [plat + (dlat - plat) * t1, plng + (dlng - plng) * t1],
      ],
      // Colour at the segment midpoint so the run reads as a smooth blend.
      color: routeGradientColorAt((t0 + t1) / 2),
    });
  }
  return segs;
}

/**
 * Back-compat aliases. The route is now a single orange→red gradient line, so
 * the old provenance-specific strokes all collapse to the unified endpoints.
 * Retained so any un-migrated import keeps rendering the uniform colours rather
 * than breaking the build. Prefer buildStraightRouteGradient() in new code.
 * @deprecated use the gradient helpers above.
 */
export const ACTUAL_ROUTE_STROKE = { strokeColor: ROUTE_GRADIENT_END, strokeWidth: ROUTE_STROKE_WIDTH } as const;
/** @deprecated the route no longer distinguishes inferred gaps visually. */
export const INFERRED_ROUTE_STROKE = { strokeColor: ROUTE_GRADIENT_START, strokeWidth: ROUTE_STROKE_WIDTH } as const;
/** @deprecated the route no longer draws a separate planned line. */
export const PLANNED_ROUTE_STROKE = { strokeColor: ROUTE_GRADIENT_START, strokeWidth: ROUTE_STROKE_WIDTH } as const;
