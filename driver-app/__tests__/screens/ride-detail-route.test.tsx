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
    // The ONE sanctioned raw-Polyline use: the dashed Period-2 pickup leg
    // (server-flag-gated) as separate grey context under the trip gradient.
    expect(source.match(/<Polyline/g) ?? []).toHaveLength(1);
    expect(source).toContain('pickupLegSections.map');
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

  it('keeps planned geometry legacy-only for completed rides', () => {
    expect(source).toContain('const isV2Route =');
    // Legacy planned coords still flow into mapCoordinates (v2 rides drop them),
    // which is what feeds the shared RouteLine — the geometry source is unchanged.
    expect(source).toContain('isV2Route ? [] : plannedSegments');
    expect(source).toContain("? 'Actual route processing'");
    expect(source).toContain('routeQualityLabel');
  });
});
