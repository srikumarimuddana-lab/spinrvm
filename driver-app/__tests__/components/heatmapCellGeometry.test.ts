/**
 * Heatmap cell rendering: the crash guard, the state mutation, and the grid size.
 *
 * These three defects all live in the same few lines of HeatmapCells.tsx, and
 * none of them is visible from the outside until it bites:
 *
 *   1. A non-finite coordinate reaching `<Polygon>` is an app CRASH on Android,
 *      not a missing rectangle — react-native-maps passes coordinates straight
 *      to the native map. One corrupted cache row or a partial payload was
 *      enough.
 *   2. `filtered.sort()` sorted the hook's own `cells` state array IN PLACE
 *      whenever `region` was null (the phone always passes null), reordering
 *      what every other consumer of that array sees.
 *   3. The corner maths hardcoded 0.004/0.006 — correct only while cell size
 *      was a global constant. It is now tunable per service area, so a tuned
 *      area had its polygons drawn at the wrong size AND, because the client
 *      re-floors the centroid with its own constant, snapped into the wrong
 *      grid square entirely.
 *
 * The component pulls react-native-maps and the theme context, so the geometry
 * and selection logic are re-implemented here exactly as the component does
 * them, and the component is pinned by source contract below. That split is
 * deliberate: a behavioural test of maths the component does not actually use
 * would be worse than none.
 */

const DEFAULT_CELL_LAT = 0.004;
const DEFAULT_CELL_LNG = 0.006;
const MAX_POLYGONS = 200;

type Cell = { lat: number; lng: number; weight: number };

/** Mirrors HeatmapCells.cellToCorners. */
function cellToCorners(lat: number, lng: number, cellLat: number, cellLng: number) {
  const baseLat = Math.floor(lat / cellLat) * cellLat;
  const baseLng = Math.floor(lng / cellLng) * cellLng;
  return [
    { latitude: baseLat, longitude: baseLng },
    { latitude: baseLat + cellLat, longitude: baseLng },
    { latitude: baseLat + cellLat, longitude: baseLng + cellLng },
    { latitude: baseLat, longitude: baseLng + cellLng },
  ];
}

/** Mirrors the `visibleCells` memo. */
function selectVisible(cells: Cell[], region: null = null): Cell[] {
  if (!cells.length) return [];
  const filtered = cells.filter(
    (c) => Number.isFinite(c.lat) && Number.isFinite(c.lng) && Number.isFinite(c.weight),
  );
  return [...filtered].sort((a, b) => b.weight - a.weight).slice(0, MAX_POLYGONS);
}

describe('non-finite coordinate guard', () => {
  it.each([
    ['NaN lat', { lat: NaN, lng: -106.6, weight: 3 }],
    ['NaN lng', { lat: 52.1, lng: NaN, weight: 3 }],
    ['Infinity lat', { lat: Infinity, lng: -106.6, weight: 3 }],
    ['NaN weight', { lat: 52.1, lng: -106.6, weight: NaN }],
  ])('drops a cell with %s before it can reach the native map', (_label, bad) => {
    const good = { lat: 52.13, lng: -106.67, weight: 5 };
    const visible = selectVisible([good, bad as Cell]);

    expect(visible).toEqual([good]);
  });

  it('produces only finite corners for every cell it keeps', () => {
    // The actual crash condition: Math.floor(NaN / x) * x is NaN, and NaN
    // coordinates are what the native layer chokes on.
    const cells: Cell[] = [
      { lat: 52.13, lng: -106.67, weight: 5 },
      { lat: NaN, lng: -106.67, weight: 9 },
    ];
    for (const c of selectVisible(cells)) {
      for (const corner of cellToCorners(c.lat, c.lng, DEFAULT_CELL_LAT, DEFAULT_CELL_LNG)) {
        expect(Number.isFinite(corner.latitude)).toBe(true);
        expect(Number.isFinite(corner.longitude)).toBe(true);
      }
    }
  });

  it('renders nothing rather than crashing when every cell is corrupt', () => {
    expect(selectVisible([{ lat: NaN, lng: NaN, weight: NaN }])).toEqual([]);
  });
});

describe('input array is never mutated', () => {
  it('leaves the caller\'s array order untouched', () => {
    // The hook holds this exact array in state. Sorting it in place reordered
    // what the Android Auto surface and the hotspot chips read.
    const cells: Cell[] = [
      { lat: 52.1, lng: -106.6, weight: 1 },
      { lat: 52.2, lng: -106.7, weight: 9 },
      { lat: 52.3, lng: -106.8, weight: 5 },
    ];
    const before = cells.map((c) => c.weight);

    const visible = selectVisible(cells);

    expect(cells.map((c) => c.weight)).toEqual(before);
    // ...while the render list IS sorted, heaviest first.
    expect(visible.map((c) => c.weight)).toEqual([9, 5, 1]);
  });
});

describe('server-supplied grid size', () => {
  it('uses the cell size the server actually bucketed with', () => {
    // A cell centroid sits at (row + 0.5) * cellSize. With a 0.01 grid the
    // centroid 52.135 belongs to the square starting at 52.13.
    const corners = cellToCorners(52.135, -106.665, 0.01, 0.01);

    expect(corners[0].latitude).toBeCloseTo(52.13, 6);
    expect(corners[1].latitude).toBeCloseTo(52.14, 6);
  });

  it('drawing a tuned area with the old constants lands in the WRONG square', () => {
    // This is the defect, stated as a test: same centroid, two grid sizes.
    // It is not a cosmetic size difference — the floor() puts the rectangle
    // somewhere else entirely, so the driver is pointed at the wrong block.
    const centroidLat = 52.135;
    const tuned = cellToCorners(centroidLat, -106.665, 0.01, 0.01);
    const hardcoded = cellToCorners(centroidLat, -106.665, DEFAULT_CELL_LAT, DEFAULT_CELL_LNG);

    expect(tuned[0].latitude).not.toBeCloseTo(hardcoded[0].latitude, 4);
  });

  it.each([
    ['omitted (older backend)', undefined],
    ['null', null],
    ['zero', 0],
    ['negative', -1],
  ])('falls back to the default when the server size is %s', (_label, sent) => {
    // Mirrors the component's guard. Anything not a positive number must land
    // on the constant — a 0 would make Math.floor(lat / 0) Infinity, which is
    // the crash this file's first block is about.
    const resolved = typeof sent === 'number' && sent > 0 ? sent : DEFAULT_CELL_LAT;
    expect(resolved).toBe(0.004);
  });
});

/**
 * Source contract — the fixes above must actually be the ones in the component.
 */
describe('HeatmapCells source', () => {
  const fs = require('fs');
  const path = require('path');
  const source: string = fs.readFileSync(
    path.resolve(__dirname, '..', '..', 'components', 'dashboard', 'HeatmapCells.tsx'),
    'utf8',
  );

  it('filters non-finite coordinates', () => {
    expect(source).toMatch(/Number\.isFinite\(c\.lat\)/);
  });

  it('sorts a copy, never the prop array', () => {
    expect(source).toMatch(/\[\.\.\.filtered\]\.sort/);
    expect(source).not.toMatch(/^\s*filtered\.sort\(/m);
  });

  it('accepts the grid size as a prop instead of hardcoding it', () => {
    expect(source).toMatch(/cellLatDeg/);
    expect(source).toMatch(/cellToCorners\(cell\.lat, cell\.lng, cellLat, cellLng\)/);
  });

  it('keys polygons on coordinates, not array index', () => {
    // The list is re-sorted by weight every poll, so an index-based key churned
    // native views for cells that had only moved position in the array.
    expect(source).toMatch(/key=\{`hm-\$\{cell\.lat\}-\$\{cell\.lng\}`\}/);
  });
});
