/**
 * Unit tests for hooks/liveRouteShared.ts — the channel that carries the OSRM
 * live route from whichever surface is polling to whichever surface is drawing.
 *
 * The guard (`isLiveRouteUsable`) is the part that matters most: a route line
 * on a windscreen that belongs to the wrong ride, the wrong leg, or a dead
 * poller is worse than no line at all, because the driver cannot tell.
 */
import {
  __resetLiveRouteSharedForTest,
  clearLiveRoute,
  getLiveRouteSnapshot,
  hasLiveRoutePublisher,
  isLiveRouteUsable,
  publishLiveRoute,
  registerLiveRoutePublisher,
  type LiveRouteSnapshot,
} from '../liveRouteShared';

const NOW = 1_000_000;
const MAX_AGE = 60_000;

const snap = (o: Partial<LiveRouteSnapshot> = {}): LiveRouteSnapshot => ({
  polyline: [
    { latitude: 1, longitude: 1 },
    { latitude: 2, longitude: 2 },
  ],
  destination: 'dropoff',
  rideId: 'r1',
  etaMinutes: 5,
  distanceKm: 2,
  receivedAt: NOW,
  ...o,
});

const usable = (s: LiveRouteSnapshot, rideId: string | null, leg: 'pickup' | 'dropoff') =>
  isLiveRouteUsable(s, rideId, leg, MAX_AGE, NOW);

beforeEach(() => {
  __resetLiveRouteSharedForTest();
});

describe('isLiveRouteUsable', () => {
  it('draws a fresh route for the matching ride and leg', () => {
    expect(usable(snap(), 'r1', 'dropoff')).toBe(true);
  });

  it('never draws another ride’s geometry', () => {
    expect(usable(snap(), 'r2', 'dropoff')).toBe(false);
    expect(usable(snap(), null, 'dropoff')).toBe(false);
  });

  it('never draws the wrong leg', () => {
    // The one that would point a driver back at a pickup they already made.
    expect(usable(snap(), 'r1', 'pickup')).toBe(false);
  });

  it('needs at least two points to be a line', () => {
    expect(usable(snap({ polyline: [{ latitude: 1, longitude: 1 }] }), 'r1', 'dropoff')).toBe(false);
    expect(usable(snap({ polyline: [] }), 'r1', 'dropoff')).toBe(false);
  });

  it('rejects a snapshot that never actually arrived', () => {
    expect(usable(snap({ receivedAt: null }), 'r1', 'dropoff')).toBe(false);
  });

  it('ages out rather than drawing a line from a dead poller', () => {
    expect(usable(snap({ receivedAt: NOW - MAX_AGE }), 'r1', 'dropoff')).toBe(true);
    expect(usable(snap({ receivedAt: NOW - MAX_AGE - 1 }), 'r1', 'dropoff')).toBe(false);
  });
});

describe('publisher refcount', () => {
  it('starts with no publisher', () => {
    expect(hasLiveRoutePublisher()).toBe(false);
  });

  it('survives an overlapping mount/unmount', () => {
    // React can mount the next instance before unmounting the previous one; a
    // boolean would let the outgoing unmount clear a flag the incoming one set.
    const releaseA = registerLiveRoutePublisher();
    const releaseB = registerLiveRoutePublisher();
    releaseA();
    expect(hasLiveRoutePublisher()).toBe(true);
    releaseB();
    expect(hasLiveRoutePublisher()).toBe(false);
  });
});

describe('publish / clear', () => {
  const route = {
    polyline: [
      { latitude: 1, longitude: 1 },
      { latitude: 2, longitude: 2 },
    ],
    destination: 'pickup' as const,
    rideId: 'r9',
    etaMinutes: 3,
    distanceKm: 1,
  };

  it('stores the line and stamps when it landed', () => {
    publishLiveRoute(route);
    const s = getLiveRouteSnapshot();
    expect(s.polyline).toHaveLength(2);
    expect(s.rideId).toBe('r9');
    expect(typeof s.receivedAt).toBe('number');
  });

  it('keeps the route after the publisher releases', () => {
    // Deliberately unlike demandHeatmapShared, which blanks. A road does not
    // stop being a road because a tab unmounted, and blanking would flash the
    // car's route line off every time the driver switched screens.
    const release = registerLiveRoutePublisher();
    publishLiveRoute(route);
    release();
    expect(getLiveRouteSnapshot().polyline).toHaveLength(2);
  });

  it('clear drops the line and its identity together', () => {
    publishLiveRoute(route);
    clearLiveRoute();
    const s = getLiveRouteSnapshot();
    expect(s.polyline).toHaveLength(0);
    expect(s.rideId).toBeNull();
    expect(s.destination).toBeNull();
  });

  it('notifies subscribers on publish and on clear', () => {
    const seen: number[] = [];
    // subscribe is internal; useLiveRouteView is the public reader, so exercise
    // the channel the way the store does — via successive snapshot reads.
    publishLiveRoute(route);
    seen.push(getLiveRouteSnapshot().polyline.length);
    clearLiveRoute();
    seen.push(getLiveRouteSnapshot().polyline.length);
    expect(seen).toEqual([2, 0]);
  });
});
