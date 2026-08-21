/**
 * THE single source of truth for how a ride route is drawn on every surface
 * (rider-app, driver-app, admin-dashboard, backend Static-Maps PNG).
 *
 * Uniform rule — the same on every map, live or completed:
 *   • Draw the REAL route geometry (the actual driven / road path).
 *   • Colour it as ONE orange → red gradient along the path (orange at the
 *     start/pickup end, red at the destination end).
 *   • Markers: green pickup (dot), red dropoff (square), amber completion
 *     (check) — one disc style, one size scale, centre-anchored, glyphs drawn
 *     as plain shapes so no surface depends on an icon font. See
 *     ROUTE_PIN_SPEC / routePinSvg below.
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

/**
 * ─── THE marker, spelled out ─────────────────────────────────────────────────
 *
 * One coloured disc, a white ring, and a white glyph drawn as PLAIN SHAPES.
 * Every renderer we have — react-native-maps, the Android Auto surface,
 * MapLibre in the admin dashboard, raw SVG on the public tracking page, and the
 * Static-Maps PNG — can draw a circle and a rectangle. None of them can be
 * trusted to draw an icon FONT: the head-unit surface is a React root inside a
 * Presentation on a VirtualDisplay, and @expo/vector-icons glyphs have been
 * observed there as empty boxes, which is why the car screen showed a bare red
 * dot where every other surface showed a flag.
 *
 * Shapes, not letters: a letter needs a font too, and 'P'/'D' read as debug
 * chrome on a customer-facing map.
 *
 *   pickup      green disc + white DOT      "you get in here"
 *   dropoff     red disc   + white SQUARE   "the trip ends here"
 *   completion  amber disc + white CHECK    "this is where it actually ended"
 *
 * Anchored at the CENTRE on every surface — the disc marks the point itself,
 * so nothing has to agree about where a pin's tip is.
 */
export type RoutePinKind = 'pickup' | 'dropoff' | 'completion';

/** Glyph geometry as a fraction of the marker's diameter. */
export const ROUTE_PIN_GEOMETRY = {
  /** White outline around the disc, in px, at ROUTE_MARKER_SIZE. */
  ringWidth: 2,
  /** Pickup dot diameter. */
  dotRatio: 0.34,
  /** Drop-off square side. */
  squareRatio: 0.3,
  /** Completion check: arm lengths + stroke, all as a fraction of the disc. */
  checkShortRatio: 0.2,
  checkLongRatio: 0.36,
  checkStrokeRatio: 0.11,
} as const;

/** The glyph each kind draws, and the disc colour it sits on. */
export const ROUTE_PIN_SPEC: Record<RoutePinKind, { color: string; glyph: 'dot' | 'square' | 'check' }> = {
  pickup: { color: ROUTE_PIN_COLORS.pickup, glyph: 'dot' },
  dropoff: { color: ROUTE_PIN_COLORS.dropoff, glyph: 'square' },
  completion: { color: ROUTE_PIN_COLORS.completion, glyph: 'check' },
};

/**
 * The marker as one self-contained SVG string, sized to `size` px.
 *
 * For every surface that draws into the DOM or into an <img>: the admin
 * dashboard's MapLibre markers and the public tracking page both build DOM
 * elements, and this keeps them from re-deriving the geometry (which is how
 * they drifted to plain 16px and 20px circles with no glyph at all).
 */
export function routePinSvg(kind: RoutePinKind, size: number = ROUTE_MARKER_SIZE): string {
  const { color, glyph } = ROUTE_PIN_SPEC[kind];
  const g = ROUTE_PIN_GEOMETRY;
  const c = size / 2;
  // Scale the ring with the marker so a 20px admin pin isn't mostly outline.
  const ring = Math.max(1, (g.ringWidth * size) / ROUTE_MARKER_SIZE);
  const r = c - ring / 2;
  let inner = '';
  if (glyph === 'dot') {
    inner = `<circle cx="${c}" cy="${c}" r="${(size * g.dotRatio) / 2}" fill="#FFFFFF"/>`;
  } else if (glyph === 'square') {
    const side = size * g.squareRatio;
    inner = `<rect x="${c - side / 2}" y="${c - side / 2}" width="${side}" height="${side}" rx="${side * 0.18}" fill="#FFFFFF"/>`;
  } else {
    const sw = size * g.checkStrokeRatio;
    const short = size * g.checkShortRatio;
    const long = size * g.checkLongRatio;
    // Two-segment tick, drawn as a polyline so no font is involved.
    inner =
      `<polyline points="${c - short},${c} ${c - short / 3},${c + short * 0.7} ${c - short / 3 + long * 0.72},${c - long * 0.6}" ` +
      `fill="none" stroke="#FFFFFF" stroke-width="${sw}" stroke-linecap="round" stroke-linejoin="round"/>`;
  }
  return (
    `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 ${size} ${size}">` +
    `<circle cx="${c}" cy="${c}" r="${r}" fill="${color}" stroke="#FFFFFF" stroke-width="${ring}"/>` +
    inner +
    `</svg>`
  );
}

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
