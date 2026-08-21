/**
 * Pure camera math for the Android Auto map — heading and zoom.
 *
 * Framework-free and side-effect-free on purpose: the surface that uses this
 * (carSurface.tsx) can only be exercised on a head unit, so the arithmetic that
 * decides where the camera points is kept out here where it can be tested on its
 * own. Same reasoning as carCard.ts.
 */

/** Below this many degrees of change, a heading update is jitter, not a turn. */
export const HEADING_COMMIT_DEG = 4;

/** Google's web-Mercator tile edge, in dp, at zoom 0. */
const TILE_SIZE_DP = 256;

/** Google Maps' usable zoom range. */
const MIN_ZOOM = 1;
const MAX_ZOOM = 20;

/**
 * A usable compass bearing in [0, 360), or null.
 *
 * expo-location reports **-1** for "heading unknown" and null when the provider
 * gives no bearing at all — and, critically, a one-shot `getCurrentPositionAsync`
 * usually has no course over ground, so it lands here as 0 or -1 rather than as
 * a real direction. Treating either as a real bearing is what points a car
 * marker due north while it drives west.
 */
export function normalizeHeading(h: number | null | undefined): number | null {
  if (typeof h !== 'number' || !Number.isFinite(h) || h < 0) return null;
  const wrapped = h % 360;
  return wrapped < 0 ? wrapped + 360 : wrapped;
}

/**
 * Smallest signed turn from `from` to `to`, in (-180, 180].
 *
 * Signed and wrapped so that 350° → 10° is +20 (a small right turn) rather than
 * -340 (nearly a full circle the wrong way). The camera would visibly spin the
 * long way round every time a driver crossed north without this.
 */
export function angularDelta(from: number, to: number): number {
  let d = (to - from) % 360;
  if (d > 180) d -= 360;
  if (d <= -180) d += 360;
  return d;
}

/**
 * Should the camera actually rotate to `next`?
 *
 * GPS course is noisy, and a map that twitches a degree at a time while the
 * driver holds a straight line is worse than one that does not rotate at all —
 * it reads as instability at exactly the moment the driver wants to trust it.
 * The first real heading always commits; after that only a genuine turn does.
 */
export function shouldCommitHeading(
  current: number | null,
  next: number | null,
  thresholdDeg: number = HEADING_COMMIT_DEG,
): boolean {
  if (next === null) return false; // never rotate on an absent bearing
  if (current === null) return true; // first fix with a course — adopt it
  return Math.abs(angularDelta(current, next)) >= thresholdDeg;
}

/**
 * Google zoom level that frames `spanDeg` degrees in a `widthDp` × `heightDp`
 * viewport at latitude `latitudeDeg`.
 *
 * Exists because the surface had to move off react-native-maps' controlled
 * `region` prop: on Android that compiles to `newLatLngBounds`, which resets
 * camera BEARING to zero on every update — so the map could never hold a
 * rotation while `region` was driving it. The camera API takes a zoom level
 * instead of a lat/lng span, and this is the conversion between them.
 *
 * Both axes are checked and the wider (more zoomed-out) of the two wins, which
 * is what `newLatLngBounds` did — so the framing a driver already has is
 * preserved rather than silently re-scaled. Latitude is divided by cos(φ)
 * because Mercator stretches it: at Saskatchewan's ~52° a degree of latitude
 * occupies about 1.6× the dp a degree of longitude does, and at this surface's
 * ~2.6:1 aspect that makes latitude the binding axis.
 */
export function zoomForSpan(
  spanDeg: number,
  widthDp: number,
  heightDp: number,
  latitudeDeg: number,
): number | null {
  if (!Number.isFinite(spanDeg) || spanDeg <= 0) return null;
  // Before first layout there is no viewport to frame against; the caller keeps
  // whatever zoom initialRegion established rather than guessing one.
  if (!Number.isFinite(widthDp) || !Number.isFinite(heightDp)) return null;
  if (widthDp <= 0 || heightDp <= 0) return null;

  const latRad = ((Number.isFinite(latitudeDeg) ? latitudeDeg : 0) * Math.PI) / 180;
  // Guarded so a nonsense latitude can't divide by ~0 and return Infinity.
  const cosLat = Math.max(Math.cos(latRad), 0.01);

  // 2^zoom that would make each axis exactly span `spanDeg`.
  const scaleForLng = (360 * widthDp) / (TILE_SIZE_DP * spanDeg);
  const scaleForLat = (360 * heightDp * cosLat) / (TILE_SIZE_DP * spanDeg);

  const zoom = Math.log2(Math.min(scaleForLng, scaleForLat));
  if (!Number.isFinite(zoom)) return null;
  return Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, zoom));
}

/**
 * The one-line heading readout drawn on the car surface.
 *
 * Exists because "the car is pointing the wrong way" was reported from a
 * photograph, and a photograph cannot say WHY — a bearing the GPS supplied, one
 * derived from two positions, one carried from an earlier reading, and none at
 * all all look identical on screen. The car has no console, no red box and no
 * Metro output, and the debug panel behind them is compiled out of production,
 * so a driver on a real head unit had nothing to report but the picture.
 *
 * Deliberately terse: it sits on a dashboard beside the map and has to cost the
 * driver nothing to ignore. Examples:
 *   "271° gps · course-up"      healthy — real course, map rotating
 *   "271° derived · course-up"  no GPS course; bearing from movement
 *   "271° held · north-up"      carried from an earlier reading
 *   "no course · north-up"      nothing to point with (the reported symptom)
 */
export function formatHeadingReadout(
  headingDeg: number | null | undefined,
  source: 'gps' | 'derived' | 'carried' | 'none',
  cameraHeadingDeg: number | null,
): string {
  const label =
    source === 'gps' ? 'gps' : source === 'derived' ? 'derived' : source === 'carried' ? 'held' : null;
  const bearing = normalizeHeading(headingDeg);
  const left =
    bearing === null || label === null ? 'no course' : `${Math.round(bearing)}° ${label}`;
  const right = cameraHeadingDeg === null ? 'north-up' : 'course-up';
  return `${left} · ${right}`;
}
