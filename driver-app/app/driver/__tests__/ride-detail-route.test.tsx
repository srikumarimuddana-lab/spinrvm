import fs from 'fs';
import path from 'path';

const source = fs.readFileSync(
  path.resolve(__dirname, '..', 'ride-detail.tsx'),
  'utf8',
);
const dashboardSource = fs.readFileSync(
  path.resolve(__dirname, '..', '..', '..', 'hooks', 'useDriverDashboard.ts'),
  'utf8',
);

describe('driver ride detail route presentation contract', () => {
  it('renders durable actual route segments without a directions fallback', () => {
    expect(source).toContain('toReactNativeSegments');
    expect(source).toContain('actualSegments.map((coordinates, index) => (');
    expect(source).not.toContain('MapViewDirections');
    expect(source).not.toContain('fallbackCoords');
  });

  it('does not present an outdated snapshot as the current actual route', () => {
    expect(source).toContain('snapshot_revision');
    expect(source).toContain('isActualSnapshot');
    expect(source).toContain('Route snapshot unavailable');
  });

  it('keeps a no-GPS route visibly planned or incomplete', () => {
    expect(source).toContain('Planned route');
    expect(source).toContain('routeQualityLabel');
  });

  it('renders the canonical quality note once (no duplicated label)', () => {
    // M-H: the pill must not prepend routeLabel to a status that already starts
    // "Actual route ..." — that produced "Actual route · Actual route · revision N".
    expect(source).not.toContain('{routeLabel} · {routeStatus}');
    expect(source).toContain('>{routeStatus}</Text>');
    expect(source).toContain('reduce<ReactNativeRouteCoordinate[]>');
    expect(source).not.toContain('reduce<any[]>');
  });

  it('refetches on the route_finalized push (revision-guarded, no-op if unmounted)', () => {
    // Dashboard WS handler fans the push out on routeFinalizedBus; the screen
    // subscribes and refetches only when the pushed revision is newer.
    expect(dashboardSource).toContain("case 'route_finalized':");
    expect(dashboardSource).toContain('emitRouteFinalized');
    expect(source).toContain('subscribeRouteFinalized');
    expect(source).toContain('event.routeRevision > rideRevisionRef.current');
  });
});
