import fs from 'fs';
import path from 'path';

const screenSource = fs.readFileSync(
  path.resolve(__dirname, '..', 'ride-completed.tsx'),
  'utf8',
);
const rideStoreSource = fs.readFileSync(
  path.resolve(__dirname, '..', '..', 'store', 'rideStore.ts'),
  'utf8',
);

describe('completed ride route presentation contract', () => {
  it('renders each captured route segment without requesting a replacement route', () => {
    expect(screenSource).toContain('toReactNativeSegments');
    expect(screenSource).toContain('actualSegments.map((coordinates, index) => (');
    expect(screenSource).not.toContain('MapViewDirections');
    expect(screenSource).not.toContain('fallbackCoords');
  });

  it('only presents a route snapshot as actual when it matches the geometry revision', () => {
    expect(screenSource).toContain('snapshot_revision');
    expect(screenSource).toContain('isActualSnapshot');
    expect(screenSource).toContain('Route snapshot unavailable');
  });

  it('keeps planned and incomplete route evidence honestly labelled', () => {
    expect(screenSource).toContain('Planned route');
    expect(screenSource).toContain('routeQualityLabel');
    expect(rideStoreSource).toContain('actual_route_segments?:');
    expect(rideStoreSource).toContain('actual_duration_minutes?:');
  });
});
