// Contract A fan-out: useDriverDashboard's WS handler emits `route_finalized`
// onto routeFinalizedBus; the mounted ride-detail screen subscribes so it can
// refetch the finalized route. No subscriber => no-op.

import { emitRouteFinalized, subscribeRouteFinalized, type RouteFinalizedEvent } from '../../../utils/routeFinalizedBus';

describe('routeFinalizedBus', () => {
  it('delivers an event to a subscriber', () => {
    const received: RouteFinalizedEvent[] = [];
    const unsubscribe = subscribeRouteFinalized((e) => received.push(e));
    emitRouteFinalized({ rideId: 'r1', routeRevision: 3, routeGeometryStatus: 'complete' });
    unsubscribe();
    expect(received).toEqual([{ rideId: 'r1', routeRevision: 3, routeGeometryStatus: 'complete' }]);
  });

  it('stops delivering after unsubscribe (screen unmounted => no-op)', () => {
    const received: RouteFinalizedEvent[] = [];
    const unsubscribe = subscribeRouteFinalized((e) => received.push(e));
    unsubscribe();
    emitRouteFinalized({ rideId: 'r1', routeRevision: 3 });
    expect(received).toHaveLength(0);
  });

  it('isolates a throwing subscriber from the rest of the fan-out', () => {
    const received: RouteFinalizedEvent[] = [];
    const unsubBad = subscribeRouteFinalized(() => {
      throw new Error('boom');
    });
    const unsubGood = subscribeRouteFinalized((e) => received.push(e));
    expect(() => emitRouteFinalized({ rideId: 'r1', routeRevision: 1 })).not.toThrow();
    unsubBad();
    unsubGood();
    expect(received).toHaveLength(1);
  });
});
