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
  it('draws the captured route through the shared RouteLine/RoutePins without requesting a replacement route', () => {
    // Real geometry is still reconstructed from the captured segments…
    expect(screenSource).toContain('toReactNativeRouteSections');
    expect(screenSource).toContain('const [routeMapReady, setRouteMapReady] = useState(false)');
    expect(screenSource).toContain('if (!routeMapReady || mapCoordinates.length < 2) return');
    expect(screenSource).toContain('setRouteMapReady(true)');
    // …then handed to the uniform shared components rather than hand-rolled
    // gradient polylines / bespoke markers.
    expect(screenSource).toContain('<RouteLine path={mapCoordinates} />');
    expect(screenSource).toContain('<RoutePins');
    expect(screenSource).toContain('completion={currentRide.actual_completion_point || null}');
    // The ONE sanctioned raw-Polyline use: the dashed Period-2 pickup leg
    // (server-flag-gated) as separate grey context under the trip gradient.
    expect(screenSource.match(/<Polyline/g) ?? []).toHaveLength(1);
    expect(screenSource).toContain('pickupLegSections.map');
    expect(screenSource).toContain('lineDashPattern={[6, 6]}');
    expect(screenSource).not.toContain('INFERRED_ROUTE_STROKE');
    expect(screenSource).not.toContain('ACTUAL_ROUTE_STROKE');
    expect(screenSource).not.toContain('{routeSnapshotUrl ? (');
    expect(screenSource).toContain('useCompletedRouteRefresh(currentRide');
    expect(screenSource).not.toContain('MapViewDirections');
    expect(screenSource).not.toContain('/route/v1/');
    expect(screenSource).not.toContain('fallbackCoords');
  });

  it('does not let a generated snapshot replace drawable actual geometry', () => {
    expect(screenSource).not.toContain('isActualSnapshot');
    expect(screenSource).not.toContain('currentRide?.route_snapshot_url');
    expect(screenSource).toContain('Actual route unavailable');
  });

  it('never feeds the planned route into RouteLine for completed v2 rides', () => {
    expect(screenSource).toContain('const isV2Route =');
    // mapCoordinates (the RouteLine path) suppresses planned geometry for v2.
    expect(screenSource).toContain('isV2Route ? [] : plannedSegments');
    expect(screenSource).toContain("? 'Actual route processing'");
    expect(screenSource).toContain("? 'Actual route'");
    expect(screenSource).toContain('routeQualityLabel');
    expect(screenSource).toContain('currentRide.actual_completion_point');
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
