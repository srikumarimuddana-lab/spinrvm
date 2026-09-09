/**
 * Which demand cells the car surface should draw.
 *
 * The head unit subscribes to the phone's poller through
 * `useDemandHeatmapView()`, which publishes the whole service-area payload with
 * no viewport filtering of its own. The car then had to fit that into a bounded
 * shape budget, and it did so by sorting on weight and truncating — with no
 * reference to what the car map is actually showing.
 *
 * That is wrong in a way that gets worse as the budget shrinks: with the cap at
 * 26 and 26 stronger cells several kilometres off-screen, a weaker cell sitting
 * under the driver's nose is discarded and the map renders empty while demand is
 * visibly in view. Panning could not rescue it either, because camera state was
 * not among the memo's dependencies.
 *
 * So: filter to what can actually paint, THEN spend the budget on it. The budget
 * itself is unchanged — it is a real constraint on a weak GPU — it is just spent
 * on cells the driver can see.
 */

import { HEAT_BLOB_RADIUS_FACTOR } from '../heatFalloff';
import type { HeatmapCell } from '../../hooks/useDemandHeatmap';

export interface CarHeatViewport {
  centerLat: number;
  centerLng: number;
  /** Square span of the car map in degrees; the surface uses one delta for both axes. */
  delta: number;
  /** Server grid size, so the overlap margin matches the shape actually drawn. */
  cellLat: number;
  cellLng: number;
  /** Shape budget: how many cells may survive. */
  cap: number;
}

/**
 * Cells worth drawing on the car map, strongest first, capped.
 *
 * A cell centred just outside the viewport still paints inside it, so the window
 * is widened by the blob's own reach before anything is dropped — the same halo
 * the tile rasteriser uses to keep its seams continuous.
 *
 * If camera state is not usable yet (zero, negative or non-finite delta) the
 * viewport filter is skipped entirely rather than applied to a degenerate box.
 * Blanking a driver's map because the camera has not settled would be a worse
 * failure than drawing a few off-screen cells.
 */
export function selectCarHeatCells(
  cells: readonly HeatmapCell[],
  viewport: CarHeatViewport,
): HeatmapCell[] {
  const { centerLat, centerLng, delta, cellLat, cellLng, cap } = viewport;

  const finite = cells.filter(
    (c) => Number.isFinite(c.lat) && Number.isFinite(c.lng) && Number.isFinite(c.weight),
  );

  const cameraReady =
    Number.isFinite(delta) &&
    delta > 0 &&
    Number.isFinite(centerLat) &&
    Number.isFinite(centerLng);

  let visible = finite;
  if (cameraReady) {
    const halfLat = delta / 2 + Math.abs(cellLat) * HEAT_BLOB_RADIUS_FACTOR;
    const halfLng = delta / 2 + Math.abs(cellLng) * HEAT_BLOB_RADIUS_FACTOR;
    visible = finite.filter(
      (c) => Math.abs(c.lat - centerLat) <= halfLat && Math.abs(c.lng - centerLng) <= halfLng,
    );
  }

  return [...visible].sort((a, b) => b.weight - a.weight).slice(0, Math.max(0, cap));
}
