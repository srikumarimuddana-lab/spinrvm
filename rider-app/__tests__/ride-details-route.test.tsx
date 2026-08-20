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
    // The TWO sanctioned raw-Polyline uses, both dashed grey CONTEXT (never a
    // substitute for actual evidence): the Period-2 pickup leg and the
    // booked-route underlay shown when a v2 actual route is missing/incomplete
    // ("never show a missing path", owner directive 2026-08-20). Any other raw
    // Polyline is a regression to per-section stroke rendering.
    expect(source.match(/<Polyline/g) ?? []).toHaveLength(2);
    expect(source).toContain('pickupLegSections.map');
    expect(source).toContain('showPlannedUnderlay && plannedSegments.map');
    expect(source).toContain('lineDashPattern={[6, 6]}');
    expect(source).toContain("s.phase === 'trip_in_progress'");
    expect(source).not.toContain('INFERRED_ROUTE_STROKE');
    expect(source).not.toContain('ACTUAL_ROUTE_STROKE');
    expect(source).not.toContain('{routeSnapshotUrl ? (');
    expect(source).toContain('ride.actual_completion_point');
    expect(source).toContain('useCompletedRouteRefresh(ride, fetchRide)');
    expect(source).not.toContain('actualSegments.flat()');
    expect(source).not.toContain('/route/v1/');
  });

  it('keeps planned geometry out of the v2 solid line and never rebuilds an actual route with Directions', () => {
    expect(source).toContain('const isV2Route =');
    // Legacy rides still draw the planned line solid; a v2 ride NEVER routes
    // planned coords into the solid RouteLine — its planned geometry appears
    // only as the dashed underlay when the actual route is missing/incomplete.
    expect(source).toContain('const showPlannedUnderlay =');
    expect(source).toContain("(!hasActualRoute || ride?.route_geometry_status !== 'complete')");
    expect(source).toContain(') : isV2Route ? null : (');
    expect(source).toContain("? (showPlannedUnderlay ? 'Booked route' : 'Actual route')");
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
