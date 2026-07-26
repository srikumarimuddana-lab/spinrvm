import fs from 'fs';
import path from 'path';

const source = fs.readFileSync(
  path.resolve(__dirname, '..', '..', 'app', 'driver', 'ride-detail.tsx'),
  'utf8',
);

describe('driver ride detail route presentation contract', () => {
  it('draws the shared uniform straight route line + pins (no directions, no segmented polylines)', () => {
    // The uniform renderers replace every segmented/planned Polyline + the
    // hand-rolled pickup/dropoff/completion markers.
    expect(source).toContain("import RouteLine from '@shared/components/RouteLine'");
    expect(source).toContain("import RoutePins from '@shared/components/RoutePins'");
    expect(source).toContain('<RouteLine');
    expect(source).toContain('<RoutePins');
    expect(source).toContain('completion={ride.actual_completion_point || null}');
    // The old provenance-specific strokes + per-segment polylines are gone.
    expect(source).not.toContain('INFERRED_ROUTE_STROKE');
    expect(source).not.toContain('ACTUAL_ROUTE_STROKE');
    expect(source).not.toContain('PLANNED_ROUTE_STROKE');
    expect(source).not.toContain('actualSections.map((section) => (');
    expect(source).not.toContain('plannedSegments.map');
    // Still never a live directions fallback.
    expect(source).not.toContain('MapViewDirections');
    expect(source).not.toContain('/route/v1/');
    expect(source).not.toContain('fallbackCoords');
    expect(source).toContain('useCompletedRouteRefresh(ride, loadRide)');
  });

  it('does not let a generated snapshot replace the drawn route', () => {
    expect(source).not.toContain('isActualSnapshot');
    expect(source).not.toContain('ride?.route_snapshot_url');
  });

  it('still derives route-quality metadata for the status pill', () => {
    expect(source).toContain('const isV2Route =');
    expect(source).toContain('routeQualityLabel');
    expect(source).toContain("? 'Actual route processing'");
    expect(source).toContain('Actual route unavailable');
  });
});
