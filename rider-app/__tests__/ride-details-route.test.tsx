import fs from 'node:fs';
import path from 'node:path';

const source = fs.readFileSync(path.join(__dirname, '..', 'app', 'ride-details.tsx'), 'utf8');

describe('ride-details v2 route rendering contract', () => {
  it('draws the single uniform straight route line and shared pins after the map is ready', () => {
    expect(source).toContain('RouteLine');
    expect(source).toContain('RoutePins');
    expect(source).toContain('actual_route_segments');
    expect(source).toContain('const [routeMapReady, setRouteMapReady] = useState(false)');
    expect(source).toContain('if (!routeMapReady || mapCoordinates.length < 2) return');
    expect(source).toContain('setRouteMapReady(true)');
    // No segmented / provenance-styled polylines any more.
    expect(source).not.toContain('<Polyline');
    expect(source).not.toContain('INFERRED_ROUTE_STROKE');
    expect(source).not.toContain('ACTUAL_ROUTE_STROKE');
    expect(source).not.toContain('PLANNED_ROUTE_STROKE');
    expect(source).not.toContain('{routeSnapshotUrl ? (');
    // Completion fix is handed to the shared pins.
    expect(source).toContain('completion={ride.actual_completion_point');
    expect(source).toContain('useCompletedRouteRefresh(ride, fetchRide)');
    expect(source).not.toContain('actualSegments.flat()');
    expect(source).not.toContain('/route/v1/');
  });

  it('keeps the route-quality label derived from geometry but never draws planned segments', () => {
    expect(source).toContain('const isV2Route =');
    expect(source).toContain('toReactNativeRouteSections');
    expect(source).not.toContain('plannedSegments');
    expect(source).not.toContain('toReactNativeSegments');
    expect(source).toContain("const routeLabel = hasActualRoute ? 'Actual route' : isV2Route ? 'Actual route' : 'Planned route';");
    expect(source).not.toContain('decodePolyline');
    expect(source).not.toContain('fetchFallbackRoute');
    expect(source).not.toContain('MapViewDirections');
  });

  it('includes revisioned snapshot and incomplete-quality copy in the client PDF HTML', () => {
    expect(source).toContain('Actual route (revision ${routeRevision})');
    expect(source).toContain('Route snapshot unavailable');
    expect(source).toContain('routeQualityLabel');
  });

  it('shows the GPS-measured distance in the stats tile and labels it as measured', () => {
    expect(source).toContain('(ride.actual_distance_km ?? ride.distance_km ?? 0).toFixed(1)');
    expect(source).toContain("ride.actual_distance_km != null ? 'Distance (GPS)' : 'Distance'");
  });
});
