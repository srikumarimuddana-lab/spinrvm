import fs from 'node:fs';
import path from 'node:path';

const source = fs.readFileSync(path.join(__dirname, '..', 'app', 'ride-details.tsx'), 'utf8');

describe('ride-details v2 route rendering contract', () => {
  it('draws actual segments through the shared RouteLine/RoutePins after the map is ready', () => {
    expect(source).toContain('toReactNativeRouteSections');
    expect(source).toContain('actual_route_segments');
    expect(source).toContain('const [routeMapReady, setRouteMapReady] = useState(false)');
    expect(source).toContain('if (!routeMapReady || mapCoordinates.length < 2) return');
    expect(source).toContain('setRouteMapReady(true)');
    // Uniform shared components replace the per-section stroke polylines + pins.
    expect(source).toContain('<RouteLine path={mapCoordinates} />');
    expect(source).toContain('<RoutePins');
    expect(source).toContain('completion={ride.actual_completion_point || null}');
    expect(source).not.toContain('Polyline');
    expect(source).not.toContain('INFERRED_ROUTE_STROKE');
    expect(source).not.toContain('ACTUAL_ROUTE_STROKE');
    expect(source).not.toContain('{routeSnapshotUrl ? (');
    expect(source).toContain('ride.actual_completion_point');
    expect(source).toContain('useCompletedRouteRefresh(ride, fetchRide)');
    expect(source).not.toContain('actualSegments.flat()');
    expect(source).not.toContain('/route/v1/');
  });

  it('keeps planned geometry legacy-only and never rebuilds a completed actual route with Directions', () => {
    expect(source).toContain('const isV2Route =');
    // mapCoordinates (the RouteLine path) drops planned geometry for v2 rides.
    expect(source).toContain('isV2Route ? [] : plannedSegments');
    expect(source).toContain("const routeLabel = hasActualRoute ? 'Actual route' : isV2Route ? 'Actual route' : 'Planned route';");
    expect(source).not.toContain('decodePolyline');
    expect(source).not.toContain('fetchFallbackRoute');
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
