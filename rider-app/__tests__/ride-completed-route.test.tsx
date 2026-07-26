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
  it('draws the single uniform straight route line and shared pins, never a road route', () => {
    // The uniform renderer: one straight pickup→dropoff gradient line + shared pins.
    expect(screenSource).toContain('RouteLine');
    expect(screenSource).toContain('RoutePins');
    expect(screenSource).toContain('const [routeMapReady, setRouteMapReady] = useState(false)');
    expect(screenSource).toContain('if (!routeMapReady || mapCoordinates.length < 2) return');
    expect(screenSource).toContain('setRouteMapReady(true)');
    // No segmented / provenance-styled polylines any more.
    expect(screenSource).not.toContain('<Polyline');
    expect(screenSource).not.toContain('INFERRED_ROUTE_STROKE');
    expect(screenSource).not.toContain('ACTUAL_ROUTE_STROKE');
    expect(screenSource).not.toContain('PLANNED_ROUTE_STROKE');
    expect(screenSource).not.toContain('{routeSnapshotUrl ? (');
    expect(screenSource).toContain('useCompletedRouteRefresh(currentRide');
    expect(screenSource).not.toContain('MapViewDirections');
    expect(screenSource).not.toContain('/route/v1/');
    expect(screenSource).not.toContain('fallbackCoords');
  });

  it('does not let a generated snapshot replace the drawn route', () => {
    expect(screenSource).not.toContain('isActualSnapshot');
    expect(screenSource).not.toContain('currentRide?.route_snapshot_url');
    expect(screenSource).toContain('Actual route unavailable');
  });

  it('keeps the route-quality label derived from geometry but never draws planned segments', () => {
    // Route geometry now feeds only the label — the drawn line is always straight.
    expect(screenSource).toContain('const isV2Route =');
    expect(screenSource).toContain('toReactNativeRouteSections');
    expect(screenSource).not.toContain('plannedSegments');
    expect(screenSource).not.toContain('toReactNativeSegments');
    expect(screenSource).toContain("? 'Actual route processing'");
    expect(screenSource).toContain("? 'Actual route'");
    expect(screenSource).toContain('routeQualityLabel');
    // Completion fix is handed to the shared pins, not a bespoke marker.
    expect(screenSource).toContain('completion={currentRide.actual_completion_point');
    expect(rideStoreSource).toContain('actual_route_segments?:');
    expect(rideStoreSource).toContain('actual_completion_point?:');
    expect(rideStoreSource).toContain('actual_duration_minutes?:');
  });

  it('shows the GPS-measured distance with the billed distance as legacy fallback', () => {
    expect(screenSource).toContain(
      'currentRide?.actual_distance_km ?? currentRide?.distance_km ?? 0',
    );
  });
});
