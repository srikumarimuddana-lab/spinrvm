import fs from 'fs';
import path from 'path';

const screenSource = fs.readFileSync(
  path.resolve(__dirname, '..', 'app', 'ride-completed.tsx'),
  'utf8',
);
const rideStoreSource = fs.readFileSync(
  path.resolve(__dirname, '..', 'store', 'rideStore.ts'),
  'utf8',
);

describe('completed ride route presentation contract', () => {
  it('renders each captured route segment without requesting a replacement route', () => {
    expect(screenSource).toContain('toReactNativeSegments');
    expect(screenSource).toContain('actualSegments.map((coordinates, index) => (');
    expect(screenSource).toContain('useCompletedRouteRefresh(currentRide');
    expect(screenSource).not.toContain('MapViewDirections');
    expect(screenSource).not.toContain('fallbackCoords');
  });

  it('only presents a route snapshot as actual when it matches the geometry revision', () => {
    expect(screenSource).toContain('snapshot_revision');
    expect(screenSource).toContain('isActualSnapshot');
    expect(screenSource).toContain('Actual route unavailable');
  });

  it('never draws the planned route for completed v2 rides', () => {
    expect(screenSource).toContain('const isV2Route =');
    expect(screenSource).toContain('isV2Route ? [] : plannedSegments');
    expect(screenSource).toContain('{!isV2Route && !hasActualRoute && plannedSegments.map');
    expect(screenSource).not.toContain('{!hasActualRoute && plannedSegments.map');
    expect(screenSource).toContain("? 'Actual route processing'");
    expect(screenSource).toContain("? 'Actual route'");
    expect(screenSource).toContain('routeQualityLabel');
    expect(rideStoreSource).toContain('actual_route_segments?:');
    expect(rideStoreSource).toContain('actual_duration_minutes?:');
  });

  it('shows the GPS-measured distance with the billed distance as legacy fallback', () => {
    expect(screenSource).toContain(
      'currentRide?.actual_distance_km ?? currentRide?.distance_km ?? 0',
    );
  });
});
