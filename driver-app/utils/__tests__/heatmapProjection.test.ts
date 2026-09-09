import { projectToScreen, unprojectFromScreen, type HeatmapRegion, type Viewport } from '../heatmapProjection';

const region: HeatmapRegion = { latitude: 52.1, longitude: -106.6, latitudeDelta: 0.02, longitudeDelta: 0.02 };
const viewport: Viewport = { width: 400, height: 800 };

describe('projectToScreen', () => {
  it('places the region center at the canvas center', () => {
    const p = projectToScreen(52.1, -106.6, region, viewport);
    expect(p.x).toBeCloseTo(200, 6);
    expect(p.y).toBeCloseTo(400, 6);
  });

  it('places the region minimum (south-west corner) at the bottom-left of the canvas', () => {
    // latMin = 52.09, lngMin = -106.61
    const p = projectToScreen(52.09, -106.61, region, viewport);
    expect(p.x).toBeCloseTo(0, 6);
    expect(p.y).toBeCloseTo(800, 6); // south = bottom = max screen y
  });

  it('places the region maximum (north-east corner) at the top-right of the canvas', () => {
    // latMax = 52.11, lngMax = -106.59
    const p = projectToScreen(52.11, -106.59, region, viewport);
    expect(p.x).toBeCloseTo(400, 6);
    expect(p.y).toBeCloseTo(0, 6); // north = top = min screen y
  });

  it('moving north decreases screen y (inverted relative to latitude)', () => {
    const south = projectToScreen(52.095, -106.6, region, viewport);
    const north = projectToScreen(52.105, -106.6, region, viewport);
    expect(north.y).toBeLessThan(south.y);
  });

  it('moving east increases screen x', () => {
    const west = projectToScreen(52.1, -106.605, region, viewport);
    const east = projectToScreen(52.1, -106.595, region, viewport);
    expect(east.x).toBeGreaterThan(west.x);
  });
});

describe('unprojectFromScreen — inverse of projectToScreen', () => {
  it.each([
    ['center', 52.1, -106.6],
    ['south-west corner', 52.09, -106.61],
    ['north-east corner', 52.11, -106.59],
    ['an arbitrary interior point', 52.103, -106.598],
  ])('round-trips %s', (_label, lat, lng) => {
    const projected = projectToScreen(lat, lng, region, viewport);
    const recovered = unprojectFromScreen(projected, region, viewport);
    expect(recovered.latitude).toBeCloseTo(lat, 9);
    expect(recovered.longitude).toBeCloseTo(lng, 9);
  });
});
