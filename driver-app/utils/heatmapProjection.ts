/**
 * Geo -> screen-space projection for the Skia gradient heatmap overlay
 * (HM-32, ACTION_ITEMS.md). Skia draws to an offscreen canvas positioned on
 * top of the MapView, not into the native map itself, so every cell's
 * lat/lng has to be converted into pixel coordinates relative to that
 * canvas before it can be drawn.
 *
 * Linear (equirectangular) approximation, not a true Mercator projection —
 * valid at the city-block/service-area scale this heatmap operates at (the
 * same scale useVisibleHeatmapCells' own region-viewport filter already
 * assumes), and consistent with how react-native-maps' `region` prop itself
 * describes the visible area as simple lat/lng deltas rather than a zoom
 * level + projection. Does not handle the antimeridian (+/-180 deg
 * longitude) — no Spinr service area is anywhere near it, and nothing else
 * in this heatmap pipeline (the region-viewport bounds filter) handles that
 * case either, so this stays consistent rather than solving a problem
 * nothing else here does.
 */

export interface HeatmapRegion {
  latitude: number;
  longitude: number;
  latitudeDelta: number;
  longitudeDelta: number;
}

export interface Viewport {
  width: number;
  height: number;
}

export interface ScreenPoint {
  x: number;
  y: number;
}

/**
 * Projects a lat/lng onto the pixel space of a `viewport`-sized canvas
 * showing `region`. Screen y is inverted relative to latitude (y grows
 * downward, latitude grows northward/upward).
 */
export function projectToScreen(lat: number, lng: number, region: HeatmapRegion, viewport: Viewport): ScreenPoint {
  const latMin = region.latitude - region.latitudeDelta / 2;
  const lngMin = region.longitude - region.longitudeDelta / 2;
  const xRatio = region.longitudeDelta !== 0 ? (lng - lngMin) / region.longitudeDelta : 0.5;
  const yRatio = region.latitudeDelta !== 0 ? (lat - latMin) / region.latitudeDelta : 0.5;
  return {
    x: xRatio * viewport.width,
    y: (1 - yRatio) * viewport.height,
  };
}

/**
 * The inverse of projectToScreen — recovers a lat/lng from a canvas pixel
 * coordinate. Not currently used by the renderer itself, but kept alongside
 * its forward counterpart (and covered by the same round-trip test) since a
 * projection helper with no verified inverse is the kind of thing that's
 * easy to get subtly wrong (e.g. an unnoticed sign flip on the y axis) and
 * hard to catch without one.
 */
export function unprojectFromScreen(point: ScreenPoint, region: HeatmapRegion, viewport: Viewport): { latitude: number; longitude: number } {
  const latMin = region.latitude - region.latitudeDelta / 2;
  const lngMin = region.longitude - region.longitudeDelta / 2;
  const xRatio = viewport.width !== 0 ? point.x / viewport.width : 0;
  const yRatio = viewport.height !== 0 ? point.y / viewport.height : 0;
  return {
    latitude: latMin + (1 - yRatio) * region.latitudeDelta,
    longitude: lngMin + xRatio * region.longitudeDelta,
  };
}
