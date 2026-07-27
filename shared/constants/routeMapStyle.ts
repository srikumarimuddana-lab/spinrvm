/**
 * THE single source of truth for how a ride route is drawn on every surface
 * (rider-app, driver-app, admin-dashboard, backend Static-Maps PNG).
 *
 * Uniform rule — the same on every map, live or completed:
 *   • Draw the REAL route geometry (the actual driven / road path).
 *   • Colour it as ONE orange → red gradient along the path (orange at the
 *     start/pickup end, red at the destination end).
 *   • Markers: green pickup, red dropoff, amber completion — one style + size.
 *
 * Only the colours + markers are unified here; each surface supplies its own
 * real route coordinates. Do not restyle a route inline in any screen — derive
 * from the constants + helpers below so two maps never draw the same ride
 * differently.
 */

/** Gradient endpoints (orange at the start, red at the destination). */
export const ROUTE_GRADIENT_START = '#FF9500';
export const ROUTE_GRADIENT_END = '#EE2B2B';
export const ROUTE_STROKE_WIDTH = 4;
/** Chunks used to render the gradient along a real path on engines without a
 *  native gradient stroke (react-native-maps, MapLibre, Google, PNG). */
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

/** Colour on the orange→red gradient at position t∈[0,1] (0 = start, 1 = end). */
export function routeGradientColorAt(t: number): string {
  const clamped = Number.isFinite(t) ? Math.max(0, Math.min(1, t)) : 0;
  const [r1, g1, b1] = _parse(ROUTE_GRADIENT_START);
  const [r2, g2, b2] = _parse(ROUTE_GRADIENT_END);
  return `#${_hex(r1 + (r2 - r1) * clamped)}${_hex(g1 + (g2 - g1) * clamped)}${_hex(b1 + (b2 - b1) * clamped)}`;
}

export interface RouteGradientSegment {
  /** Ordered [lat, lng] points of this sub-polyline (≥2). */
  coordinates: LatLng[];
  /** Interpolated orange→red colour for this sub-polyline. */
  color: string;
}

function _valid(p: unknown): p is LatLng {
  return Array.isArray(p) && p.length === 2 && Number.isFinite(p[0]) && Number.isFinite(p[1]);
}

/**
 * Colour the REAL route path as an orange→red gradient. The path keeps ALL its
 * points (the true road shape); it is split into up to `segments` contiguous
 * chunks — adjacent chunks share an endpoint so the line stays unbroken — each
 * chunk coloured by its position along the route. Returns [] for < 2 points.
 */
export function buildPathGradient(
  path: LatLng[] | null | undefined,
  segments: number = ROUTE_GRADIENT_SEGMENTS,
): RouteGradientSegment[] {
  const pts = (path ?? []).filter(_valid);
  if (pts.length < 2) return [];
  const chunks = Math.max(1, Math.min(Math.floor(segments), pts.length - 1));
  const per = (pts.length - 1) / chunks;
  const out: RouteGradientSegment[] = [];
  for (let c = 0; c < chunks; c++) {
    const start = Math.floor(c * per);
    const end = c === chunks - 1 ? pts.length - 1 : Math.floor((c + 1) * per);
    const slice = pts.slice(start, end + 1);
    if (slice.length >= 2) out.push({ coordinates: slice, color: routeGradientColorAt((c + 0.5) / chunks) });
  }
  return out;
}

/**
 * Colour MULTIPLE route sections (v2 capture segments) as one orange→red
 * gradient WITHOUT bridging them. Each section is drawn independently — the end
 * of one capture session is never chorded to the start of the next across a GPS
 * gap — but the colour runs continuously across the whole trip by global
 * position. This preserves the backend's gap contract (sections must never be
 * concatenated) while keeping the uniform look. Returns [] when no section has
 * ≥ 2 valid points.
 */
export function buildMultiPathGradient(
  sections: (LatLng[] | null | undefined)[] | null | undefined,
  segments: number = ROUTE_GRADIENT_SEGMENTS,
): RouteGradientSegment[] {
  const clean = (sections ?? [])
    .map((s) => (s ?? []).filter(_valid))
    .filter((s) => s.length >= 2);
  if (clean.length === 0) return [];
  const totalPts = clean.reduce((a, s) => a + s.length, 0);
  const out: RouteGradientSegment[] = [];
  let seen = 0;
  for (const sec of clean) {
    const chunks = Math.max(1, Math.round((segments * sec.length) / totalPts));
    const per = (sec.length - 1) / chunks;
    for (let c = 0; c < chunks; c++) {
      const start = Math.floor(c * per);
      const end = c === chunks - 1 ? sec.length - 1 : Math.floor((c + 1) * per);
      const slice = sec.slice(start, end + 1);
      if (slice.length >= 2) {
        out.push({ coordinates: slice, color: routeGradientColorAt((seen + (start + end) / 2) / totalPts) });
      }
    }
    seen += sec.length;
  }
  return out;
}

/**
 * Straight pickup→destination gradient — the fallback when a surface has no real
 * path yet (e.g. a pre-dispatch booking preview). Same colour language as
 * buildPathGradient so the two never clash.
 */
export function buildStraightRouteGradient(
  pickup: LatLng | null | undefined,
  destination: LatLng | null | undefined,
  count: number = ROUTE_GRADIENT_SEGMENTS,
): RouteGradientSegment[] {
  if (!_valid(pickup) || !_valid(destination)) return [];
  const [plat, plng] = pickup;
  const [dlat, dlng] = destination;
  if (plat === dlat && plng === dlng) return [];
  const n = Math.max(1, Math.floor(count));
  const out: RouteGradientSegment[] = [];
  for (let i = 0; i < n; i++) {
    const t0 = i / n;
    const t1 = (i + 1) / n;
    out.push({
      coordinates: [
        [plat + (dlat - plat) * t0, plng + (dlng - plng) * t0],
        [plat + (dlat - plat) * t1, plng + (dlng - plng) * t1],
      ],
      color: routeGradientColorAt((t0 + t1) / 2),
    });
  }
  return out;
}

/**
 * Back-compat aliases. The route is now one orange→red gradient along the real
 * path; kept so any un-migrated importer still renders in the uniform palette.
 * @deprecated prefer buildPathGradient() / <RouteLine path=…/>.
 */
export const ACTUAL_ROUTE_STROKE = { strokeColor: ROUTE_GRADIENT_END, strokeWidth: ROUTE_STROKE_WIDTH } as const;
/** @deprecated gaps are no longer styled separately. */
export const INFERRED_ROUTE_STROKE = { strokeColor: ROUTE_GRADIENT_START, strokeWidth: ROUTE_STROKE_WIDTH } as const;
/** @deprecated no separate planned line. */
export const PLANNED_ROUTE_STROKE = { strokeColor: ROUTE_GRADIENT_START, strokeWidth: ROUTE_STROKE_WIDTH } as const;
