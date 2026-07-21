import fs from 'fs';
import path from 'path';

const source = fs.readFileSync(
  path.resolve(__dirname, '..', '..', 'app', 'driver', 'ride-detail.tsx'),
  'utf8',
);

describe('driver ride detail route presentation contract', () => {
  it('renders durable actual route segments without a directions fallback', () => {
    expect(source).toContain('toReactNativeSegments');
    expect(source).toContain('const [routeMapReady, setRouteMapReady] = useState(false)');
    expect(source).toContain('if (!routeMapReady || mapCoordinates.length < 2) return');
    expect(source).toContain('setRouteMapReady(true)');
    expect(source).toContain('actualSegments.map((coordinates, index) => (');
    expect(source).not.toContain('{routeSnapshotUrl ? (');
    expect(source).toContain('ride.actual_completion_point');
    expect(source).toContain('useCompletedRouteRefresh(ride, loadRide)');
    expect(source).not.toContain('MapViewDirections');
    expect(source).not.toContain('fallbackCoords');
  });

  it('does not let a generated snapshot replace drawable actual geometry', () => {
    expect(source).not.toContain('isActualSnapshot');
    expect(source).not.toContain('ride?.route_snapshot_url');
    expect(source).toContain('Actual route unavailable');
  });

  it('keeps planned geometry legacy-only for completed rides', () => {
    expect(source).toContain('const isV2Route =');
    expect(source).toContain('isV2Route ? [] : plannedSegments');
    expect(source).toContain('{!isV2Route && !hasActualRoute && plannedSegments.map');
    expect(source).not.toContain('{!hasActualRoute && plannedSegments.map');
    expect(source).toContain("? 'Actual route processing'");
    expect(source).toContain('routeQualityLabel');
  });
});
