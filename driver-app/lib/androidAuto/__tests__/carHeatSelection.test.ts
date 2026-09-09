import { selectCarHeatCells, type CarHeatViewport } from '../carHeatSelection';
import { HEAT_BLOB_RADIUS_FACTOR } from '../../heatFalloff';
import type { HeatmapCell } from '../../../hooks/useDemandHeatmap';

const SK = { lat: 52.1332, lng: -106.67 };

function viewport(over: Partial<CarHeatViewport> = {}): CarHeatViewport {
  return {
    centerLat: SK.lat,
    centerLng: SK.lng,
    delta: 0.05, // roughly a few km of car map
    cellLat: 0.004,
    cellLng: 0.006,
    cap: 26,
    ...over,
  };
}

const cell = (lat: number, lng: number, weight: number): HeatmapCell => ({ lat, lng, weight });

describe('selectCarHeatCells', () => {
  it('keeps a weak visible cell over stronger cells kilometres away', () => {
    // The reported failure: 26 stronger cells off-screen filled the whole
    // budget, so the head unit drew nothing while demand sat in view.
    const offscreen = Array.from({ length: 26 }, (_, i) =>
      cell(SK.lat + 1 + i * 0.01, SK.lng + 1, 100),
    );
    const visible = cell(SK.lat, SK.lng, 1);
    const got = selectCarHeatCells([...offscreen, visible], viewport({ cap: 26 }));
    expect(got).toContainEqual(visible);
    expect(got).toHaveLength(1);
  });

  it('drops cells outside the viewport entirely', () => {
    expect(selectCarHeatCells([cell(SK.lat + 5, SK.lng + 5, 99)], viewport())).toEqual([]);
  });

  it('keeps a cell just outside the edge whose blob still paints inside', () => {
    // Same halo reasoning as the tile rasteriser: a shape centred just beyond
    // the edge still reaches into the visible area, so cutting at the exact
    // boundary would leave a bald strip along the rim.
    const vp = viewport();
    const justOutside = cell(
      SK.lat + vp.delta / 2 + vp.cellLat * HEAT_BLOB_RADIUS_FACTOR * 0.9,
      SK.lng,
      5,
    );
    expect(selectCarHeatCells([justOutside], vp)).toHaveLength(1);
  });

  it('drops a cell beyond the halo', () => {
    const vp = viewport();
    const wellOutside = cell(
      SK.lat + vp.delta / 2 + vp.cellLat * HEAT_BLOB_RADIUS_FACTOR * 3,
      SK.lng,
      5,
    );
    expect(selectCarHeatCells([wellOutside], vp)).toEqual([]);
  });

  it('re-selects as the camera pans', () => {
    const north = cell(SK.lat + 0.4, SK.lng, 5);
    const here = cell(SK.lat, SK.lng, 5);
    const cells = [north, here];
    expect(selectCarHeatCells(cells, viewport())).toEqual([here]);
    expect(selectCarHeatCells(cells, viewport({ centerLat: SK.lat + 0.4 }))).toEqual([north]);
  });

  it('still honours the shape budget among visible cells', () => {
    const many = Array.from({ length: 40 }, (_, i) => cell(SK.lat + i * 0.0002, SK.lng, i + 1));
    expect(selectCarHeatCells(many, viewport({ cap: 26 }))).toHaveLength(26);
  });

  it('spends the budget on the strongest visible cells', () => {
    const many = Array.from({ length: 10 }, (_, i) => cell(SK.lat + i * 0.0002, SK.lng, i + 1));
    expect(selectCarHeatCells(many, viewport({ cap: 3 })).map((c) => c.weight)).toEqual([10, 9, 8]);
  });

  it('drops non-finite cells before they reach a native shape', () => {
    const good = cell(SK.lat, SK.lng, 3);
    const cells = [
      good,
      cell(NaN, SK.lng, 3),
      cell(SK.lat, Infinity, 3),
      cell(SK.lat, SK.lng, NaN),
    ];
    expect(selectCarHeatCells(cells, viewport())).toEqual([good]);
  });

  it('does not blank the map when camera state is not ready yet', () => {
    // Blanking because the camera has not settled would be a worse failure than
    // drawing a few off-screen cells, so a degenerate viewport skips filtering.
    const cells = [cell(SK.lat, SK.lng, 3), cell(SK.lat + 5, SK.lng + 5, 9)];
    for (const bad of [0, -1, NaN, Infinity]) {
      expect(selectCarHeatCells(cells, viewport({ delta: bad }))).toHaveLength(2);
    }
    expect(selectCarHeatCells(cells, viewport({ centerLat: NaN }))).toHaveLength(2);
  });

  it('returns nothing for a zero or negative budget rather than throwing', () => {
    const cells = [cell(SK.lat, SK.lng, 3)];
    expect(selectCarHeatCells(cells, viewport({ cap: 0 }))).toEqual([]);
    expect(selectCarHeatCells(cells, viewport({ cap: -5 }))).toEqual([]);
  });

  it("does not mutate the caller's array", () => {
    const cells = [cell(SK.lat, SK.lng, 1), cell(SK.lat + 0.001, SK.lng, 9)];
    const snapshot = [...cells];
    selectCarHeatCells(cells, viewport());
    expect(cells).toEqual(snapshot);
  });
});
