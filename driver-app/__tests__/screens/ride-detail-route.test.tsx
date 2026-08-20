import fs from 'fs';
import path from 'path';

const source = fs.readFileSync(
  path.resolve(__dirname, '..', '..', 'app', 'driver', 'ride-detail.tsx'),
  'utf8',
);

describe('driver ride detail route presentation contract', () => {
  it('draws the real actual route through the shared RouteLine + RoutePins', () => {
    expect(source).toContain('toReactNativeRouteSections');
    expect(source).toContain('const [routeMapReady, setRouteMapReady] = useState(false)');
    expect(source).toContain('if (!routeMapReady || mapCoordinates.length < 2) return');
    expect(source).toContain('setRouteMapReady(true)');
    // The route is now drawn by the shared components, not hand-rolled Polylines
    // or bespoke marker styles — same colours + markers as every other map.
    expect(source).toContain("import { RouteLine } from '@shared/components/RouteLine'");
    expect(source).toContain("import { RoutePins } from '@shared/components/RoutePins'");
    expect(source).toContain('<RouteLine path={mapCoordinates} />');
    expect(source).toContain('completion={ride.actual_completion_point || null}');
    expect(source).not.toContain('actualSections.map((section) => (');
    expect(source).not.toContain('INFERRED_ROUTE_STROKE');
    expect(source).not.toContain('ACTUAL_ROUTE_STROKE');
    // The TWO sanctioned raw-Polyline uses, both dashed grey CONTEXT (never a
    // substitute for actual evidence): the Period-2 pickup leg and the
    // booked-route underlay shown when a v2 actual route is missing/incomplete
    // ("never show a missing path", owner directive 2026-08-20).
    expect(source.match(/<Polyline/g) ?? []).toHaveLength(2);
    expect(source).toContain('pickupLegSections.map');
    expect(source).toContain('showPlannedUnderlay && plannedSegments.map');
    expect(source).toContain('lineDashPattern={[6, 6]}');
    expect(source).not.toContain('{routeSnapshotUrl ? (');
    expect(source).toContain('ride.actual_completion_point');
    expect(source).toContain('useCompletedRouteRefresh(ride, loadRide)');
    expect(source).not.toContain('MapViewDirections');
    expect(source).not.toContain('/route/v1/');
    expect(source).not.toContain('fallbackCoords');
  });

  it('does not let a generated snapshot replace drawable actual geometry', () => {
    expect(source).not.toContain('isActualSnapshot');
    expect(source).not.toContain('ride?.route_snapshot_url');
    expect(source).toContain('Actual route unavailable');
  });

  it('keeps planned geometry out of the v2 solid line — dashed underlay only', () => {
    expect(source).toContain('const isV2Route =');
    // Legacy rides still draw the planned line solid; a v2 ride NEVER routes
    // planned coords into the solid RouteLine — its planned geometry appears
    // only as the dashed underlay when the actual route is missing/incomplete.
    expect(source).toContain('const showPlannedUnderlay =');
    expect(source).toContain("(!hasActualRoute || ride?.route_geometry_status !== 'complete')");
    expect(source).toContain(') : isV2Route ? null : (');
    expect(source).toContain("? (showPlannedUnderlay ? 'Booked route' : 'Actual route')");
    expect(source).toContain("? 'Actual route processing'");
    expect(source).toContain('routeQualityLabel');
  });
});
