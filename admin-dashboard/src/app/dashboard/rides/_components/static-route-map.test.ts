import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  DEFAULT_RASTER_TILE_URL,
  project,
  rasterAttribution,
  rasterTileUrl,
  rasterTileUrlTemplate,
} from './static-route-map';

const TILE = 256;
const tileOf = (lat: number, lng: number, z: number) => {
  const { x, y } = project(lat, lng, z);
  return { x: Math.floor(x / TILE), y: Math.floor(y / TILE) };
};

describe('static route map projection', () => {
  // Cross-checked against the reference OSM slippy-map formula
  // (floor((1 - asinh(tan(lat)) / PI) / 2 * 2^z)), which agrees exactly, and
  // against the live provider by tile *density* rather than status code —
  // Carto answers 200 for any valid coordinate, so a 200 proves nothing:
  //   13/1668/2701 (Saskatoon)      -> 20905 B
  //   13/1500/4000 (mid-Pacific)    ->  1718 B  (essentially empty)
  // If this drifts, every request asks for the wrong square of the planet and
  // the panel renders a plausible-looking map of nowhere — which is
  // indistinguishable from "the tile host is down" unless you read the bytes.
  it('maps Saskatoon (DEFAULT_CENTER) to the verified Carto tile at z13', () => {
    expect(tileOf(52.13, -106.67, 13)).toEqual({ x: 1668, y: 2701 });
  });

  it('puts the origin of the world at tile 0,0 and centres null island', () => {
    expect(tileOf(85.05, -179.99, 2)).toEqual({ x: 0, y: 0 });
    // At z1 the world is 2x2 tiles, so 0,0 lands exactly on the seam.
    expect(project(0, 0, 1)).toEqual({ x: TILE, y: TILE });
  });

  it('is monotonic: east increases x, north decreases y', () => {
    const west = project(52.13, -106.7, 13);
    const east = project(52.13, -106.6, 13);
    const north = project(52.2, -106.67, 13);
    const south = project(52.0, -106.67, 13);
    expect(east.x).toBeGreaterThan(west.x);
    expect(north.y).toBeLessThan(south.y);
  });

  it('doubles pixel coordinates for each zoom level', () => {
    const a = project(52.13, -106.67, 10);
    const b = project(52.13, -106.67, 11);
    expect(b.x).toBeCloseTo(a.x * 2, 6);
    expect(b.y).toBeCloseTo(a.y * 2, 6);
  });

  // Web Mercator is undefined at the poles; clamping keeps y finite so a bad
  // GPS point cannot produce NaN tile coordinates and blank the whole grid.
  it('clamps beyond the Mercator limit instead of returning Infinity', () => {
    for (const lat of [90, -90, 89.999, -89.999]) {
      const { x, y } = project(lat, 0, 13);
      expect(Number.isFinite(x)).toBe(true);
      expect(Number.isFinite(y)).toBe(true);
    }
    expect(project(90, 0, 13).y).toBeCloseTo(project(85.05112878, 0, 13).y, 6);
  });
});

// This renderer needs real rasterised PNGs — it deliberately uses no WebGL and
// no vector tiles — so self-hosting the vector basemap alone cannot feed it.
// NEXT_PUBLIC_RASTER_TILE_URL points it at deploy/tiles' raster endpoint.
describe('raster tile source', () => {
  afterEach(() => vi.unstubAllEnvs());

  it('defaults to the keyless Carto pyramid, unchanged from before', () => {
    expect(rasterTileUrlTemplate()).toBe(DEFAULT_RASTER_TILE_URL);
    expect(rasterTileUrl(13, 1668, 2701)).toBe(
      'https://basemaps.cartocdn.com/light_all/13/1668/2701.png',
    );
  });

  it('substitutes every placeholder in a configured template', () => {
    vi.stubEnv(
      'NEXT_PUBLIC_RASTER_TILE_URL',
      'https://maps.spinr.ca/styles/basemap/{z}/{x}/{y}.png',
    );
    expect(rasterTileUrl(13, 1668, 2701)).toBe(
      'https://maps.spinr.ca/styles/basemap/13/1668/2701.png',
    );
  });

  // A leftover placeholder requests one wrong URL forever, which renders an
  // empty grid — indistinguishable from a dead tile host.
  it('leaves no placeholder behind, even when one repeats', () => {
    const url = rasterTileUrl(5, 6, 7, 'https://t.example/{z}/{x}/{y}/{z}.png');
    expect(url).toBe('https://t.example/5/6/7/5.png');
    expect(url).not.toMatch(/\{[zxy]\}/);
  });

  it('ignores whitespace-only configuration rather than requesting it', () => {
    vi.stubEnv('NEXT_PUBLIC_RASTER_TILE_URL', '   ');
    expect(rasterTileUrlTemplate()).toBe(DEFAULT_RASTER_TILE_URL);
  });

  // Attribution is a licensing condition, not decoration. OSM's is always
  // required; Carto's only when Carto actually served the bytes.
  it('always credits OpenStreetMap', () => {
    expect(rasterAttribution()).toContain('OpenStreetMap');
    expect(rasterAttribution('https://maps.spinr.ca/styles/basemap/{z}/{x}/{y}.png'))
      .toContain('OpenStreetMap');
  });

  it('credits CARTO only while Carto is serving the tiles', () => {
    expect(rasterAttribution(DEFAULT_RASTER_TILE_URL)).toContain('CARTO');
    expect(rasterAttribution('https://basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png'))
      .toContain('CARTO');
    // Crediting Carto for bytes from our own tile server would be false, not
    // merely redundant.
    expect(rasterAttribution('https://maps.spinr.ca/styles/basemap/{z}/{x}/{y}.png'))
      .not.toContain('CARTO');
  });

  it('matches the Carto host regardless of casing', () => {
    // Hostnames are case-insensitive. A config typo here would UNDER-credit
    // Carto — the same licensing failure this function prevents, inverted.
    expect(rasterAttribution('https://BaseMaps.CartoCDN.com/light_all/{z}/{x}/{y}.png'))
      .toContain('CARTO');
  });

  it('does not hand Carto attribution to a lookalike host', () => {
    // The match is anchored to a dot, "//" or string start, so a host that
    // merely contains the string cannot claim someone else's credit.
    expect(rasterAttribution('https://evilcartocdn.com/light_all/{z}/{x}/{y}.png'))
      .not.toContain('CARTO');
  });
});
