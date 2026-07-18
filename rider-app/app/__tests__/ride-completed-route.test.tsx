import fs from 'fs';
import path from 'path';

const screenSource = fs.readFileSync(
  path.resolve(__dirname, '..', 'ride-completed.tsx'),
  'utf8',
);
const rideStoreSource = fs.readFileSync(
  path.resolve(__dirname, '..', '..', 'store', 'rideStore.ts'),
  'utf8',
);
const socketSource = fs.readFileSync(
  path.resolve(__dirname, '..', '..', 'hooks', 'useRiderSocket.ts'),
  'utf8',
);

describe('completed ride route presentation contract', () => {
  it('renders each captured route segment without requesting a replacement route', () => {
    expect(screenSource).toContain('toReactNativeSegments');
    expect(screenSource).toContain('actualSegments.map((coordinates, index) => (');
    expect(screenSource).not.toContain('MapViewDirections');
    expect(screenSource).not.toContain('fallbackCoords');
  });

  it('only presents a route snapshot as actual when it matches the geometry revision', () => {
    expect(screenSource).toContain('snapshot_revision');
    expect(screenSource).toContain('isActualSnapshot');
    expect(screenSource).toContain('Route snapshot unavailable');
  });

  it('keeps planned and incomplete route evidence honestly labelled', () => {
    expect(screenSource).toContain('Planned route');
    expect(screenSource).toContain('routeQualityLabel');
    expect(rideStoreSource).toContain('actual_route_segments?:');
    expect(rideStoreSource).toContain('actual_duration_minutes?:');
  });

  it('renders the canonical quality note once (no duplicated label)', () => {
    // M-H: the overlay must not prepend a separate label to a status string that
    // already begins "Actual route ...", which produced "Actual route · Actual
    // route · revision N".
    expect(screenSource).not.toContain('{routeLabel} · {routeStatus}');
    expect(screenSource).toContain('>{routeStatus}</Text>');
  });

  it('types the flattened map coordinates instead of any[]', () => {
    expect(screenSource).toContain('reduce<ReactNativeRouteCoordinate[]>');
    expect(screenSource).not.toContain('reduce<any[]>');
  });

  it('refetches on the route_finalized push via a revision-guarded store action', () => {
    // The screen reads currentRide from the store; the socket handler drives the
    // refetch through handleRouteFinalizedFromWS (revision-guarded, loop-safe).
    expect(rideStoreSource).toContain('handleRouteFinalizedFromWS');
    expect(socketSource).toContain("case 'route_finalized':");
    expect(socketSource).toContain('handleRouteFinalizedFromWS');
  });
});
